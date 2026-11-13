"""
Fuel calibration override (senders.merge_fuel_points / load_fuel_points),
tools/capture_fuel_point.py refusals and ohms conversion, and
tools/reset_fuel_learning.py on a temporary database. No hardware.

Run:  .venv/bin/python -m tests.test_fuel_cal
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from sensors import senders  # noqa: E402
import capture_fuel_point as cap  # noqa: E402
import reset_fuel_learning as reset  # noqa: E402
from engine.store import EngineStore  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


D = senders.CALIBRATION["fuel"]
check("no custom points: defaults, sorted by ohms", senders.merge_fuel_points(D, []) == [(10, 100.0), (23, 50.0), (73, 0.0)])
check("same percent replaces", senders.merge_fuel_points(D, [[65.5, 0]]) == [(10, 100.0), (23, 50.0), (65.5, 0.0)])
check("new percent is added", senders.merge_fuel_points(D, [[40, 25]]) ==
      [(10, 100.0), (23, 50.0), (40.0, 25.0), (73, 0.0)])


def refuses(points):
    try:
        senders.merge_fuel_points(D, points)
    except ValueError:
        return True
    return False


check("out-of-order point refused", refuses([[15, 25]]))
check("bad numbers refused", refuses([[0, 10]]) and refuses([[30, 120]]) and refuses([[300, 10]]))

tmp = Path(tempfile.mkdtemp())
cal = tmp / "fuel_cal.json"
cal.write_text(json.dumps({"points": [[60, 0]]}))
check("file replaces the empty point", senders.load_fuel_points(cal)[-1] == (60.0, 0.0))
cal.write_text("{not json")
check("broken file falls back to defaults", senders.load_fuel_points(cal) == [(10, 100.0), (23, 50.0), (73, 0.0)])
check("missing file falls back to defaults", senders.load_fuel_points(tmp / "none.json")[-1] == (73, 0.0))
curve = senders.InterpCurve([(10, 100.0), (23, 50.0), (60.0, 0.0)])
check("curve clamps beyond the empty point", curve.value(70) == 0.0 and curve.value(60) == 0.0)

# conversion with a known supply
senders.CAL_FILE = tmp / "senders_cal.json"
senders.CAL_FILE.write_text(json.dumps({"supply_v": 1.2}))
senders._cal["mtime"] = None
v = senders.supply_v() * 40 / (40 + senders.PULLUP_OHMS)
check("A0 voltage -> ohms is the senders divider", abs(cap.ohms_from_voltage(v) - 40) < 1e-6)


def refused(voltage):
    try:
        cap.ohms_from_voltage(voltage)
    except cap.Refused:
        return True
    return False


check("3.5 V on A0 (floating) is refused", refused(3.5))
check("gauges off (0 V) is refused", refused(0.0) and refused(-0.02))
check("open sender (just under the supply) is refused", refused(1.19))
check("no reading is refused", refused(None))

p = tmp / "fuel_cal.json"
p.unlink(missing_ok=True)
cap.update_file(p, 61.234, 0)
check("capture writes the point", json.loads(p.read_text())["points"] == [[61.23, 0]])
cap.update_file(p, 22.0, 50)
cap.update_file(p, 58.0, 0)
pts = json.loads(p.read_text())["points"]
check("capture replaces the same percent and keeps a .bak", pts == [[22.0, 50], [58.0, 0]]
      and (tmp / "fuel_cal.json.bak").exists(), str(pts))
try:
    cap.update_file(p, 80.0, 25)          # 80 ohm at 25 % is out of order
    bad = False
except ValueError:
    bad = True
check("a point that breaks the curve is refused and the file is untouched",
      bad and json.loads(p.read_text())["points"] == pts)

# reset tool
db = tmp / "engine.db"
store = EngineStore(db)
for i in range(3):
    tid = store.open_trip(1000 + i)
store.set("mpg_odometer_start", "1")
store.set("mpg_start_ts", "2")
store.set("mpg_fuel_start_pct", "3")
store.set("tach_proven", "1")
store.add_sample({"ts": 5, "trip": 1, "ignition": 1})
store.add_anchor(6, 1, "oil_zero", 0.5, 1.2, True)
store.db.close()
trips, keys = reset.reset(db, "trips_archive_test", apply=False)
c = sqlite3.connect(str(db))
check("dry run changes nothing", trips == 3 and len(keys) == 3 and c.execute("select count(*) from trips").fetchone()[0] == 3)
reset.reset(db, "trips_archive_test", now=1234, apply=True)
check("trips archived and cleared", c.execute("select count(*) from trips_archive_test").fetchone()[0] == 3
      and c.execute("select count(*) from trips").fetchone()[0] == 0)
meta = dict(c.execute("select key, value from meta"))
check("meta keys removed, reset_ts set, others kept", "mpg_start_ts" not in meta and meta["reset_ts"] == "1234"
      and meta["tach_proven"] == "1")
check("samples and anchors kept", c.execute("select count(*) from samples").fetchone()[0] == 1
      and c.execute("select count(*) from anchors").fetchone()[0] == 1)
try:
    reset.reset(db, "trips_archive_test", apply=True)
    again = False
except SystemExit:
    again = True
check("a second apply is refused", again)
bak = tmp / "bak.db"
reset.backup(db, bak)
check("backup is a readable copy", sqlite3.connect(str(bak)).execute("select count(*) from trips_archive_test").fetchone()[0] == 3)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
