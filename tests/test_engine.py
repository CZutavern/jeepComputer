"""
Engine log, fuel use and gauge self-calibration (engine/logger.py) on a
simulated day: key on, an hour's drive burning 20 % of the tank, key off,
a fill-up, a second drive. Temporary database and calibration file.

Run:  .venv/bin/python -m tests.test_engine
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sensors import senders  # noqa: E402
from engine.logger import EngineLogger  # noqa: E402
from engine.store import EngineStore  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


tmp = Path(tempfile.mkdtemp())
senders.CAL_FILE = tmp / "senders_cal.json"
EngineStore(tmp / "engine.db").set("tach_proven", "1")    # the tach saw the engine run on an earlier drive
log = EngineLogger(lambda: EngineStore(tmp / "engine.db"))

SUPPLY = 1.30            # the "true" supply, a bit off the default 1.35 V


def volts(ohms):
    return SUPPLY * ohms / (ohms + senders.PULLUP_OHMS)


def fuel_ohms(pct):      # the fuel curve backwards (73: empty, 23: half, 10: full)
    return 23 + (50 - pct) / 50 * 50 if pct <= 50 else 10 + (100 - pct) / 50 * 13


def data(fuel, running, ignition=True):
    if not ignition:
        return {"analog": {"status": "OK", "data": {c: {"voltage": 0.0} for c in ("A0", "A1", "A2", "A3")}},
                "tach": {"status": "ERROR", "data": {}}}
    return {"analog": {"status": "OK", "data": {
        "A0": {"voltage": volts(fuel_ohms(fuel))}, "A1": {"voltage": volts(32)},
        "A2": {"voltage": volts(23 if running else 73)}, "A3": {"voltage": 0.5}}},
        "tach": {"status": "OK", "data": {"rpm": 800.0}} if running else {"status": "ERROR", "data": {}}}


t = 1_800_000_000.0


def run(seconds, fuel_from, fuel_to, running, ignition=True):
    global t
    summary = None
    for i in range(seconds):
        summary = log.add(data(fuel_from + (fuel_to - fuel_from) * i / seconds, running, ignition), now=t)
        t += 1
    return summary


run(60, 90, 90, False)                   # key on, engine off: calibration moments
run(30, 90, 90, True)                    # start up: the supply is learned from them
run(200, 90, 90, False, ignition=False)  # key off
check("supply learned from key-on, engine-off oil", abs(senders.supply_v() - SUPPLY) < 0.01,
      f"{senders.supply_v():.3f} V (default {senders.SUPPLY_V} V)")

run(60, 90, 90, False)                   # trip 2: key on
run(3600, 90, 70, True)                  # an hour's drive
run(200, 70, 70, False, ignition=False)  # key off: the trip ends
trip = log.store.trip(2)
check("trip 2: an hour of engine time", abs(trip["engine_s"] - 3600) < 30, f"{trip['engine_s']:.0f} s")
check("trip 2: fuel 90 -> 70 %", abs(trip["fuel_start"] - 90) < 1.5 and abs(trip["fuel_end"] - 70) < 1.5,
      f"{trip['fuel_start']:.1f} -> {trip['fuel_end']:.1f}")

run(80, 95, 95, False)                   # trip 3: filled up
check("fill-up detected", abs((log.store.trip(3)["refuel_gal"] or 0) - 5.25) < 0.5,
      f"{log.store.trip(3)['refuel_gal']} gal")
run(2400, 95, 85, True)                  # 10 % in 40 min, then 10 min steady
run(600, 85, 85, True)
log.last_summary = 0
s = log.add(data(85, True), now=t)
# 10 % of 21 gal over 50 min of engine: 2.5 gal/h
check("gal/h since the fill-up", s["gph"] is not None and abs(s["gph"] - 2.5) < 0.2,
      f"{s['gph']} gal/h over {s['gph_engine_hours']} h")
check("engine hours add up", abs(s["engine_hours"] - (30 + 3600 + 3000) / 3600) < 0.05, f"{s['engine_hours']} h")
check("gallons left from 21 gal", abs(s["gallons_left"] - 0.85 * 21) < 0.5, f"{s['gallons_left']} gal")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
