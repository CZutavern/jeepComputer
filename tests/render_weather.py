"""
Offscreen renders of the Weather and Home pages with synthetic data.

Run:  QT_QPA_PLATFORM=offscreen .venv/bin/python -m tests.render_weather OUTDIR
"""

import math
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QStackedWidget

from dashboard.theme import COLORS
from weather.analysis import Sample, analyse

# --real: render the stored history instead of synthetic weather
real = "--real" in sys.argv
args = [a for a in sys.argv[1:] if a != "--real"]
out_dir = Path(args[0] if args else ".")
random.seed(3)
app = QApplication([])

# ---- 3 days of synthetic weather -------------------------------------
now = time.time()
samples = []
for i in range(72 * 60):
    t = now - (72 * 60 - 1 - i) * 60
    hour = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
    day = math.sin((hour - 9) / 24 * 2 * math.pi)            # peaks ~3 pm

    minutes_left = 72 * 60 - i
    # Slow high-pressure drift, then a storm approaching in the last 6 h
    p = 1018 + 2.5 * math.sin(i / 1500)
    if minutes_left < 360:
        p -= (360 - minutes_left) * (7.0 / 360)
    # Drove 400 m higher on day 2 for 5 hours (elevation, not weather)
    moving = None
    if 30 * 60 < i < 35 * 60:
        p -= 45
    if 30 * 60 - 20 < i <= 30 * 60 or 35 * 60 - 20 < i <= 35 * 60:
        moving = True

    temp = 22 + 7 * day - (4 * (1 - minutes_left / 360) if minutes_left < 360 else 0)
    hum = 55 - 18 * day + (25 * (1 - minutes_left / 360) if minutes_left < 360 else 0)
    lux = max(0.0, 30000 * math.sin((hour - 6) / 14 * math.pi)) if 6 <= hour <= 20 else 0.0

    samples.append(Sample(t, p + random.gauss(0, 0.03), temp + random.gauss(0, 0.1),
                          min(100, hum + random.gauss(0, 0.5)), lux, moving))

elevation = None
if real:
    from weather.store import WeatherStore
    samples = WeatherStore().since(7 * 86400)
    elevation = 318 * 0.3048
summary = analyse(samples, now, elevation_m=elevation)
print(f"forecast: {summary.forecast!r}  storm {summary.storm_probability}% {summary.storm_level}")
change = "--" if summary.change_3h is None else f"{summary.change_3h:+.2f}"
print(f"3h {change} hPa  {summary.tendency}  ({summary.characteristic})")
print("alerts:", summary.alerts)

# ---- Weather page -----------------------------------------------------
from dashboard.pages.weather import WeatherPage

stack = QStackedWidget()
stack.setObjectName("pages")
stack.setStyleSheet(f"#pages {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                    f"stop:0 {COLORS['background_top']}, stop:1 {COLORS['background']}); }}")
stack.resize(1920, 930)

weather = WeatherPage()
stack.addWidget(weather)
stack.show()
weather.set_summary(summary)

def settle(ms=900):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)

settle()
stack.grab().save(str(out_dir / "weather_24h.png"))

weather.ranges.set_selected("72h"); weather.set_range("72h")
weather.select_metric("temp")
settle(200)
stack.grab().save(str(out_dir / "weather_temp_72h.png"))

# ---- Home page with live sender voltages -------------------------------
from dashboard.pages.home import HomePage

home = HomePage()
stack.addWidget(home)
stack.setCurrentWidget(home)

def ok(d):
    return {"status": "OK", "error": None, "data": d}

home.update_sensors({
    "amp_box": ok({"temperature_f": 96.8}),
    "outside": ok({"temperature_f": 84.2}),
    "power": ok({"voltage": 14.05}),
    "analog": ok({"A0": {"voltage": 1.55}, "A1": {"voltage": 1.62}, "A2": {"voltage": 1.95}}),
})
settle(1000)
stack.grab().save(str(out_dir / "home_gauges.png"))
print("rendered")
