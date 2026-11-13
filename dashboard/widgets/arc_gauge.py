"""
Animated round gauge: a 240° dial with coloured zones, labelled ticks, a
filled value arc and a needle. Values glide to their new position.
"""

import math

from PySide6.QtCore import Qt, QRectF, QPointF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QRadialGradient
from PySide6.QtWidgets import QWidget, QSizePolicy

from dashboard.theme import COLORS, STATUS_COLORS, make_font
from dashboard.widgets.compass import draw_card_background


START_DEG = 210.0    # left end of the dial (math angle, CCW from +x)
SWEEP_DEG = 240.0    # clockwise sweep to the right end


class ArcGauge(QWidget):

    def __init__(
        self,
        title,
        unit="",
        minimum=0.0,
        maximum=100.0,
        zones=None,
        ticks=None,
        decimals=0,
        formatter=None,
    ):
        """
        zones:     [(start, end, status)], e.g. [(220, 235, "warning")]
        ticks:     [(value, label)]; label may be "" for a minor tick
        formatter: value -> text for the centre readout
        """
        super().__init__()

        self.title = title.upper()
        self.unit = unit
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.zones = zones or []
        self.ticks = ticks or []
        self.decimals = decimals
        self.formatter = formatter

        self.target = None
        self.shown = self.minimum
        self.present = 0.0          # 0..1 fade for needle / arc
        self.status = "normal"
        self.text_override = None

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(700)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._on_anim)

        self.fade = QVariantAnimation(self)
        self.fade.setDuration(400)
        self.fade.valueChanged.connect(self._on_fade)

        self.setMinimumSize(280, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ---------------------------------------------------------

    def set_value(self, value, text=None):
        """value: number or None (no reading). text: optional readout override."""

        self.text_override = text

        if value is None:
            if self.target is not None:
                self.target = None
                self._fade_to(0.0)
            self.update()
            return

        value = max(self.minimum, min(self.maximum, float(value)))
        first = self.target is None
        self.target = value

        if first:
            self._fade_to(1.0)

        if abs(value - self.shown) > 1e-6:
            self.anim.stop()
            self.anim.setStartValue(float(self.shown))
            self.anim.setEndValue(value)
            self.anim.start()
        else:
            self.update()

    def set_status(self, status):
        if status != self.status:
            self.status = status
            self.update()

    def _fade_to(self, end):
        self.fade.stop()
        self.fade.setStartValue(float(self.present))
        self.fade.setEndValue(float(end))
        self.fade.start()

    def _on_anim(self, v):
        self.shown = float(v)
        self.update()

    def _on_fade(self, v):
        self.present = float(v)
        self.update()

    # ---------------------------------------------------------

    def fraction(self, value):
        span = self.maximum - self.minimum
        return 0.0 if span <= 0 else (value - self.minimum) / span

    def angle(self, value):
        return START_DEG - SWEEP_DEG * max(0.0, min(1.0, self.fraction(value)))

    @staticmethod
    def point(cx, cy, r, deg):
        a = math.radians(deg)
        return QPointF(cx + r * math.cos(a), cy - r * math.sin(a))

    def readout(self):
        if self.text_override is not None:
            return self.text_override
        if self.target is None:
            return "—"
        if self.formatter:
            return self.formatter(self.shown)
        return f"{self.shown:.{self.decimals}f}"

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        draw_card_background(p, rect)

        color = QColor(STATUS_COLORS.get(self.status, COLORS["accent"]))
        if self.target is None:
            color = QColor(COLORS["muted"])

        # Caption + status dot
        p.setFont(make_font(15, QFont.Bold, spacing=2))
        p.setPen(QColor(COLORS["text_secondary"]))
        p.drawText(rect.adjusted(22, 16, -40, 0), Qt.AlignLeft | Qt.AlignTop, self.title)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(rect.right() - 28, rect.top() + 27), 6, 6)

        # Dial geometry: the arc's lowest points sit at sin(30°) = 0.5 r
        top = rect.top() + 48
        avail_h = rect.bottom() - top - 12
        # Height needed: 1.06 r above the centre, 0.86 r below (readout + unit)
        r = min(rect.width() * 0.40, avail_h / 1.92)
        cx = rect.center().x()
        cy = top + r * 1.06
        width = max(10.0, r * 0.11)

        arc_rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)

        # Soft glow behind the dial
        glow = QRadialGradient(QPointF(cx, cy), r * 1.1)
        g = QColor(color)
        g.setAlpha(int(34 * self.present))
        glow.setColorAt(0, g)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), r * 1.1, r * 1.1)

        # Track
        pen = QPen(QColor(COLORS["border"]), width, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawArc(arc_rect, int(START_DEG * 16), int(-SWEEP_DEG * 16))

        # Zones: thin coloured band just inside the track
        zone_r = r - width * 0.95
        zone_rect = QRectF(cx - zone_r, cy - zone_r, 2 * zone_r, 2 * zone_r)
        for start, end, status in self.zones:
            zc = QColor(STATUS_COLORS.get(status, COLORS["accent"]))
            zc.setAlpha(170)
            p.setPen(QPen(zc, max(3.0, width * 0.28), Qt.SolidLine, Qt.FlatCap))
            a0, a1 = self.angle(start), self.angle(end)
            p.drawArc(zone_rect, int(a0 * 16), int((a1 - a0) * 16))

        # Value arc in the status colour
        if self.present > 0.01:
            a_val = self.angle(self.shown)
            vc = QColor(color)
            vc.setAlpha(int(255 * self.present))
            p.setPen(QPen(vc, width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc_rect, int(START_DEG * 16), int((a_val - START_DEG) * 16))

        # Ticks and labels
        label_font = make_font(max(12, int(r * 0.11)), QFont.Bold)
        p.setFont(label_font)
        for value, label in self.ticks:
            a = self.angle(value)
            outer = r + width * 0.5 + 4
            inner = outer + (10 if label else 6)
            p.setPen(QPen(QColor(COLORS["text_secondary"] if label else COLORS["muted"]),
                          2.5 if label else 1.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(self.point(cx, cy, outer, a), self.point(cx, cy, inner, a))

            if label:
                lp = self.point(cx, cy, r - width * 1.9, a)
                box = QRectF(lp.x() - 34, lp.y() - 13, 68, 26)
                p.setPen(QColor(COLORS["text_secondary"]))
                p.drawText(box, Qt.AlignCenter, label)

        # Needle
        if self.present > 0.01:
            a = self.angle(self.shown)
            tip = self.point(cx, cy, r - width * 0.2, a)
            base = self.point(cx, cy, r * 0.18, a)
            nc = QColor(COLORS["text"])
            nc.setAlpha(int(255 * self.present))
            p.setPen(QPen(nc, max(3.0, r * 0.03), Qt.SolidLine, Qt.RoundCap))
            p.drawLine(base, tip)

            p.setPen(Qt.NoPen)
            hub = QColor(color)
            hub.setAlpha(int(255 * self.present))
            p.setBrush(hub)
            p.drawEllipse(QPointF(cx, cy), r * 0.085, r * 0.085)
            p.setBrush(QColor(COLORS["card"]))
            p.drawEllipse(QPointF(cx, cy), r * 0.04, r * 0.04)

        # Readout in the dial's open bottom
        value_font = make_font(max(22, int(r * 0.30)), QFont.ExtraBold)
        p.setFont(value_font)
        p.setPen(QColor(COLORS["text"] if self.target is not None else COLORS["muted"]))
        p.drawText(QRectF(cx - r, cy + r * 0.22, 2 * r, r * 0.42), Qt.AlignCenter, self.readout())

        if self.unit:
            p.setFont(make_font(max(13, int(r * 0.12)), QFont.Bold, spacing=2))
            p.setPen(QColor(COLORS["text_secondary"]))
            p.drawText(QRectF(cx - r, cy + r * 0.62, 2 * r, r * 0.2), Qt.AlignCenter, self.unit.upper())

        p.end()
