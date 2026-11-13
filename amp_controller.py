"""
Amplifier remote-on controller.

Drives the amp turn-on circuit from a GPIO pin and decides when the
amps should be on:

  auto  - on while CamillaDSP is running, the CarPlay audio route is
          linked and audio is actually playing; off again after
          `idle_off_s` seconds of silence (no static while nothing
          plays). idle_off_s = 0 keeps them on whenever audio is ready.
  on    - forced on
  off   - forced off

The dashboard talks to this service over a small unix socket
($XDG_RUNTIME_DIR/jeep-amp.sock), one JSON request per connection:

  {"cmd": "status"}
  {"cmd": "set_mode", "mode": "auto" | "on" | "off"}
  {"cmd": "set_idle_off", "seconds": 30}

Settings live in ~/.config/jeep_computer/amp.json. The mode always
starts in "auto" after a restart.
"""

import json
import os
import select
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

# lgpio drops its notification pipes in the working directory; give
# this service its own so it never collides with the dashboard's.
_LG_WD = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "jeep-amp-lgpio"
_LG_WD.mkdir(exist_ok=True)
os.environ.setdefault("LG_WD", str(_LG_WD))

import lgpio

from audio.camilla_client import CamillaClient
from audio.routing import route_ok


SETTINGS_FILE = Path.home() / ".config/jeep_computer/amp.json"
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "jeep-amp.sock"

DEFAULT_SETTINGS = {
    # GPIO 17 = header pin 11. GPIO 18-27 belong to the HiFiBerry DAC8x.
    "pin": 17,
    # True if driving the pin HIGH turns the amps on
    "active_high": True,
    # Auto mode: seconds of silence before the amps turn off (0 = never)
    "idle_off_s": 30,
}

LOOP_S = 0.2
ROUTE_CHECK_S = 2.0
GPIO_RETRY_S = 10.0

# Let the DAC settle before the amps come on, so there's no thump
BOOT_DELAY_S = 3.0
CAMILLA_SETTLE_S = 1.0

# Don't flip the amps faster than this (protects relays / amp soft-start)
MIN_TOGGLE_S = 2.0

# Anything above this on any CamillaDSP input counts as "playing"
SIGNAL_THRESHOLD_DB = -70.0

# CarPlay dropout counter: music (inputs 1-2) that stops dead, digital
# silence, then comes back within a few seconds. Songs fade out and pauses
# last longer, so this counts the gaps you hear as audio cutting out
# (the phone -> dongle -> LIVI stream starving). Logged to the journal.
CARPLAY_CHANNELS = (0, 1)
DROPOUT_LOUD_DB = -40.0         # playing, before and after the gap
DROPOUT_SILENT_DB = -85.0       # the gap: nothing at all
DROPOUT_MIN_S = 0.15
DROPOUT_MAX_S = 6.0

# DAC guard. The DAC8x gets its 8 channels over four I2S data lines; when
# CamillaDSP recovers from an underrun it re-prepares the stream in place,
# and the lines can come back out of step (channel pairs on the wrong
# outputs until the device is reopened). After underruns, reopen the DAC:
# once the burst is over, at the first quiet moment (or after a limit, so
# wrong channels don't last a whole drive), with the amps off meanwhile.
RESYNC_AFTER_BURST_S = 5.0
RESYNC_QUIET_S = 1.5
RESYNC_MAX_WAIT_S = 20.0


def load_settings():
    settings = dict(DEFAULT_SETTINGS)

    try:
        settings.update(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        pass

    return settings


def save_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2) + "\n")


