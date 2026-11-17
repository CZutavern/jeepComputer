"""
Engine RPM from the ignition coil's negative (tach) terminal.

The coil signal goes through the opto-isolated input in docs/tach-circuit.md
(PC817 optocoupler), whose output pulls the GPIO low once per
spark. A 4-stroke engine fires cylinders / 2 times per revolution, so

    RPM = sparks per second * 120 / cylinders

Settings (~/.config/jeep_computer/tach.json):
    pin        BCM GPIO of the optocoupler output (default 12 = header pin 32).
               Taken: 4 IMU reset, 5 DAC8x ADC-detect, 6 and 16 the X1209 UPS
               (16 HIGH disables charging, which cuts the Pi's power when no
               batteries are fitted), 17 amp remote, 18-27 DAC8x
    cylinders  8 for the AMC 360 / 401 V8, 6 for the 258 straight six
    gauge_max  top of the dashboard RPM dial (default 6000)
    redline    where the dial's warning band starts (default 4500, the AMC V8)
"""

import json
import statistics
import threading
import time
from collections import deque
from datetime import timedelta
from pathlib import Path

try:
    import gpiod             # libgpiod v2 (lgpio's edge alerts fail on this kernel)
    from gpiod.line import Bias, Direction, Edge
except ImportError:          # development machines
    gpiod = None

CHIP = "/dev/gpiochip0"      # RP1 header GPIOs on the Pi 5

SETTINGS = Path.home() / ".config/jeep_computer/tach.json"
DEFAULTS = {"pin": 12, "cylinders": 8, "gauge_max": 6000, "redline": 4500}

# Never claim these: the X1209 UPS reads GPIO 6 and 16, and a pull-up on
# 16 switches its charger off. 10 is the ACC-sense input (sensors/acc.py)
RESERVED_PINS = {4, 5, 6, 10, 16, 17} | set(range(18, 28))

LOCKOUT_NS = 1_000_000       # ignore edges < 1 ms apart (points bounce, coil ringing)
DEBOUNCE_US = 0              # kernel debounce OFF: with 100 us the RP1 delivered no edges at all on the running
                             # engine's clean 36 Hz signal (measured 2026-10-15: 0 events vs 74 without it).
                             # Bounce/ringing is handled by LOCKOUT_NS below.
STOPPED_S = 1.0              # no spark for this long = engine stopped
WATCHDOG_S = 2.0             # line is changing but no edge events for this long = reader stuck, re-open it
PERIODS = 8                  # median over the last N spark intervals


def load_settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def gauge_scale(settings=None):
    """(gauge_max, redline) for the RPM dial; bad values fall back to the defaults."""
    s = settings if settings is not None else load_settings()
    try:
        top, red = float(s["gauge_max"]), float(s["redline"])
    except (KeyError, TypeError, ValueError):
        top, red = float(DEFAULTS["gauge_max"]), float(DEFAULTS["redline"])
    if not (top > 0 and 0 < red < top):
        top, red = float(DEFAULTS["gauge_max"]), float(DEFAULTS["redline"])
    return top, red


class Tach:

    def __init__(self, settings=None):
        self.settings = settings or load_settings()
        self.cylinders = int(self.settings["cylinders"])
        self.pin = int(self.settings["pin"])
        if self.pin in RESERVED_PINS:
            raise RuntimeError(f"GPIO {self.pin} is reserved for other hardware")

        self.lock = threading.Lock()
        self.periods = deque(maxlen=PERIODS)
        self.last_tick = None          # ns, kernel event timestamp
        self.last_spark = None         # time.monotonic()
        self.sparks = 0
        self.restarts = 0              # watchdog re-opens of the GPIO line

        self.request = None
        self.stopping = threading.Event()
        if gpiod is None:
            raise RuntimeError("gpiod not available")

        # Pull-up keeps an unconnected input quiet (no phantom RPM)
        self._open()
        self.thread = threading.Thread(target=self._watch, name="tach", daemon=True)
        self.thread.start()

    def _open(self):
        extra = {"debounce_period": timedelta(microseconds=DEBOUNCE_US)} if DEBOUNCE_US else {}
        self.request = gpiod.request_lines(CHIP, consumer="jeep-tach", config={
            self.pin: gpiod.LineSettings(
                direction=Direction.INPUT,
                edge_detection=Edge.FALLING,
                bias=Bias.PULL_UP,
                **extra,
            )
        })

    def _reopen(self, why):
        """Release and re-request the line, so a reader that stopped getting events starts again."""
        print(f"tach: re-opening GPIO {self.pin} ({why})", flush=True)
        self.restarts += 1
        try:
            self.request.release()
        except Exception:
            pass
        for _ in range(5):
            try:
                self._open()
                return
            except Exception as e:
                print("tach: re-open failed:", e, flush=True)
                time.sleep(1)

    def _watch(self):
        last_event = time.monotonic()       # last time an edge event arrived
        last_level = None
        changed = 0.0                       # last time the line level was seen to change
        while not self.stopping.is_set():
            try:
                if self.request.wait_edge_events(timedelta(milliseconds=200)):
                    events = self.request.read_edge_events()
                    for event in events:
                        self._edge(event.timestamp_ns)
                    if events:
                        last_event = time.monotonic()
                # Watchdog: the pin is toggling but no events are arriving. The reader is stuck
                # (seen 2026-10-15: counted while cranking, then stayed at 0 with the engine running).
                level = self.request.get_value(self.pin)
                now = time.monotonic()
                if last_level is not None and level != last_level:
                    changed = now
                last_level = level
                if now - changed < 0.5 and now - last_event > WATCHDOG_S:
                    self._reopen("pin is toggling but no edge events")
                    last_event = changed = time.monotonic()
            except Exception as e:
                if not self.stopping.is_set():
                    print("tach: read error:", e, flush=True)
                    time.sleep(1)
                    self._reopen("read error")

    def _edge(self, tick):
        with self.lock:
            if self.last_tick is not None:
                period = tick - self.last_tick
                if period < LOCKOUT_NS:
                    return
                # A gap longer than "stopped" isn't a running period
                if period < STOPPED_S * 1e9:
                    self.periods.append(period)
            self.last_tick = tick
            self.last_spark = time.monotonic()
            self.sparks += 1

    def rpm(self):
        """RPM, 0 when stopped, None if no spark has ever been seen (not wired)."""
        with self.lock:
            if self.last_spark is None:
                return None
            if time.monotonic() - self.last_spark > STOPPED_S or len(self.periods) < 3:
                return 0
            period_s = statistics.median(self.periods) / 1e9
        return 120.0 / (self.cylinders * period_s)

    def read(self):
        rpm = self.rpm()
        return {"rpm": None if rpm is None else round(rpm), "sparks": self.sparks, "restarts": self.restarts}

    def close(self):
        self.stopping.set()
        if self.request is not None:
            self.thread.join(timeout=2)
            self.request.release()
            self.request = None
