from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QLinearGradient

from dashboard.theme import COLORS, make_font


class VolumeOverlay(QWidget):
    """
    Pop-up volume indicator shown for a moment after W / S.
    """

    MIN_DB = -60.0
    MAX_DB = 0.0

    def __init__(self, parent):
        super().__init__(parent)

        self.volume = None
        self.message = ""

        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.resize(560, 170)
        self.hide()

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_volume(self, volume_db, message=""):
        self.volume = volume_db
        self.message = message

        parent = self.parentWidget()
        self.move(
            (parent.width() - self.width()) // 2,
            parent.height() - self.height() - 140,
        )

        self.show()
        self.raise_()
        self.update()
        self.hide_timer.start(1500)

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        bg = QColor(COLORS["card_top"])
        bg.setAlpha(245)
        p.setPen(QPen(QColor(COLORS["accent"]), 2))
        p.setBrush(bg)
        p.drawRoundedRect(rect, 22, 22)

        inner = rect.adjusted(30, 22, -30, -26)

        p.setFont(make_font(16, QFont.Bold, spacing=3))
        p.setPen(QColor(COLORS["text_secondary"]))
        p.drawText(inner, Qt.AlignLeft | Qt.AlignTop, "VOLUME")

        if self.volume is None:
            text = self.message or "CAMILLADSP OFFLINE"
            color = COLORS["critical"]
        else:
            text = self.message or f"{self.volume:.1f} dB"
            color = COLORS["text"]

        p.setFont(make_font(46, QFont.ExtraBold))
        p.setPen(QColor(color))
        p.drawText(inner.adjusted(0, 18, 0, -26), Qt.AlignLeft | Qt.AlignVCenter, text)

        # Level bar
        bar = QRectF(inner.left(), inner.bottom() - 12, inner.width(), 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["border"]))
        p.drawRoundedRect(bar, 6, 6)

        if self.volume is not None:
            span = self.MAX_DB - self.MIN_DB
            fraction = max(0.0, min(1.0, (self.volume - self.MIN_DB) / span))

            fill = QRectF(bar.left(), bar.top(), bar.width() * fraction, bar.height())
            gradient = QLinearGradient(fill.topLeft(), fill.topRight())
            gradient.setColorAt(0, QColor(COLORS["accent_dark"]))
            gradient.setColorAt(1, QColor(COLORS["accent_bright"]))
            p.setBrush(gradient)
            p.drawRoundedRect(fill, 6, 6)

        p.end()
