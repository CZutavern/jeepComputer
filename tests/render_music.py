"""
Offscreen render of the Music page (stem stage).

Run:  QT_QPA_PLATFORM=offscreen .venv/bin/python tests/render_music.py [out_dir]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QStackedWidget

from dashboard.theme import COLORS

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
app = QApplication([])

from dashboard.pages.music import MusicPlayerPage

stack = QStackedWidget()
stack.setObjectName("pages")
stack.setStyleSheet(f"#pages {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                    f"stop:0 {COLORS['background_top']}, stop:1 {COLORS['background']}); }}")
stack.resize(1920, 930)
page = MusicPlayerPage()
stack.addWidget(page)
stack.show()
page.meter_timer.stop()
page.stage.set_levels({"drums": 0.8, "bass": 0.6, "vocals": 0.9, "other": 0.4})
for _ in range(5):
    app.processEvents()
stack.grab().save(str(out_dir / "music.png"))
print("rendered")
