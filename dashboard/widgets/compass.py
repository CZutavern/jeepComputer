import math

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter,
    QPen,
    QColor,
    QFont,
    QPolygonF,
    QRadialGradient,
    QLinearGradient,
)

from dashboard.theme import COLORS, make_font


CARDINALS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def cardinal(heading):
    return CARDINALS[int((heading % 360) / 45 + 0.5) % 8]


def draw_card_background(p, rect, radius=18):
    """
    Same card look as GaugeCard, for custom-painted panels.
    """

    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, QColor(COLORS["card_top"]))
    gradient.setColorAt(1.0, QColor(COLORS["card"]))

    p.setPen(QPen(QColor(COLORS["border"]), 1.2))
    p.setBrush(gradient)
    p.drawRoundedRect(rect, radius, radius)


class Compass(QWidget):
    """
    Rotating compass rose with a fixed pointer at the top and the
    heading readout in the centre.
    """

    def __init__(self):
        super().__init__()

        self.heading = None
        self.setMinimumSize(300, 300)

    def set_heading(self, heading):
        heading = None if heading is None else float(heading) % 360

        if heading != self.heading:
            self.heading = heading
            self.update()

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        draw_card_background(p, rect)

        # Caption
        p.setFont(make_font(15, QFont.Bold, spacing=2))
        p.setPen(QColor(COLORS["text_secondary"]))
        p.drawText(rect.adjusted(22, 18, 0, 0), Qt.AlignLeft | Qt.AlignTop, "HEADING")

        cx = rect.center().x()
        cy = rect.center().y() + 14
        radius = min(rect.width(), rect.height() - 60) / 2 - 14

        heading = self.heading or 0.0

        # Dial face
        face = QRadialGradient(QPointF(cx, cy), radius)
        face.setColorAt(0.0, QColor(COLORS["card_alt"]))
        face.setColorAt(1.0, QColor(COLORS["background_top"]))
        p.setPen(QPen(QColor(COLORS["border_bright"]), 2))
        p.setBrush(face)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Ticks (rotate with the vehicle)
        for deg in range(0, 360, 5):
            a = math.radians(deg - heading - 90)

            if deg % 45 == 0:
                length, width, color = 0.14, 3, COLORS["text"]
            elif deg % 15 == 0:
                length, width, color = 0.09, 2, COLORS["text_secondary"]
            else:
                length, width, color = 0.05, 1, COLORS["muted"]

            outer = radius * 0.96
            inner = radius * (0.96 - length)

            p.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(
                QPointF(cx + math.cos(a) * inner, cy + math.sin(a) * inner),
                QPointF(cx + math.cos(a) * outer, cy + math.sin(a) * outer),
            )

        # Direction letters
        for i, label in enumerate(CARDINALS):
            deg = i * 45
            a = math.radians(deg - heading - 90)
            r = radius * 0.66

            major = i % 2 == 0
            p.setFont(make_font(int(radius * (0.17 if major else 0.10)), QFont.ExtraBold))

            if label == "N":
                p.setPen(QColor(COLORS["critical"]))
            elif major:
                p.setPen(QColor(COLORS["text"]))
            else:
                p.setPen(QColor(COLORS["text_secondary"]))

            box = radius * 0.4
            p.drawText(
                QRectF(cx + math.cos(a) * r - box / 2, cy + math.sin(a) * r - box / 2, box, box),
                Qt.AlignCenter,
                label,
            )

        # Fixed pointer at the top
        tip = cy - radius - 2
        pointer = QPolygonF([
            QPointF(cx, tip + radius * 0.16),
            QPointF(cx - radius * 0.08, tip - 4),
            QPointF(cx + radius * 0.08, tip - 4),
        ])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["accent_bright"]))
        p.drawPolygon(pointer)

        # Centre readout
        if self.heading is None:
            text, sub = "—", "NO IMU"
        else:
            text, sub = f"{self.heading:.0f}°", cardinal(self.heading)

        p.setPen(QColor(COLORS["text"] if self.heading is not None else COLORS["muted"]))
        p.setFont(make_font(int(radius * 0.26), QFont.ExtraBold))
        p.drawText(
            QRectF(cx - radius, cy - radius * 0.22, radius * 2, radius * 0.3),
            Qt.AlignCenter,
            text,
        )

        p.setPen(QColor(COLORS["accent_bright"]))
        p.setFont(make_font(int(radius * 0.11), QFont.Bold, spacing=2))
        p.drawText(
            QRectF(cx - radius, cy + radius * 0.08, radius * 2, radius * 0.18),
            Qt.AlignCenter,
            sub,
        )

        p.end()


