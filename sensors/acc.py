"""
Ignition-key state (Accessory or On) from the opto-isolated ACC-sense input.

The circuit in docs/circuits/README.md (PC817, 10k pull-up to 3.3 V) pulls
the GPIO LOW while the key is in Accessory or On and leaves it HIGH with the
key off. Cranking makes the ACC feed dip for a moment, so the state is
filtered:

    key_on turns True once the line has been LOW for ON_AFTER_S (0.3 s)
    key_on turns False only after the line has been HIGH for OFF_AFTER_S (2 s)

so dips shorter than 2 s while the engine starts do not count as key off.

read() returns {"key_on", "raw", "since_s"}: raw is the unfiltered key state
(True = line LOW), since_s the seconds since key_on last changed.

Settings (~/.config/jeep_computer/acc.json):
    pin         BCM GPIO of the optocoupler output (default 10 = header pin 19;
                the boot overlay nospi10 keeps SPI off it)
    active_low  true: LOW = key on (the PC817 circuit)

Only one process can hold the GPIO line. If the UPS monitor owns it
(ups.json "vehicle_signal": "acc"), the monitor publishes the state to
/run/jeep-ups/acc.json and open_acc() gives the dashboard a reader of that
file instead, so both always see the same state.
"""

import json
import threading
import time
from datetime import timedelta
from pathlib import Path

from .tach import CHIP, RESERVED_PINS, gpiod

if gpiod is not None:
    from gpiod.line import Bias, Direction, Value

SETTINGS = Path.home() / ".config/jeep_computer/acc.json"
DEFAULTS = {"pin": 10, "active_low": True}
SHARED_FILE = Path("/run/jeep-ups/acc.json")      # written by ups_monitor.py when it owns the line
SHARED_MAX_AGE_S = 5.0

ON_AFTER_S = 0.3
OFF_AFTER_S = 2.0
POLL_S = 0.02


def load_settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


class AccFilter:
    """The stability filter with no hardware: feed it key levels and times."""

    def __init__(self, now=0.0, on_after_s=ON_AFTER_S, off_after_s=OFF_AFTER_S):
        self.on_after_s = on_after_s
        self.off_after_s = off_after_s
        self.key_on = False
        self.raw = False
        self.since = now            # when key_on last changed
        self.raw_since = now        # when raw last changed

    def update(self, raw, now):
        """raw: True while the line says key on (LOW). Returns the filtered key_on."""
        if raw != self.raw:
            self.raw = raw
            self.raw_since = now
        held = now - self.raw_since
        if not self.key_on and self.raw and held >= self.on_after_s:
            self.key_on, self.since = True, now
        elif self.key_on and not self.raw and held >= self.off_after_s:
            self.key_on, self.since = False, now
        return self.key_on

    def read(self, now):
        return {"key_on": self.key_on, "raw": self.raw, "since_s": round(max(0.0, now - self.since), 1)}


class AccReader:

    def __init__(self, settings=None):
        self.settings = settings or load_settings()
        self.pin = int(self.settings["pin"])
        self.active_low = bool(self.settings["active_low"])
        # Everything the tach refuses, except this pin (the tach reserves it for us)
        if self.pin in RESERVED_PINS - {DEFAULTS["pin"]}:
            raise RuntimeError(f"GPIO {self.pin} is reserved for other hardware")
        if gpiod is None:
            raise RuntimeError("gpiod not available")

        self.lock = threading.Lock()
        self.filter = AccFilter(time.monotonic())
        self.stopping = threading.Event()
        self.last_error = None

        self.request = gpiod.request_lines(CHIP, consumer="jeep-acc", config={
            self.pin: gpiod.LineSettings(
                direction=Direction.INPUT,
                bias=Bias.PULL_UP,
                debounce_period=timedelta(milliseconds=5),
            )
        })
        self._sample()
        self.thread = threading.Thread(target=self._watch, name="acc", daemon=True)
        self.thread.start()

    def _sample(self):
        low = self.request.get_value(self.pin) == Value.INACTIVE
        raw = low if self.active_low else not low
        with self.lock:
            self.filter.update(raw, time.monotonic())

    def _watch(self):
        while not self.stopping.is_set():
            try:
                self._sample()
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                time.sleep(1)
            time.sleep(POLL_S)

    def read(self):
        if self.last_error:
            raise OSError(self.last_error)
        with self.lock:
            return self.filter.read(time.monotonic())

    def close(self):
        self.stopping.set()
        if self.request is not None:
            self.thread.join(timeout=2)
            self.request.release()
            self.request = None


class SharedAccReader:
    """The state the UPS monitor publishes, for when it holds the GPIO line."""

    def __init__(self, path=SHARED_FILE):
        self.path = Path(path)

    def read(self):
        state = json.loads(self.path.read_text())
        if time.time() - state["time"] > SHARED_MAX_AGE_S:
            raise OSError("ACC state from the UPS monitor is stale")
        return {k: state[k] for k in ("key_on", "raw", "since_s")}

    def close(self):
        pass


def pick_vehicle_power(signal, acc_key_on, pld_high):
    """The UPS monitor's "vehicle power present" answer.

    signal       ups.json "vehicle_signal": "ups" (the X1209's GPIO 6, default) or "acc"
    acc_key_on   filtered ACC key state, or None when it can't be read
    pld_high     the X1209 power-loss line (GPIO 6) is high
    """
    if signal == "acc" and acc_key_on is not None:
        return bool(acc_key_on)
    return bool(pld_high)


def _ups_owns_acc():
    """True when ups.json hands the ignition-key line to the UPS monitor ("vehicle_signal": "acc")."""
    try:
        return json.loads((Path.home() / ".config/jeep_computer/ups.json").read_text()).get("vehicle_signal") == "acc"
    except (OSError, ValueError):
        return False


def open_acc():
    """The UPS monitor's shared state when it owns the line, else an AccReader.

    Only one process can hold GPIO 10. With vehicle_signal "acc" the UPS monitor must own it
    (it shuts the Pi down on key-off), so the dashboard never grabs it first.
    """
    if _ups_owns_acc():
        return SharedAccReader()
    try:
        return AccReader()
    except Exception as first:
        if SHARED_FILE.exists():
            return SharedAccReader()
        raise first
