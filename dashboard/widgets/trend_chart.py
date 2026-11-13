"""
Time-series chart for the weather page: filled history line, min/max
markers, the latest reading, and the 3-hour trend projected ahead.
"""

import math
import time

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter,
    QPainterPath,
    QColor,
    QPen,
    QFont,
    QLinearGradient,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget, QSizePolicy

from dashboard.theme import COLORS, make_font
from dashboard.widgets.compass import draw_card_background


GAP_S = 20 * 60          # break the line across gaps longer than this
PROJECT_S = 2 * 3600     # how far ahead the trend line is drawn
TREND_WINDOW_S = 3 * 3600


def format_rate(rate, decimals):
    """+1.3, -0.6, -3.9k: compact rate-of-change text."""
    if abs(rate) >= 1000:
        return f"{rate / 1000:+.1f}k"
    return f"{rate:+.{max(1, decimals)}f}"


def nice_step(span, target_lines=4):
    """A round grid step (1, 2, 2.5, 5 x 10^n) giving ~target_lines lines."""
    if span <= 0:
        return 1.0
    raw = span / target_lines
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


class TrendChart(QWidget):

    def __init__(self):
        super().__init__()

        self.points = []
        self.title = ""
        self.unit = ""
        self.decimals = 1
        self.slope_h = None
        self.color = QColor(COLORS["accent_bright"])
        self.range_s = 24 * 3600
        self.min_span = 1.0
        self.project = True
        self.now = time.time()

        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, title, metric, range_s, color=None, min_span=1.0, project=True):
        """
        metric: one entry of WeatherSummary.metrics (points, unit,
        decimals, slope_h). min_span: smallest y range shown, so a flat
        line doesn't get blown up into noise. project=False ends the
        chart at "now" instead of leaving room for the trend projection.
        """
        self.title = title.upper()
        self.points = (metric or {}).get("points") or []
        self.unit = (metric or {}).get("unit", "")
        self.decimals = (metric or {}).get("decimals", 1)
        self.slope_h = (metric or {}).get("slope_h")
        self.range_s = range_s
        self.min_span = min_span
        self.project = project
        self.now = time.time()
        if color:
            self.color = QColor(color)
        self.update()

    def fmt(self, v):
        return f"{v:.{self.decimals}f}"

    # ---------------------------------------------------------

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        draw_card_background(p, rect)

        p.setFont(make_font(15, QFont.Bold, spacing=2))
        p.setPen(QColor(COLORS["text_secondary"]))
        p.drawText(rect.adjusted(22, 16, 0, 0), Qt.AlignLeft | Qt.AlignTop, self.title)

        plot = rect.adjusted(24, 76, -92, -44)

        start = self.now - self.range_s
        end = self.now + (PROJECT_S if self.project and self.range_s <= 24 * 3600 else 0)
        pts = [(t, v) for t, v in self.points if t >= start]

        if len(pts) < 2:
            p.setFont(make_font(22, QFont.DemiBold))
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(plot, Qt.AlignCenter, "Collecting data…  trends appear after about 30 minutes")
            p.end()
            return

        # Trend line (last 3 h, extended to `end`)
        trend = None
        if self.slope_h is not None:
            anchor_t, anchor_v = pts[-1]
            trend = (anchor_t, anchor_v, self.slope_h / 3600.0)

        values = [v for _, v in pts]
        lo, hi = min(values), max(values)
        if trend:
            t0, v0, s = trend
            for t in (max(start, t0 - TREND_WINDOW_S), end):
                v = v0 + s * (t - t0)
                lo, hi = min(lo, v), max(hi, v)

        if hi - lo < self.min_span:
            mid = (hi + lo) / 2
            lo, hi = mid - self.min_span / 2, mid + self.min_span / 2
        pad = (hi - lo) * 0.12
        lo, hi = lo - pad, hi + pad

        def x_of(t):
            return plot.left() + (t - start) / (end - start) * plot.width()

        def y_of(v):
            return plot.bottom() - (v - lo) / (hi - lo) * plot.height()

        # Grid + y labels
        step = nice_step(hi - lo)
        p.setFont(make_font(14, QFont.Bold))
        v = math.ceil(lo / step) * step
        while v <= hi:
            y = y_of(v)
            p.setPen(QPen(QColor(COLORS["border"]), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(COLORS["muted"]))
            label = f"{v:.{max(0, -int(math.floor(math.log10(step))))}f}" if step < 1 else f"{v:.0f}"
            p.drawText(QRectF(plot.right() + 10, y - 11, 80, 22), Qt.AlignLeft | Qt.AlignVCenter, label)
            v += step

        # Time labels
        hours = self.range_s / 3600
        tick_h = 1 if hours <= 3 else (4 if hours <= 24 else 12)
        first = math.ceil(start / (tick_h * 3600)) * tick_h * 3600
        t = first
        while t <= end:
            x = x_of(t)
            p.setPen(QPen(QColor(COLORS["border"]), 1, Qt.DotLine))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(QColor(COLORS["muted"]))
            lt = time.localtime(t)
            text = time.strftime("%a %-I%p" if hours > 24 else "%-I:%M%p", lt).lower()
            p.drawText(QRectF(x - 60, plot.bottom() + 8, 120, 22), Qt.AlignCenter, text)
            t += tick_h * 3600

        # "Now" marker
        xn = x_of(self.now)
        p.setPen(QPen(QColor(COLORS["border_bright"]), 1.5, Qt.DashLine))
        p.drawLine(QPointF(xn, plot.top()), QPointF(xn, plot.bottom()))

        # History: filled area + line, broken across gaps
        segments, seg = [], [pts[0]]
        for a, b in zip(pts, pts[1:]):
            if b[0] - a[0] > GAP_S:
                segments.append(seg)
                seg = []
            seg.append(b)
        segments.append(seg)

        fill = QLinearGradient(QPointF(0, plot.top()), QPointF(0, plot.bottom()))
        c1 = QColor(self.color)
        c1.setAlpha(90)
        c2 = QColor(self.color)
        c2.setAlpha(0)
        fill.setColorAt(0, c1)
        fill.setColorAt(1, c2)

        for seg in segments:
            if len(seg) < 2:
                continue
            line = QPainterPath(QPointF(x_of(seg[0][0]), y_of(seg[0][1])))
            for t, v in seg[1:]:
                line.lineTo(QPointF(x_of(t), y_of(v)))

            # Area under the curve, built explicitly down to the axis
            area = QPainterPath(QPointF(x_of(seg[0][0]), plot.bottom()))
            for t, v in seg:
                area.lineTo(QPointF(x_of(t), y_of(v)))
            area.lineTo(QPointF(x_of(seg[-1][0]), plot.bottom()))
            area.closeSubpath()
            p.fillPath(area, fill)

            # No brush: drawPath would otherwise fill the open line along its chord
            p.setPen(QPen(self.color, 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(line)

        # Min / max markers (over the shown range)
        (t_min, v_min) = min(pts, key=lambda tv: tv[1])
        (t_max, v_max) = max(pts, key=lambda tv: tv[1])
        p.setFont(make_font(14, QFont.Bold))
        for (t, v), label, dy in (((t_max, v_max), "HIGH", -30), ((t_min, v_min), "LOW", 8)):
            x, y = x_of(t), y_of(v)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS["text_secondary"]))
            p.drawEllipse(QPointF(x, y), 4, 4)
            p.setPen(QColor(COLORS["text_secondary"]))
            box = QRectF(x - 70, y + dy, 140, 22)
            box.moveLeft(max(plot.left(), min(box.left(), plot.right() - 140)))
            p.drawText(box, Qt.AlignCenter, f"{label} {self.fmt(v)}")

        # Trend projection
        if trend:
            t0, v0, s = trend
            ta = max(start, t0 - TREND_WINDOW_S)
            pa = QPointF(x_of(ta), y_of(v0 + s * (ta - t0)))
            pb = QPointF(x_of(end), y_of(v0 + s * (end - t0)))
            tc = QColor(COLORS["warning"] if self.slope_h < 0 and self.title == "PRESSURE" else COLORS["text"])
            tc.setAlpha(200)
            p.setPen(QPen(tc, 2, Qt.DashLine, Qt.RoundCap))
            p.drawLine(pa, pb)

            p.setFont(make_font(15, QFont.ExtraBold))
            p.setPen(tc)
            p.drawText(QRectF(pb.x() - 220, pb.y() - 30, 216, 24), Qt.AlignRight | Qt.AlignVCenter,
                       f"{format_rate(self.slope_h, self.decimals)} {self.unit}/h")

        # Latest reading
        tl, vl = pts[-1]
        xl, yl = x_of(tl), y_of(vl)
        glow = QRadialGradient(QPointF(xl, yl), 18)
        g = QColor(self.color)
        g.setAlpha(120)
        glow.setColorAt(0, g)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(xl, yl), 18, 18)
        p.setBrush(self.color)
        p.drawEllipse(QPointF(xl, yl), 6, 6)

        p.end()