class Inclinometer(QWidget):
    """
    Roll or pitch gauge: a vehicle outline tilted by the angle,
    over a ±45° scale, with the value underneath.

    view is "rear" (roll) or "side" (pitch). Optional: a tip-over angle
    (red marker on the scale) and the peak angles reached each way.
    """

    WARNING = 15
    CRITICAL = 25
    SPAN = 50          # scale shows ±SPAN degrees

    def __init__(self, title, view="rear", warning=15, critical=25):
        super().__init__()

        self.title = title.upper()
        self.view = view
        self.angle = None
        self.tip = None
        self.peaks = (None, None)

        self.WARNING = warning
        self.CRITICAL = critical

        self.setMinimumSize(260, 260)

    def set_limits(self, warning, critical, tip=None):
        self.WARNING, self.CRITICAL, self.tip = warning, critical, tip
        self.update()

    def set_peaks(self, low, high):
        """Most negative and most positive angle reached (None = not shown)."""
        if (low, high) != self.peaks:
            self.peaks = (low, high)
            self.update()

    def set_angle(self, angle):
        angle = None if angle is None else float(angle)

        if angle != self.angle:
            self.angle = angle
            self.update()

    def status_color(self):
        if self.angle is None:
            return QColor(COLORS["muted"])
        if abs(self.angle) > self.CRITICAL:
            return QColor(COLORS["critical"])
        if abs(self.angle) > self.WARNING:
            return QColor(COLORS["warning"])
        return QColor(COLORS["good"])

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        draw_card_background(p, rect)

        color = self.status_color()
        angle = self.angle or 0.0

        # Caption + status dot
        p.setFont(make_font(15, QFont.Bold, spacing=2))
        p.setPen(QColor(COLORS["text_secondary"]))
        p.drawText(rect.adjusted(22, 18, 0, 0), Qt.AlignLeft | Qt.AlignTop, self.title)

        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(rect.right() - 28, rect.top() + 30), 6, 6)

        # Dial area
        value_h = rect.height() * 0.22
        cx = rect.center().x()
        cy = rect.top() + 50 + (rect.height() - 50 - value_h) * 0.74
        radius = min(rect.width() * 0.42, (rect.height() - 50 - value_h) * 0.68)

        # Scale arc (±SPAN°) across the top half
        for deg in range(-self.SPAN, self.SPAN + 1, 5):
            a = math.radians(deg - 90)
            major = deg % 15 == 0

            if abs(deg) > self.CRITICAL:
                tick_color = COLORS["critical"]
            elif abs(deg) > self.WARNING:
                tick_color = COLORS["warning"]
            else:
                tick_color = COLORS["text_secondary"] if major else COLORS["muted"]

            inner = radius * (0.84 if major else 0.9)
            p.setPen(QPen(QColor(tick_color), 3 if major else 1.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(
                QPointF(cx + math.cos(a) * inner, cy + math.sin(a) * inner),
                QPointF(cx + math.cos(a) * radius, cy + math.sin(a) * radius),
            )

        # Tip-over angle: red wedges just outside the scale
        if self.tip is not None and self.tip <= self.SPAN:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS["critical"]))
            for sign in (-1, 1):
                a = math.radians(sign * self.tip - 90)
                ux, uy = math.cos(a), math.sin(a)          # outward
                tx, ty = -uy, ux                           # along the arc
                base = radius * 1.04
                tipx, tipy = cx + ux * base, cy + uy * base
                w, h = radius * 0.05, radius * 0.1
                p.drawPolygon(QPolygonF([
                    QPointF(tipx, tipy),
                    QPointF(tipx + ux * h + tx * w, tipy + uy * h + ty * w),
                    QPointF(tipx + ux * h - tx * w, tipy + uy * h - ty * w),
                ]))
            p.setFont(make_font(14, QFont.Bold, spacing=1))
            p.setPen(QColor(COLORS["critical"]))
            p.drawText(QRectF(rect.right() - 160, rect.top() + 18, 116, 24),
                       Qt.AlignRight | Qt.AlignVCenter, f"TIP {self.tip:.0f}°")

        # Peak angles: thin accent marks
        for peak in self.peaks:
            if peak is None or abs(peak) < 1:
                continue
            a = math.radians(max(-self.SPAN, min(self.SPAN, peak)) - 90)
            p.setPen(QPen(QColor(COLORS["accent_bright"]), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(
                QPointF(cx + math.cos(a) * radius * 0.72, cy + math.sin(a) * radius * 0.72),
                QPointF(cx + math.cos(a) * radius * 0.8, cy + math.sin(a) * radius * 0.8),
            )

        # Needle
        a = math.radians(max(-self.SPAN, min(self.SPAN, angle)) - 90)
        p.setPen(QPen(color, 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(
            QPointF(cx + math.cos(a) * radius * 0.5, cy + math.sin(a) * radius * 0.5),
            QPointF(cx + math.cos(a) * radius * 0.98, cy + math.sin(a) * radius * 0.98),
        )

        # Ground line
        p.setPen(QPen(QColor(COLORS["border_bright"]), 2, Qt.DashLine))
        p.drawLine(QPointF(cx - radius, cy), QPointF(cx + radius, cy))

        # Vehicle, tilted
        p.save()
        p.translate(cx, cy)
        p.rotate(angle)
        self._draw_vehicle(p, radius * 0.5, color)
        p.restore()

        # Value
        text = "—" if self.angle is None else f"{self.angle:+.1f}°"
        p.setPen(QColor(COLORS["text"] if self.angle is not None else COLORS["muted"]))
        p.setFont(make_font(int(min(56, value_h * 0.7)), QFont.ExtraBold))
        p.drawText(
            QRectF(rect.left(), rect.bottom() - value_h - 8, rect.width(), value_h),
            Qt.AlignCenter,
            text,
        )

        p.end()

    def _draw_vehicle(self, p, size, color):
        """
        Simple Jeep outline sitting on the origin (ground level).
        """

        body = QColor(COLORS["accent"])
        body.setAlpha(210)
        wheel = QColor(COLORS["text"])

        p.setPen(QPen(QColor(COLORS["accent_bright"]), 2))

        if self.view == "rear":
            w = size * 1.3
            # Body and cab
            p.setBrush(body)
            p.drawRoundedRect(QRectF(-w / 2, -size * 0.75, w, size * 0.45), 6, 6)
            p.drawRoundedRect(QRectF(-w * 0.36, -size * 1.15, w * 0.72, size * 0.42), 6, 6)
            # Wheels
            p.setBrush(wheel)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(-w / 2 - 2, -size * 0.34, size * 0.3, size * 0.34), 4, 4)
            p.drawRoundedRect(QRectF(w / 2 - size * 0.3 + 2, -size * 0.34, size * 0.3, size * 0.34), 4, 4)
        else:
            w = size * 2.0
            p.setBrush(body)
            # Body, then cab toward the rear
            p.drawRoundedRect(QRectF(-w / 2, -size * 0.72, w, size * 0.42), 6, 6)
            p.drawRoundedRect(QRectF(-w * 0.18, -size * 1.12, w * 0.58, size * 0.42), 6, 6)
            p.setBrush(wheel)
            p.setPen(Qt.NoPen)
            r = size * 0.2
            p.drawEllipse(QPointF(-w * 0.3, -r), r, r)
            p.drawEllipse(QPointF(w * 0.3, -r), r, r)
