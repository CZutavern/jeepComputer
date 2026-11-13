"""
The UPS monitor's state machine driven by a fake ACC key (vehicle_signal "acc",
station mode, no sleep cycles, the recommended always-on settings): vehicle ->
grace -> station -> vehicle with no halt, the 48 h station limit with its
countdown (cancelled by the key, not once the halt command is issued), and
what the default settings would do with a low UPS cell. Everything that touches
the system (systemctl, uhubctl, GPIO, poweroff) is replaced; nothing real runs.

Run:  .venv/bin/python -m tests.test_ups_acc
"""

import importlib.util
import pathlib
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


# --- load ups_monitor.py without root, lgpio, SMBus or /run/jeep-ups -------------
for name in ("lgpio", "smbus2"):
    if name not in sys.modules:
        fake = types.ModuleType(name)
        fake.gpiochip_open = lambda n: 1
        fake.gpio_claim_input = lambda h, p: None
        fake.gpio_read = lambda h, p: 1            # X1209 line: external power present
        fake.gpiochip_close = lambda h: None
        fake.SMBus = object
        sys.modules[name] = fake
sys.modules["lgpio"].gpio_read = lambda h, p: 1

_mkdir = pathlib.Path.mkdir
pathlib.Path.mkdir = lambda self, *a, **k: None if str(self) == "/run/jeep-ups" else _mkdir(self, *a, **k)
spec = importlib.util.spec_from_file_location("ups_monitor_under_test", ROOT / "ups_monitor.py")
ups = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ups)
pathlib.Path.mkdir = _mkdir

tmp = pathlib.Path(tempfile.mkdtemp())
ups.ACC_SHARED_FILE = tmp / "missing-dir" / "acc.json"       # the publish fails quietly: no disk writes in the loop
ups.SIMULATE_FILE = tmp / "no-such-simulate-file"


class Clock:
    def __init__(self):
        self.t = 5000.0

    def monotonic(self):
        return self.t

    def time(self):
        return self.t

    def sleep(self, s):
        pass


clock = Clock()
ups.time = clock
ups.uptime = lambda: 10_000.0

ALWAYS_ON = {"vehicle_signal": "acc", "station_mode": True, "duty_cycle": False, "station_max_hours": 48,
             "grace_s": 30, "critical_voltage": -1, "critical_percent": -1,
             "station_reserve_percent": -1, "station_reserve_voltage": -1}


class FakeAcc:
    def __init__(self):
        self.key_on = True

    def read(self):
        return {"key_on": self.key_on, "raw": self.key_on, "since_s": 1.0}

    def close(self):
        pass


def make(settings, cell=(3.9, 90.0)):
    """A monitor with every system action replaced by a recorder."""
    m = object.__new__(ups.UpsMonitor)
    m.settings = {**ups.DEFAULT_SETTINGS, **settings}
    m.settings_mtime = None
    m.reload_settings_if_changed = lambda: None
    m.gpio = 1
    m.halt_at = None
    m.acc, m.acc_retry_at = FakeAcc(), 0.0
    m.gauge = types.SimpleNamespace(read=lambda: cell)
    m.mode, m.on_battery_since, m.station_since = "vehicle", None, None
    m.battery_history = ups.deque(maxlen=3600)
    m.last_powered, m.shutting_down, m.simulated_shutdown, m.saved_governor = None, False, None, None
    m.duty_verified = False
    m.calls, m.status, m.halts = [], {}, []
    for name in ("amps_off", "amps_auto", "stop_dashboard", "set_governor"):
        setattr(m, name, lambda *a, _n=name: m.calls.append(_n))
    m.usb_power = lambda on: m.calls.append("usb_on" if on else "usb_off")
    m.write_status = lambda **kw: m.status.update(kw)
    real_shutdown = ups.UpsMonitor.shutdown
    m.shutdown_reasons = []
    m.shutdown = lambda reason: m.shutdown_reasons.append(reason)
    return m


ups.run = lambda cmd, **k: calls_sink.append(tuple(cmd))
ups.user_systemctl = lambda *a: calls_sink.append(("user",) + a) or True
ups.set_wakealarm = lambda s: True
calls_sink = []


def steps(m, seconds, key=None, dt=1.0):
    """Run the monitor for `seconds` of fake time, one step every dt s (long parked
    stretches use dt=60: the loop itself is 1 s in real life)."""
    if key is not None:
        m.acc.key_on = key
    for _ in range(int(seconds / dt)):
        clock.t += dt
        m.step()


