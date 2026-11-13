"""
Weather page pieces: tappable metric cards with a trend arrow and
sparkline, and a painted forecast icon.
"""

import math
import time

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPainterPath, QColor, QPen, QFont, QRadialGradient
from PySide6.QtWidgets import QWidget, QSizePolicy

from dashboard.theme import COLORS, make_font
from dashboard.widgets.compass import draw_card_background
from dashboard.widgets.trend_chart import format_rate


def since_text(ts):
    """'9:42 PM' today, 'Wed 9:42 PM' otherwise."""
    t = time.localtime(ts)
    clock = time.strftime("%-I:%M %p", t)
    return clock if t[:3] == time.localtime()[:3] else time.strftime("%a ", t) + clock


class MetricCard(QWidget):
    """Current value, trend arrow + rate, and a 6-hour sparkline. Tap to chart it."""

    clicked = Signal(str)

    SPARK_S = 6 * 3600

    def __init__(self, key, title, color, steady_rate):
        """steady_rate: |rate per hour| below which the trend reads 'steady'."""
        super().__init__()

        self.key = key
        self.title = title.upper()
        self.color = QColor(color)
        self.steady_rate = steady_rate
        self.values = {}
        self.selected = False

        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_metric(self, metric):
        self.values = metric or {}
        self.update()

    def set_selected(self, selected):
        if selected != self.selected:
            self.selected = selected
            self.update()

    def mousePressEvent(self, event):
        self.clicked.emit(self.key)

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        draw_card_background(p, rect)

        if self.selected:
            p.setPen(QPen(self.color, 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 18, 18)

        m = self.values
        value = m.get("now")
        unit = m.get("unit", "")
        decimals = m.get("decimals", 1)
        slope = m.get("slope_h")

        # Sparkline (drawn first, faint, along the bottom)
        pts = [(t, v) for t, v in (m.get("points") or []) if time.time() - t <= self.SPARK_S]
        spark = rect.adjusted(18, rect.height() * 0.58, -18, -14)
        if len(pts) >= 2:
            vs = [v for _, v in pts]
            lo, hi = min(vs), max(vs)
            span = max(hi - lo, 1e-6)
            t0, t1 = pts[0][0], pts[-1][0]
            tspan = max(t1 - t0, 1)
            path = QPainterPath()
            for i, (t, v) in enumerate(pts):
                pt = QPointF(spark.left() + (t - t0) / tspan * spark.width(),
                             spark.bottom() - (v - lo) / span * spark.height())
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            c = QColor(self.color)
            c.setAlpha(150)
            p.setPen(QPen(c, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        # Caption
        p.setFont(make_font(14, QFont.Bold, spacing=2))
        p.setPen(QColor(COLORS["text_secondary"]))
        p.drawText(rect.adjusted(18, 12, -10, 0), Qt.AlignLeft | Qt.AlignTop, self.title)

        # Value
        text = "—" if value is None else f"{value:,.{decimals}f}"
        p.setFont(make_font(34, QFont.ExtraBold))
        p.setPen(QColor(COLORS["text"] if value is not None else COLORS["muted"]))
        p.drawText(rect.adjusted(18, 34, -10, 0), Qt.AlignLeft | Qt.AlignTop, f"{text}")

        value_w = p.fontMetrics().horizontalAdvance(text)
        p.setFont(make_font(16, QFont.Bold))
        p.setPen(QColor(COLORS["text_secondary"]))
        if value is not None:
            p.drawText(QRectF(rect.left() + 18 + value_w + 6, rect.top() + 50, 60, 24),
                       Qt.AlignLeft | Qt.AlignVCenter, unit)
        elif m.get("last_ts"):
            # Sensor gone quiet: say when it was last seen instead of a stale value
            p.setFont(make_font(13, QFont.Bold))
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(QRectF(rect.left() + 18 + value_w + 10, rect.top() + 42, rect.width() - value_w - 40, 36),
                       Qt.AlignLeft | Qt.AlignVCenter, "no reading since\n" + since_text(m["last_ts"]))

        # Trend arrow + rate, top right
        if slope is not None:
            if abs(slope) < self.steady_rate:
                arrow, tc = "→", COLORS["text_secondary"]
            elif slope > 0:
                arrow, tc = "↑", COLORS["good"]
            else:
                arrow, tc = "↓", COLORS["warning"]

            p.setFont(make_font(15, QFont.ExtraBold))
            p.setPen(QColor(tc))
            rate = f"{arrow} {format_rate(slope, decimals)}/h"
            p.drawText(rect.adjusted(0, 12, -16, 0), Qt.AlignRight | Qt.AlignTop, rate)

        p.end()


class WeatherIcon(QWidget):
    """Forecast icon: sun / moon, cloud, showers, rain, thunderstorm."""

    def __init__(self):
        super().__init__()
        self.kind = "unknown"
        self.night = False
        self.setMinimumSize(150, 130)

    def set_forecast(self, index, night):
        if index is None:
            kind = "unknown"
        elif index <= 2:
            kind = "clear"
        elif index <= 8:
            kind = "partly"
        elif index <= 16:
            kind = "showers"
        elif index <= 23:
            kind = "rain"
        else:
            kind = "storm"

        if (kind, night) != (self.kind, self.night):
            self.kind, self.night = kind, night
            self.update()

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        s = min(self.width(), self.height())
        cx, cy = self.width() / 2, self.height() / 2
        u = s / 10.0   # drawing unit

        def sun(x, y, r):
            glow = QRadialGradient(QPointF(x, y), r * 2.2)
            glow.setColorAt(0, QColor(255, 196, 64, 110))
            glow.setColorAt(1, QColor(255, 196, 64, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(x, y), r * 2.2, r * 2.2)
            p.setBrush(QColor("#ffc440"))
            p.drawEllipse(QPointF(x, y), r, r)
            p.setPen(QPen(QColor("#ffc440"), u * 0.35, Qt.SolidLine, Qt.RoundCap))
            for i in range(8):
                a = math.radians(i * 45)
                p.drawLine(QPointF(x + math.cos(a) * r * 1.35, y + math.sin(a) * r * 1.35),
                           QPointF(x + math.cos(a) * r * 1.75, y + math.sin(a) * r * 1.75))

        def moon(x, y, r):
            p.setPen(Qt.NoPen)
            path = QPainterPath()
            path.addEllipse(QPointF(x, y), r, r)
            cut = QPainterPath()
            cut.addEllipse(QPointF(x + r * 0.55, y - r * 0.35), r * 0.9, r * 0.9)
            p.setBrush(QColor(COLORS["accent_bright"]))
            p.drawPath(path.subtracted(cut))

        def cloud(x, y, w, color):
            path = QPainterPath()
            h = w * 0.42
            path.addRoundedRect(QRectF(x - w / 2, y, w, h), h / 2, h / 2)
            path.addEllipse(QPointF(x - w * 0.16, y + h * 0.1), w * 0.24, w * 0.24)
            path.addEllipse(QPointF(x + w * 0.14, y - h * 0.05), w * 0.3, w * 0.3)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawPath(path.simplified())

        def drops(x, y, w, count, color):
            p.setPen(QPen(QColor(color), u * 0.35, Qt.SolidLine, Qt.RoundCap))
            for i in range(count):
                dx = x - w / 2 + (i + 0.5) * w / count
                p.drawLine(QPointF(dx, y), QPointF(dx - u * 0.5, y + u * 1.3))

        def bolt(x, y):
            path = QPainterPath(QPointF(x + u * 0.4, y))
            for px, py in ((x - u * 0.8, y + u * 1.6), (x, y + u * 1.6), (x - u * 0.5, y + u * 3.0),
                           (x + u * 1.0, y + u * 1.1), (x + u * 0.1, y + u * 1.1)):
                path.lineTo(QPointF(px, py))
            path.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffd23f"))
            p.drawPath(path)

        light = COLORS["text"]
        dark = "#8ea4c8"

        if self.kind == "clear":
            (moon if self.night else sun)(cx, cy, u * 2.2)
        elif self.kind == "partly":
            (moon if self.night else sun)(cx - u * 1.3, cy - u * 1.3, u * 1.6)
            cloud(cx + u * 0.6, cy - u * 0.2, u * 5.2, light)
        elif self.kind == "showers":
            (moon if self.night else sun)(cx - u * 1.6, cy - u * 1.8, u * 1.3)
            cloud(cx + u * 0.4, cy - u * 1.2, u * 5.4, light)
            drops(cx + u * 0.4, cy + u * 1.6, u * 3.2, 3, COLORS["accent_bright"])
        elif self.kind == "rain":
            cloud(cx, cy - u * 1.6, u * 6.4, dark)
            drops(cx, cy + u * 1.4, u * 4.8, 5, COLORS["accent_bright"])
        elif self.kind == "storm":
            cloud(cx, cy - u * 1.8, u * 6.6, "#5b6f94")
            drops(cx - u * 0.8, cy + u * 1.2, u * 3.0, 3, COLORS["accent_bright"])
            bolt(cx + u * 1.2, cy + u * 0.6)
        else:
            p.setFont(make_font(int(u * 3), QFont.ExtraBold))
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "?")

        p.end()