class AmpController:

    def __init__(self):
        self.settings = load_settings()
        save_settings(self.settings)

        self.mode = "auto"
        self.amps_on = False
        self.reason = "Starting"
        self.error = None

        self.started = time.monotonic()
        self.last_toggle = 0.0
        self.last_signal = None
        self.camilla_state = None
        self.camilla_running_since = None
        self.route_linked = False
        self.last_route_check = 0.0

        self.gpio = None
        self.last_gpio_attempt = 0.0

        self.camilla = CamillaClient(timeout=0.5, log_errors=False)
        self.underrun_first = None
        self.underrun_last = None
        self.resyncs = 0
        self.carplay_dropouts = 0
        self.last_dropout = None
        self.last_loud = None
        self.gap_start = None
        self.last_resync = None
        threading.Thread(target=self.watch_underruns, name="underruns", daemon=True).start()
        self.last_camilla_attempt = 0.0

    # ---------------------------------------------------------
    # GPIO
    # ---------------------------------------------------------

    def claim_gpio(self):
        now = time.monotonic()

        if self.gpio is not None or now - self.last_gpio_attempt < GPIO_RETRY_S:
            return

        self.last_gpio_attempt = now
        pin = self.settings["pin"]

        try:
            handle = lgpio.gpiochip_open(0)
        except lgpio.error as e:
            self.error = f"Cannot open GPIO chip: {e}"
            return

        try:
            lgpio.gpio_claim_output(handle, pin, self.level(False))
        except lgpio.error as e:
            lgpio.gpiochip_close(handle)
            self.error = f"Cannot use GPIO {pin}: {e}"
            print(self.error, flush=True)
            return

        self.gpio = handle
        self.error = None
        print(f"Amp remote on GPIO {pin}", flush=True)

    def level(self, on):
        return 1 if on == self.settings["active_high"] else 0

    def write_amps(self, on):
        if self.gpio is None:
            return

        lgpio.gpio_write(self.gpio, self.settings["pin"], self.level(on))

    def release_gpio(self):
        if self.gpio is None:
            return

        self.write_amps(False)
        lgpio.gpio_free(self.gpio, self.settings["pin"])
        lgpio.gpiochip_close(self.gpio)
        self.gpio = None

    # ---------------------------------------------------------
    # Inputs
    # ---------------------------------------------------------

    def poll_camilla(self, now):
        # Reconnect at most every couple of seconds while CamillaDSP is down
        if not self.camilla.connected:
            if now - self.last_camilla_attempt < ROUTE_CHECK_S:
                self.camilla_state = None
                self.camilla_running_since = None
                return
            self.last_camilla_attempt = now

        try:
            state = self.camilla.get_state()
            levels = self.camilla.call("GetSignalLevelsSinceLast")

        except RuntimeError:
            state, levels = None, None

        if state == "Running":
            if self.camilla_state != "Running":
                self.camilla_running_since = now
        else:
            self.camilla_running_since = None

        self.camilla_state = state

        peaks = (levels or {}).get("capture_peak") or []

        if peaks and max(peaks) > SIGNAL_THRESHOLD_DB:
            self.last_signal = now

        if state == "Running" and len(peaks) > max(CARPLAY_CHANNELS):
            self.watch_dropouts(now, max(peaks[ch] for ch in CARPLAY_CHANNELS))
        else:
            self.gap_start = None

    def watch_dropouts(self, now, peak):
        if peak > DROPOUT_LOUD_DB:
            if self.gap_start is not None:
                gap = now - self.gap_start
                if DROPOUT_MIN_S <= gap <= DROPOUT_MAX_S:
                    self.carplay_dropouts += 1
                    self.last_dropout = time.time()
                    print(f"CarPlay dropout: {gap:.1f} s of silence mid-song "
                          f"({self.carplay_dropouts} since start)", flush=True)
                self.gap_start = None
            self.last_loud = now
        elif peak < DROPOUT_SILENT_DB:
            # Only a cut straight from music: loud in the previous poll
            if self.gap_start is None and self.last_loud is not None and now - self.last_loud <= LOOP_S * 2.5:
                self.gap_start = self.last_loud + LOOP_S / 2
            elif self.gap_start is not None and now - self.gap_start > DROPOUT_MAX_S:
                self.gap_start = None          # a pause or the end of a song
        else:
            self.gap_start = None              # quiet music, not a dropout

    def poll_route(self, now):
        if now - self.last_route_check >= ROUTE_CHECK_S:
            self.last_route_check = now
            self.route_linked = route_ok()

    # ---------------------------------------------------------
    # Decision
    # ---------------------------------------------------------

    def silent_for(self, now):
        if self.last_signal is None:
            return None
        return now - self.last_signal

    def decide(self, now):
        if self.mode == "on":
            return True, "Forced on"

        if self.mode == "off":
            return False, "Forced off"

        if now - self.started < BOOT_DELAY_S:
            return False, "Starting"

        if self.camilla_state != "Running":
            return False, "CamillaDSP not running"

        if now - self.camilla_running_since < CAMILLA_SETTLE_S:
            return False, "CamillaDSP starting"

        if not self.route_linked:
            return False, "Audio route not linked"

        silent = self.silent_for(now)
        idle_off = self.settings["idle_off_s"]

        if silent is None:
            return False, "Waiting for audio"

        if silent < 1.0:
            return True, "Playing"

        if idle_off <= 0 or silent < idle_off:
            return True, f"Silent {silent:.0f}s"

        return False, "Idle (silent)"

    # ---------------------------------------------------------
    # DAC guard
    # ---------------------------------------------------------

    def watch_underruns(self):
        """Follow CamillaDSP's log for playback underruns."""
        while True:
            try:
                proc = subprocess.Popen(["journalctl", "-u", "camilladsp", "-f", "-n", "0", "-o", "cat"],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                for line in proc.stdout:
                    if "underrun" in line:
                        now = time.monotonic()
                        self.underrun_first = self.underrun_first or now
                        self.underrun_last = now
            except OSError:
                pass
            time.sleep(5)

    def resync_dac(self, now):
        """Reopen the DAC by changing CamillaDSP's devices section (target
        level 768 <-> 769, i.e. 0.02 ms): it closes and reopens the device
        in ~1.5 s without restarting. Amps stay off until it has settled."""
        if self.amps_on:
            self.amps_on = False
            self.last_toggle = now
            self.write_amps(False)
        try:
            config = json.loads(self.camilla.call("GetConfigJson"))
            level = config["devices"]["target_level"]
            config["devices"]["target_level"] = level + 1 if level % 2 == 0 else level - 1
            self.camilla.call("SetConfigJson", json.dumps(config))
        except Exception as e:
            print("DAC resync failed:", e, flush=True)
            return
        self.camilla_running_since = now        # decide() waits for it to settle
        self.resyncs += 1
        self.last_resync = time.time()
        self.underrun_first = self.underrun_last = None
        print(f"DAC reopened after underruns (re-sync #{self.resyncs})", flush=True)

    def check_dac(self, now):
        if self.underrun_last is None or self.camilla_state != "Running":
            return
        if now - self.underrun_last < RESYNC_AFTER_BURST_S:
            return                              # still in the burst
        silent = self.silent_for(now)
        quiet = silent is None or silent >= RESYNC_QUIET_S
        if quiet or now - self.underrun_first >= RESYNC_MAX_WAIT_S:
            self.resync_dac(now)

    def step(self):
        now = time.monotonic()

        self.claim_gpio()
        self.poll_camilla(now)
        self.poll_route(now)
        self.check_dac(now)

        want, self.reason = self.decide(now)

        # Forced changes happen right away; auto changes are rate limited
        forced = self.mode != "auto"

        if want != self.amps_on and (forced or now - self.last_toggle >= MIN_TOGGLE_S):
            self.amps_on = want
            self.last_toggle = now
            self.write_amps(want)
            print(f"Amps {'ON' if want else 'OFF'} ({self.reason})", flush=True)

    # ---------------------------------------------------------
    # Control socket
    # ---------------------------------------------------------

    def status(self):
        now = time.monotonic()
        silent = self.silent_for(now)

        return {
            "mode": self.mode,
            "amps_on": self.amps_on,
            "reason": self.reason,
            "pin": self.settings["pin"],
            "idle_off_s": self.settings["idle_off_s"],
            "silent_for": None if silent is None else round(silent, 1),
            "camilla_state": self.camilla_state,
            "dac_resyncs": self.resyncs,
            "carplay_dropouts": self.carplay_dropouts,
            "last_carplay_dropout": self.last_dropout,
            "last_dac_resync": self.last_resync,
            "route_ok": self.route_linked,
            "gpio_ok": self.gpio is not None,
            "error": self.error,
        }

    def handle(self, request):
        cmd = request.get("cmd")

        if cmd == "set_mode":
            mode = request.get("mode")
            if mode not in ("auto", "on", "off"):
                return {"ok": False, "error": f"bad mode {mode!r}"}
            self.mode = mode
            print(f"Mode -> {mode}", flush=True)
            self.step()

        elif cmd == "set_idle_off":
            try:
                seconds = max(0, min(3600, int(request.get("seconds"))))
            except (TypeError, ValueError):
                return {"ok": False, "error": "seconds must be a number"}
            self.settings["idle_off_s"] = seconds
            save_settings(self.settings)

        elif cmd != "status":
            return {"ok": False, "error": f"unknown command {cmd!r}"}

        return {"ok": True, **self.status()}

    def serve_client(self, conn):
        conn.settimeout(0.5)

        try:
            data = b""
            while not data.endswith(b"\n") and len(data) < 4096:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                data += chunk

            try:
                reply = self.handle(json.loads(data or b"{}"))
            except ValueError:
                reply = {"ok": False, "error": "invalid JSON"}

            conn.sendall(json.dumps(reply).encode() + b"\n")

        except OSError:
            pass

        finally:
            conn.close()

    # ---------------------------------------------------------

    def run(self):
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(SOCKET_PATH))
        server.listen(4)

        print(f"Amp controller running (GPIO {self.settings['pin']}, "
              f"auto-off after {self.settings['idle_off_s']}s)", flush=True)

        try:
            next_step = 0.0

            while True:
                timeout = max(0.0, next_step - time.monotonic())
                readable, _, _ = select.select([server], [], [], timeout)

                if readable:
                    conn, _ = server.accept()
                    self.serve_client(conn)

                if time.monotonic() >= next_step:
                    self.step()
                    next_step = time.monotonic() + LOOP_S

        finally:
            print("Amps OFF (controller stopping)", flush=True)
            self.release_gpio()
            self.camilla.close()
            server.close()
            if SOCKET_PATH.exists():
                SOCKET_PATH.unlink()


def main():
    # systemd stops us with SIGTERM: exit through the finally block
    # so the amps are switched off and the GPIO released
    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)

    AmpController().run()


if __name__ == "__main__":
    main()
