"""
The ACC key reader's filter, the starting-battery conversion and
calibration, the engine logger's running rule (tach / charging / oil), the
UPS monitor's vehicle_signal choice, and a SensorManager with no INA238.
No hardware needed.

Run:  .venv/bin/python -m tests.test_vehicle_inputs
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sensors import acc, battery, senders  # noqa: E402
from engine.logger import EngineLogger  # noqa: E402
from engine.store import EngineStore  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import calibrate_start_battery as calibrate  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


# ---------------------------------------------------------------- ACC filter

def feed(f, levels):
    """levels: [(raw, seconds)] sampled every 0.1 s; returns the key_on after each segment."""
    t = feed.t
    out = []
    for raw, seconds in levels:
        for _ in range(int(round(seconds * 10))):
            t += 0.1
            f.update(raw, t)
        out.append(f.key_on)
    feed.t = t
    return out


feed.t = 100.0
f = acc.AccFilter(now=feed.t)
check("ACC starts off", f.key_on is False)
check("ACC on after 0.3 s LOW", feed(f, [(True, 0.1), (True, 0.4)]) == [False, True])
check("a 1 s dip stays on", feed(f, [(False, 1.0), (True, 2.0)]) == [True, True] and f.key_on)
check("1.9 s HIGH stays on", feed(f, [(False, 1.9), (True, 1.0)]) == [True, True])
check("3 s HIGH turns it off", feed(f, [(False, 3.0)]) == [False])
check("a 0.1 s LOW blip does not turn it on", feed(f, [(True, 0.1), (False, 1.0)]) == [False, False])
f2 = acc.AccFilter(now=0)
f2.update(True, 1.0)
f2.update(True, 1.5)
d = f2.read(4.0)
check("read() reports key_on, raw and since_s", d == {"key_on": True, "raw": True, "since_s": 2.5}, str(d))
check("10 is reserved so the tach can never claim it", 10 in acc.RESERVED_PINS)

# ---------------------------------------------------------------- battery

S = dict(battery.DEFAULTS)
check("12.6 V battery reads 2.27 V on A3 and converts back",
      abs(battery.convert(12.6 * 0.18033, S) - 12.6) < 1e-6)
check("scale and offset apply", abs(battery.convert(2.0, {**S, "scale": 1.02, "offset_v": 0.1})
                                    - (2.0 / 0.18033 * 1.02 + 0.1)) < 1e-9)
check("None and a disconnected input give None", battery.convert(None, S) is None and battery.convert(0.02, S) is None)
check("an impossible voltage (floating A3, 3.5 V = 19.5 V) gives None", battery.convert(3.5243, S) is None and battery.convert(2.9, S) is not None)
check("broken settings give None", battery.convert(2.3, {"divider": 0}) is None)
r = battery.reading(2.2712, S)
check("reading() gives voltage and a3_volts", abs(r["voltage"] - 12.595) < 0.01 and r["a3_volts"] == 2.2712, str(r))

scale, why = calibrate.plan(12.80, 2.27, S)          # meter says 12.80, divider alone says 12.59
check("calibration makes v_start equal the meter", scale and abs(battery.convert(2.27, {**S, "scale": scale}) - 12.80) < 1e-9,
      f"scale {scale:.4f}")
check("calibration refuses a dead A3", calibrate.plan(12.6, 0.2, S)[0] is None)
check("calibration refuses an implausible scale", calibrate.plan(12.6, 1.5, S)[0] is None)
tmp = Path(tempfile.mkdtemp())
path = tmp / "battery.json"
check("first save writes the file", calibrate.save(1.0166, path) is True)
check("saving the same scale again changes nothing", calibrate.save(1.0166, path) is False)
saved = json.loads(path.read_text())
check("the other settings are kept", saved["divider"] == 0.18033 and saved["scale"] == 1.0166, str(saved))
calibrate.save(1.02, path)
scale2, _ = calibrate.plan(12.80, 2.27, {**S, "scale": 1.02})
check("repeating the calibration gives the same scale", abs(scale2 - scale) < 1e-12)

# ---------------------------------------------------------------- logger running rule

senders.CAL_FILE = tmp / "senders_cal.json"
log = EngineLogger(lambda: EngineStore(tmp / "engine.db"))
log.store = log.store_factory()


def sample(a3_battery=None, rpm=None, oil_v=0.0, acc_on=None, gauges=True, tach=True):
    v = 0.5 if gauges else 0.0
    d = {"analog": {"status": "OK", "data": {"A0": {"voltage": v}, "A1": {"voltage": v},
                                              "A2": {"voltage": oil_v}, "A3": {"voltage": 2.4}}}}
    if a3_battery is not None:
        d["battery"] = {"status": "OK", "data": {"voltage": a3_battery}}
    if rpm is not None:
        d["tach"] = {"status": "OK", "data": {"rpm": rpm}}
    if acc_on is not None:
        d["acc"] = {"status": "OK", "data": {"key_on": acc_on, "raw": acc_on, "since_s": 5}}
    return log.read(d)


oil_off = senders.supply_v() * 73 / (73 + senders.PULLUP_OHMS)       # about 0 psi
oil_on = senders.supply_v() * 23 / (23 + senders.PULLUP_OHMS)        # running pressure
check("charging 13.8 V counts as running (no tach)", sample(13.8, oil_v=oil_off)["running"])
check("12.3 V key on is not running", not sample(12.3, oil_v=oil_off)["running"])
check("exactly 13.2 V counts as running", sample(13.2, oil_v=oil_off)["running"])
check("oil pressure alone counts as running", sample(12.3, oil_v=oil_on)["running"])
check("v_start goes to the v_system column value", sample(13.8, oil_v=oil_off)["v_system"] == 13.8)
check("battery sensor absent: old behaviour (oil), no crash",
      sample(None, oil_v=oil_on)["running"] and not sample(None, oil_v=oil_off)["running"])
check("INA238 'power' data is ignored", not log.read({"power": {"status": "OK", "data": {"voltage": 14.0}},
                                                      "analog": {"status": "OK", "data": {}}})["running"])
check("ACC key state is carried as accessory info", sample(12.3, oil_v=oil_off, acc_on=True)["acc"] is True
      and sample(12.3, oil_v=oil_off)["acc"] is None)
check("gauges off + charging voltage: not running (needs ignition)", not sample(13.8, gauges=False)["running"])
check("rpm 800 counts as running", sample(12.3, rpm=800, oil_v=oil_off)["running"])     # also proves the tach
check("a proven tach is believed over charging voltage", not sample(13.8, rpm=0, oil_v=oil_off)["running"])

# ---------------------------------------------------------------- UPS vehicle_signal

pick = acc.pick_vehicle_power
check("default 'ups' follows GPIO 6 only", pick("ups", False, True) is True and pick("ups", True, False) is False)
check("'acc' follows the key", pick("acc", True, False) is True and pick("acc", False, True) is False)
check("'acc' falls back to GPIO 6 if ACC can't be read", pick("acc", None, True) is True and pick("acc", None, False) is False)
check("unknown value acts like 'ups'", pick("bogus", True, False) is False)
src = (Path(__file__).resolve().parent.parent / "ups_monitor.py").read_text()
check("ups_monitor defaults vehicle_signal to 'ups'", '"vehicle_signal": "ups"' in src)

# ---------------------------------------------------------------- SensorManager without INA238

try:
    from sensors.sensor_manager import SensorManager
except Exception as e:                       # development machine without the I2C libraries
    SensorManager = None
    print("[SKIP] SensorManager (needs board/adafruit):", e)

if SensorManager is not None:
    names = [n for n, _ in SensorManager.ALL_SENSORS]
    check("INA238 is not in the sensor list", "power" not in names and "analog" in names, str(names))

    class FakeAnalog:
        def read(self):
            return {"A0": {"voltage": 0.5}, "A3": {"voltage": 2.27}}

    class FakeAcc:
        def read(self):
            return {"key_on": True, "raw": True, "since_s": 3.0}

    m = object.__new__(SensorManager)
    m.sensors = {"analog": {"sensor": FakeAnalog(), "class": None, "status": "OK", "error": None}}
    m.tach, m.acc, m.acc_error = None, FakeAcc(), None
    m.bus_failures = 0
    clean = m.get_data()
    check("no 'power' entry and no crash", "power" not in clean and clean["analog"]["status"] == "OK")
    check("'battery' sensor from A3", clean["battery"]["status"] == "OK"
          and abs(clean["battery"]["data"]["voltage"] - 2.27 / 0.18033 * battery.load_settings()["scale"]) < 1e-6
          and clean["battery"]["data"]["a3_volts"] == 2.27, str(clean["battery"]))
    check("'acc' sensor data", clean["acc"] == {"status": "OK", "error": None,
                                                "data": {"key_on": True, "raw": True, "since_s": 3.0}})
    m.acc, m.acc_error = None, "gpio busy"
    check("ACC failure is an ERROR entry, not an exception", m.get_data()["acc"]["status"] == "ERROR")
    m.sensors["analog"]["sensor"] = None
    m.sensors["analog"]["error"] = "No I2C device at address: 0x48"
    check("analog down: battery ERROR, nothing raises", m.get_data()["battery"]["status"] == "ERROR")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
