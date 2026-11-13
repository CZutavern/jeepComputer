from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QLinearGradient

from dashboard.theme import COLORS, make_font


class PageHeader(QWidget):
    """
    Page title with a short blue accent bar, and an optional
    status line on the right (e.g. "6 OF 7 SENSORS ONLINE").
    """

    HEIGHT = 64

    def __init__(self, title, subtitle=""):
        super().__init__()

        self.title = title.upper()
        self.subtitle = subtitle.upper()
        self.subtitle_color = COLORS["text_secondary"]

        self.setFixedHeight(self.HEIGHT)

    def set_subtitle(self, text, color=None):
        text = text.upper()
        color = color or COLORS["text_secondary"]

        if text != self.subtitle or color != self.subtitle_color:
            self.subtitle = text
            self.subtitle_color = color
            self.update()

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect())

        # Accent bar
        bar = QRectF(4, rect.center().y() - 16, 6, 32)
        gradient = QLinearGradient(bar.topLeft(), bar.bottomLeft())
        gradient.setColorAt(0, QColor(COLORS["accent_bright"]))
        gradient.setColorAt(1, QColor(COLORS["accent_dark"]))
        p.setPen(Qt.NoPen)
        p.setBrush(gradient)
        p.drawRoundedRect(bar, 3, 3)

        # Title
        title_font = make_font(30, QFont.ExtraBold, spacing=3)
        p.setFont(title_font)
        p.setPen(QColor(COLORS["text"]))
        p.drawText(
            QRectF(26, 0, rect.width() / 2, rect.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            self.title,
        )

        # Subtitle / status on the right, with a leading dot
        if self.subtitle:
            sub_font = make_font(16, QFont.Bold, spacing=2)
            p.setFont(sub_font)

            text_w = QFontMetrics(sub_font).horizontalAdvance(self.subtitle)
            right = rect.width() - 8

            p.setPen(QColor(self.subtitle_color))
            p.drawText(
                QRectF(right - text_w, 0, text_w, rect.height()),
                Qt.AlignRight | Qt.AlignVCenter,
                self.subtitle,
            )

            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.subtitle_color))
            p.drawEllipse(QRectF(right - text_w - 22, rect.center().y() - 5, 10, 10))

        # Hairline under the header
        p.setPen(QColor(COLORS["border"]))
        p.drawLine(0, int(rect.bottom()), int(rect.width()), int(rect.bottom()))

        p.end()
