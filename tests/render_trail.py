"""
Offscreen render of the Trail (Off Road) page with a simulated climb.

Run:  QT_QPA_PLATFORM=offscreen .venv/bin/python tests/render_trail.py [out_dir]
"""

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QStackedWidget

from dashboard.theme import COLORS

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
app = QApplication([])

from dashboard.pages import offroad
offroad.SETTINGS = out_dir / "trail-test.json"      # don't touch the real settings
from dashboard.pages.offroad import OffroadPage

stack = QStackedWidget()
stack.setObjectName("pages")
stack.setStyleSheet(f"#pages {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                    f"stop:0 {COLORS['background_top']}, stop:1 {COLORS['background']}); }}")
stack.resize(1920, 930)
page = OffroadPage()
stack.addWidget(page)
stack.show()

# 70 minutes of trail: climb 900 ft with a dip, sampled every second
real_time = time.time
start = real_time() - 70 * 60
for i in range(0, 70 * 60, 2):
    t = start + i
    ft = 1200 + 900 * (i / 4200) - 120 * math.sin(i / 600) + 3 * math.sin(i / 7)
    p_hpa = 1013.25 * (1 - ft / 3.28084 / 44330.77) ** (1 / 0.190263)
    roll = 18 * math.sin(i / 300) + 4
    pitch = 14 * math.sin(i / 240) + 2
    time.time = lambda t=t: t
    page.update_sensors({
        "imu": {"status": "OK", "data": {"heading": (i / 10) % 360, "roll": roll, "pitch": pitch}},
        "pressure": {"status": "OK", "data": {"pressure": p_hpa}},
    })
time.time = real_time
page.chart.now = real_time()
page.update_sensors({
    "imu": {"status": "OK", "data": {"heading": 312, "roll": 21.5, "pitch": -8.0}},
    "pressure": {"status": "OK", "data": {"pressure": p_hpa}},
})

for _ in range(10):
    app.processEvents()
    time.sleep(0.05)
stack.grab().save(str(out_dir / "trail.png"))
print("rendered")
