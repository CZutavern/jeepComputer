import math
import time

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QPen

from dashboard.theme import COLORS, make_font
from dashboard.widgets.icons import draw_icon


class CarPlayPage(QWidget):
    """
    Behind the CarPlay area. In the shell, LIVI only appears once a phone is
    streaming, so until then this is what the CarPlay tab shows: "Starting
    CarPlay" with a slow pulse, so it never looks frozen.
    """

    PULSE_S = 2.4
    FRAME_MS = 83               # 12 fps: the pulse is slow, more is wasted CPU
    ANIMATE_FOR_S = 90          # then hold still (the text still updates)

    def __init__(self):
        super().__init__()

        self.message = "Starting CarPlay…"
        self.detail = "Connecting to your iPhone"
        self.busy = True

        self.anim = QTimer(self)
        self.anim.setInterval(self.FRAME_MS)
        self.anim.timeout.connect(self.tick)
        self.anim_started = time.monotonic()

    def set_state(self, message, detail="", busy=True):
        if (message, detail, busy) != (self.message, self.detail, self.busy):
            self.message, self.detail, self.busy = message, detail, busy
            self.restart_animation()
            self.update()

    def restart_animation(self):
        self.anim_started = time.monotonic()
        if self.isVisible() and self.busy:
            self.anim.start()

    def animating(self):
        return self.busy and time.monotonic() - self.anim_started < self.ANIMATE_FOR_S

    def tick(self):
        if not self.animating():
            self.anim.stop()
        # Only the rings change: repainting the whole page at frame rate cost
        # a third of a core (and made VNC re-encode the screen constantly)
        cx, cy = self.width() / 2, self.height() / 2 - 60
        self.update(int(cx - 210), int(cy - 210), 420, 420)

    def showEvent(self, event):
        self.restart_animation()

    def hideEvent(self, event):
        self.anim.stop()

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        cx = self.width() / 2
        cy = self.height() / 2 - 60
        center = QPointF(cx, cy)

        # Expanding rings while connecting (frozen once it stops animating)
        if self.busy:
            now = time.monotonic() if self.animating() else self.anim_started + self.ANIMATE_FOR_S
            phase = (now % self.PULSE_S) / self.PULSE_S
            for k in range(3):
                t = (phase + k / 3) % 1.0
                ring = QColor(COLORS["accent"])
                ring.setAlphaF(0.35 * (1 - t))
                p.setPen(QPen(ring, 3))
                p.setBrush(Qt.NoBrush)
                r = 95 + 110 * t
                p.drawEllipse(center, r, r)

        disc = QColor(COLORS["accent"])
        disc.setAlpha(45)
        p.setPen(Qt.NoPen)
        p.setBrush(disc)
        p.drawEllipse(center, 90, 90)

        glow = 0.75 + 0.25 * math.sin(time.monotonic() * 2.2) if self.animating() else 1.0
        icon = QColor(COLORS["accent_bright"])
        icon.setAlphaF(glow)
        draw_icon(p, "carplay", QRectF(cx - 50, cy - 50, 100, 100), icon, width=4)

        p.setPen(QColor(COLORS["text"]))
        p.setFont(make_font(40, QFont.ExtraBold, spacing=2))
        p.drawText(QRectF(0, cy + 130, self.width(), 60), Qt.AlignCenter, self.message)

        p.setPen(QColor(COLORS["text_secondary"]))
        p.setFont(make_font(22, QFont.DemiBold))
        p.drawText(QRectF(0, cy + 195, self.width(), 40), Qt.AlignCenter, self.detail)

        p.end()
