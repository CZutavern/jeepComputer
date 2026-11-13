from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QLinearGradient,
    QFontMetrics,
    QFont,
)

from dashboard.theme import COLORS, STATUS_COLORS, make_font


class GaugeCard(QWidget):
    """
    Dashboard reading card.

    Uppercase caption top-left, status dot top-right, large value
    with its unit, and an optional level bar along the bottom.

    Give minimum/maximum to show the level bar. With bipolar=True
    the bar grows out from the middle (used for roll / pitch).
    """

    RADIUS = 18

    def __init__(
        self,
        title,
        unit="",
        minimum=None,
        maximum=None,
        bipolar=False,
        value_size=84,
    ):
        super().__init__()

        self.title_text = title.upper()
        self.unit_text = unit
        self.value_text = "--"
        self.status = "normal"

        self.minimum = minimum
        self.maximum = maximum
        self.bipolar = bipolar
        self.level = None

        self.value_size = value_size

        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ---------------------------------------------------------
    # Public API (unchanged from the old card)
    # ---------------------------------------------------------

    def set_value(self, value):
        text = "--" if value is None else str(value)

        # Numeric values also drive the level bar
        if self.minimum is not None and self.maximum is not None:
            try:
                self.level = float(value)
            except (TypeError, ValueError):
                self.level = None

        if text != self.value_text:
            self.value_text = text
            self.update()

    def set_title(self, title):
        """Change the caption (e.g. to carry a second fact)."""
        if title.upper() != self.title_text:
            self.title_text = title.upper()
            self.update()

    def set_status(self, status):
        if status != self.status:
            self.status = status
            self.update()

    # ---------------------------------------------------------
    # Painting
    # ---------------------------------------------------------

    def _status_color(self):
        if self.value_text == "--":
            return QColor(COLORS["muted"])

        return QColor(STATUS_COLORS.get(self.status, COLORS["accent"]))

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        status_color = self._status_color()
        alert = self.status in ("warning", "critical") and self.value_text != "--"

        # Background
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, QColor(COLORS["card_top"]))
        gradient.setColorAt(1.0, QColor(COLORS["card"]))

        border = QColor(status_color) if alert else QColor(COLORS["border"])
        p.setPen(QPen(border, 2 if alert else 1.2))
        p.setBrush(gradient)
        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

        pad = 22
        inner = rect.adjusted(pad, pad - 4, -pad, -pad)

        # Caption
        p.setFont(make_font(15, QFont.Bold, spacing=2))
        p.setPen(QColor(COLORS["text_secondary"]))
        caption_h = 24
        p.drawText(
            QRectF(inner.left(), inner.top(), inner.width() - 24, caption_h),
            Qt.AlignLeft | Qt.AlignVCenter,
            self.title_text,
        )

        # Status dot
        p.setPen(Qt.NoPen)
        p.setBrush(status_color)
        p.drawEllipse(QPointF(inner.right() - 6, inner.top() + caption_h / 2), 6, 6)

        # Level bar area
        has_bar = self.minimum is not None and self.maximum is not None
        bar_h = 8 if has_bar else 0
        bar_gap = 16 if has_bar else 0

        value_area = QRectF(
            inner.left(),
            inner.top() + caption_h,
            inner.width(),
            inner.height() - caption_h - bar_h - bar_gap,
        )

        self._draw_value(p, value_area)

        if has_bar:
            self._draw_bar(
                p,
                QRectF(inner.left(), inner.bottom() - bar_h, inner.width(), bar_h),
                status_color,
            )

        p.end()

    def _draw_value(self, p, area):

        # Shrink long text (e.g. "LOW OIL PRESSURE") to fit the card
        size = min(self.value_size, int(area.height() * 0.8))
        unit_size = max(16, int(size * 0.38))

        unit_font = make_font(unit_size, QFont.DemiBold)
        unit_w = QFontMetrics(unit_font).horizontalAdvance(self.unit_text) + 10 if self.unit_text else 0

        while size > 18:
            value_font = make_font(size, QFont.Bold)
            if QFontMetrics(value_font).horizontalAdvance(self.value_text) + unit_w <= area.width():
                break
            size -= 2

        value_font = make_font(size, QFont.Bold)
        value_metrics = QFontMetrics(value_font)

        # Missing readings show as a single dash
        text = "—" if self.value_text == "--" else self.value_text
        value_w = value_metrics.horizontalAdvance(text)

        # Value and unit share a baseline, left-aligned
        baseline = area.center().y() + value_metrics.capHeight() / 2

        color = COLORS["muted"] if self.value_text == "--" else COLORS["text"]
        p.setPen(QColor(color))
        p.setFont(value_font)
        p.drawText(QPointF(area.left(), baseline), text)

        if self.unit_text:
            p.setPen(QColor(COLORS["text_secondary"]))
            p.setFont(unit_font)
            p.drawText(QPointF(area.left() + value_w + 10, baseline), self.unit_text)

    def _draw_bar(self, p, bar, color):

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["border"]))
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)

        if self.level is None:
            return

        span = self.maximum - self.minimum
        if span <= 0:
            return

        if self.bipolar:
            middle = bar.left() + bar.width() / 2
            fraction = max(-1.0, min(1.0, self.level / (span / 2)))
            width = abs(fraction) * bar.width() / 2
            left = middle if fraction >= 0 else middle - width
            fill = QRectF(left, bar.top(), width, bar.height())

            # Centre marker
            p.setBrush(QColor(COLORS["text_secondary"]))
            p.drawRect(QRectF(middle - 1, bar.top() - 3, 2, bar.height() + 6))
        else:
            fraction = max(0.0, min(1.0, (self.level - self.minimum) / span))
            fill = QRectF(bar.left(), bar.top(), bar.width() * fraction, bar.height())

        if fill.width() <= 0:
            return

        gradient = QLinearGradient(fill.topLeft(), fill.topRight())
        start = QColor(color)
        start.setAlpha(150)
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, color)

        p.setBrush(gradient)
        p.drawRoundedRect(fill, bar.height() / 2, bar.height() / 2)
