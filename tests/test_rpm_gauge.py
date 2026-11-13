"""
The Home page RPM dial: rpm -> needle angle, redline band, None / no-signal
handling, the tach.json scale settings. Offscreen Qt, no hardware.

Run:  QT_QPA_PLATFORM=offscreen .venv/bin/python -m tests.test_rpm_gauge
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from sensors.tach import gauge_scale, DEFAULTS  # noqa: E402
from dashboard.widgets.arc_gauge import START_DEG, SWEEP_DEG  # noqa: E402
from dashboard.pages.home import HomePage, rpm_ticks  # noqa: E402

app = QApplication.instance() or QApplication([])
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


check("defaults: 6000 max, 4500 redline", gauge_scale({}) == (6000.0, 4500.0) and DEFAULTS["gauge_max"] == 6000)
check("tach.json values are used", gauge_scale({"gauge_max": 5000, "redline": 4000}) == (5000.0, 4000.0))
check("nonsense falls back", gauge_scale({"gauge_max": "x", "redline": 1}) == (6000.0, 4500.0)
      and gauge_scale({"gauge_max": 3000, "redline": 4000}) == (6000.0, 4500.0))
check("ticks every 1000 with minor ticks between", (6000, "6") in rpm_ticks(6000) and (500, "") in rpm_ticks(6000)
      and (5500, "") in rpm_ticks(6000) and (6500, "") not in rpm_ticks(6000))

page = HomePage()
g = page.gauges["rpm"]
check("0 rpm at the left end, 6000 at the right", g.angle(0) == START_DEG and g.angle(6000) == START_DEG - SWEEP_DEG)
check("3000 rpm is straight up", abs(g.angle(3000) - 90.0) < 1e-9)
check("redline band starts at 4500", g.zones == [(4500.0, 6000.0, "warning")])
check("values above the top clamp", g.angle(9000) == g.angle(6000))

page.sensor_data = {"tach": {"status": "ERROR", "error": "no ignition signal", "data": {}}}
page.update_values()
check("no ignition signal: no value, readout dash", g.target is None and g.readout() == "—")
page.sensor_data = {}
page.update_values()
check("no tach sensor at all: same", g.target is None)
page.sensor_data = {"tach": {"status": "OK", "error": None, "data": {"rpm": 754, "sparks": 3}}}
page.update_values()
g.shown = g.target
check("750 rpm: target set, readout rounds to 10", g.target == 754 and g.readout() == "750", g.readout())
page.sensor_data = {"tach": {"status": "OK", "error": None, "data": {"rpm": 0, "sparks": 3}}}
page.update_values()
check("engine stopped (0 rpm) is a value, not an error", g.target == 0)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
