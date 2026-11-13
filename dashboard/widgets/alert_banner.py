from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QLinearGradient

from dashboard.theme import COLORS, STATUS_COLORS, make_font


class AlertBanner(QWidget):
    """
    Full-width alert strip shown under the top bar on every page.

    Warnings can be tapped away (they come back if the message
    changes); critical alerts stay until the condition clears.
    """

    HEIGHT = 76

    def __init__(self):
        super().__init__()

        self.level = None
        self.title = ""
        self.detail = ""
        self.dismissed = None

        self.setFixedHeight(self.HEIGHT)
        self.hide()

    def show_alert(self, level, title, detail=""):
        """level: "warning" or "critical"; None clears the banner."""

        if level is None:
            self.level = None
            self.dismissed = None
            self.hide()
            return

        key = (level, title)

        if level == "warning" and key == self.dismissed:
            self.hide()
            return

        changed = (level, title, detail) != (self.level, self.title, self.detail)
        self.level, self.title, self.detail = level, title, detail

        self.show()
        if changed:
            self.update()

    def mousePressEvent(self, event):
        if self.level == "warning":
            self.dismissed = (self.level, self.title)
            self.hide()

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        color = QColor(STATUS_COLORS.get(self.level, COLORS["warning"]))
        rect = QRectF(self.rect())

        # Navy base with a tint of the alert colour from the left
        p.fillRect(rect, QColor(COLORS["top_bar"]))
        tint = QLinearGradient(rect.topLeft(), rect.topRight())
        strong = QColor(color)
        strong.setAlpha(90)
        faint = QColor(color)
        faint.setAlpha(20)
        tint.setColorAt(0, strong)
        tint.setColorAt(0.6, faint)
        tint.setColorAt(1, faint)
        p.fillRect(rect, tint)

        p.fillRect(QRectF(0, rect.bottom() - 2, rect.width(), 2), color)

        # Warning icon
        cx, cy = 52, rect.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), 22, 22)
        p.setPen(QPen(QColor(COLORS["background"]), 5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy - 11), QPointF(cx, cy + 3))
        p.drawPoint(QPointF(cx, cy + 11))

        p.setPen(QColor(COLORS["text"]))
        p.setFont(make_font(26, QFont.ExtraBold, spacing=1))
        p.drawText(rect.adjusted(92, 0, -40, 0), Qt.AlignLeft | Qt.AlignVCenter, self.title)

        right = self.detail
        if self.level == "warning":
            right = f"{right}   ·   TAP TO DISMISS" if right else "TAP TO DISMISS"

        p.setPen(QColor(COLORS["text_secondary"]))
        p.setFont(make_font(18, QFont.Bold, spacing=2))
        p.drawText(rect.adjusted(92, 0, -32, 0), Qt.AlignRight | Qt.AlignVCenter, right.upper())

        p.end()