# --- vehicle -> grace -> station -> vehicle ---------------------------------------
m = make(ALWAYS_ON)
steps(m, 5, key=True)
check("key on: vehicle mode", m.mode == "vehicle" and m.status["vehicle_power"] is True)
steps(m, 1, key=False)
check("key off: grace starts", m.mode == "grace" and m.status["grace_action"] == "station", m.mode)
steps(m, 20)
check("still grace at 21 s", m.mode == "grace")
steps(m, 15)
check("station mode after the 30 s grace", m.mode == "station", m.mode)
check("amps, dashboard and USB switched off", {"amps_off", "stop_dashboard", "usb_off"} <= set(m.calls), str(m.calls))
check("weather station started, desktop stopped", ("user", "start", "jeep-weather-station.service") in calls_sink
      and ("systemctl", "stop", "lightdm") in calls_sink)
steps(m, 3600 * 5, dt=60)
check("5 h parked: still station, no shutdown", m.mode == "station" and not m.shutdown_reasons)
check("status shows station hours and the limit", m.status["station_max_hours"] == 48
      and 4.9 < m.status["station_hours"] < 5.2 and m.status["halt_warning"] is None, str(m.status["station_hours"]))
calls_sink.clear()
m.calls.clear()
steps(m, 1, key=True)
check("key on: back to vehicle without a reboot", m.mode == "vehicle" and m.station_since is None)
check("display, USB and amps restored", "usb_on" in m.calls and "amps_auto" in m.calls
      and ("systemctl", "start", "lightdm") in calls_sink and ("systemctl", "start", "camilladsp") in calls_sink)
check("no halt in the whole cycle", not m.shutdown_reasons)

# --- cranking dip shorter than the grace never leaves vehicle mode -----------------
m = make(ALWAYS_ON)
steps(m, 3, key=True)
steps(m, 10, key=False)
steps(m, 1, key=True)
check("key back on within the grace: vehicle, nothing switched off", m.mode == "vehicle" and "usb_off" not in m.calls)

# --- low UPS cell: defaults halt, always-on settings do not ------------------------
m = make({"vehicle_signal": "acc"}, cell=(3.2, 3.0))
steps(m, 3, key=True)
steps(m, 40, key=False)
check("DEFAULT settings with a flat cell would shut down", bool(m.shutdown_reasons), m.shutdown_reasons[0])
m = make(ALWAYS_ON, cell=(3.2, 3.0))
steps(m, 3, key=True)
steps(m, 400, key=False, dt=10)
check("always-on settings ignore the cell", m.mode == "station" and not m.shutdown_reasons, m.mode)
m = make(ALWAYS_ON, cell=(None, None))
steps(m, 3, key=True)
steps(m, 40, key=False)
check("a missing fuel gauge does not halt", m.mode == "station" and not m.shutdown_reasons)

# --- ACC unreadable falls back to GPIO 6 (high with external power) ---------------
m = make(ALWAYS_ON)
m.acc = None
m.acc_retry_at = clock.t + 10_000
steps(m, 40)
check("ACC unreadable: stays in vehicle mode", m.mode == "vehicle" and not m.shutdown_reasons)

# --- 48 h limit: warning, countdown, cancel; and no cancel after the halt ----------
m = make(ALWAYS_ON)
steps(m, 3, key=True)
steps(m, 40, key=False)
assert m.mode == "station"
steps(m, 47 * 3600 - 120, dt=60)
check("before 47 h: no warning", m.status["halt_warning"] is None, str(m.status["station_hours"]))
steps(m, 240, dt=10)
check("after 47 h: warning says the Pi stays off until the button",
      m.status["halt_warning"] and "X1209 button" in m.status["halt_warning"] and not m.shutdown_reasons)
steps(m, 3510, dt=10)
check("48 h reached: countdown running, no halt yet", m.halt_at is not None and not m.shutdown_reasons
      and "Turn the key" in m.status["halt_warning"], m.status["halt_warning"])
steps(m, 30, key=True)
check("key on during the countdown cancels it and returns to vehicle mode",
      m.halt_at is None and m.mode == "vehicle" and not m.shutdown_reasons)

m = make(ALWAYS_ON)
steps(m, 3, key=True)
steps(m, 40, key=False)
steps(m, 47 * 3600 + 3600 + 20, dt=60)
check("countdown starts at the limit", m.halt_at is not None and not m.shutdown_reasons)
steps(m, 70)
check("halt requested after the countdown, with the stays-off note",
      m.shutdown_reasons and all("X1209 button" in r for r in m.shutdown_reasons), str(m.shutdown_reasons[:1]))

# the real shutdown(): once poweroff is issued it is final for this process
real = ups.UpsMonitor.shutdown
issued = []
ups.subprocess.run = lambda cmd, **k: issued.append(cmd)
ups.SHUTDOWN_MARKER = tmp / "marker"
m2 = make(ALWAYS_ON)
m2.shutdown = types.MethodType(real, m2)
m2.shutdown("test")
m2.shutdown("test again")
check("poweroff issued once and the monitor is shutting down", issued == [["systemctl", "poweroff"]]
      and m2.mode == "shutting_down" and m2.shutting_down)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
