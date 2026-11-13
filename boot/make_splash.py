"""
Render the boot splash / wallpaper in the dashboard's blue theme.

  splash.png          upright  (desktop wallpaper, shown after the
                                compositor has rotated the screen)
  splash-rotated.png  180°     (Plymouth boot splash: the screen is
                                mounted upside down and Plymouth draws
                                before anything rotates it)

Usage: python make_splash.py OUTPUT_DIR
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QGuiApplication,
    QImage,
    QPainter,
    QColor,
    QPen,
    QLinearGradient,
    QRadialGradient,
    QFont,
    QTransform,
)

from dashboard.theme import COLORS, make_font


W, H = 1920, 1080

# Progress bar position (Plymouth draws it; kept here so the layout
# lives in one place — see jeep.script)
BAR_Y = 800
BAR_W = 420


def render():
    image = QImage(W, H, QImage.Format_RGB32)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)

    # Background: deep navy with a soft blue glow behind the logo
    bg = QLinearGradient(0, 0, 0, H)
    bg.setColorAt(0, QColor(COLORS["background_top"]))
    bg.setColorAt(1, QColor(COLORS["background"]))
    p.fillRect(0, 0, W, H, bg)

    glow = QRadialGradient(QPointF(W / 2, 470), 560)
    c = QColor(COLORS["accent"])
    c.setAlpha(55)
    glow.setColorAt(0, c)
    glow.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(0, 0, W, H, glow)

    # Seven-slot grille with round headlights
    cx, cy = W / 2, 470
    slot_w, slot_h, gap = 34, 190, 22
    total = 7 * slot_w + 6 * gap

    accent = QColor(COLORS["accent_bright"])
    fill = QColor(COLORS["accent"])
    fill.setAlpha(60)

    p.setPen(QPen(accent, 5))
    p.setBrush(fill)

    for i in range(7):
        x = cx - total / 2 + i * (slot_w + gap)
        p.drawRoundedRect(QRectF(x, cy - slot_h / 2, slot_w, slot_h), slot_w / 2, slot_w / 2)

    for side in (-1, 1):
        hx = cx + side * (total / 2 + 95)
        p.setPen(QPen(accent, 5))
        p.setBrush(fill)
        p.drawEllipse(QPointF(hx, cy), 72, 72)
        p.setPen(QPen(accent, 3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(hx, cy), 44, 44)

    # Hood line above the grille
    p.setPen(QPen(accent, 5, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(cx - total / 2 - 190, cy - slot_h / 2 - 40),
               QPointF(cx + total / 2 + 190, cy - slot_h / 2 - 40))

    # Title
    p.setPen(QColor(COLORS["text"]))
    p.setFont(make_font(64, QFont.ExtraBold, spacing=14))
    p.drawText(QRectF(0, 640, W, 90), Qt.AlignCenter, "JEEP COMPUTER")

    p.setPen(QColor(COLORS["text_secondary"]))
    p.setFont(make_font(22, QFont.Bold, spacing=8))
    p.drawText(QRectF(0, 725, W, 40), Qt.AlignCenter, "CARPLAY  ·  AUDIO  ·  TRAIL")

    p.end()
    return image


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    app = QGuiApplication([])

    image = render()
    image.save(os.path.join(out, "splash.png"))
    image.transformed(QTransform().rotate(180)).save(os.path.join(out, "splash-rotated.png"))

    print("written to", out)


if __name__ == "__main__":
    main()
