"""
Top-down Jeep cabin for the stem mixer: the speakers where they really are,
and one puck per stem to drag around. Each puck shows its stereo width (L/R
dots), its spread (halo) and glows with the stem's level; the selected stem
draws a line to every speaker, as thick as that speaker's share of it.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from audio import stems as S
from dashboard.theme import COLORS, make_font
from dashboard.widgets.compass import draw_card_background

SPEAKER_LABELS = {
    "front_l": "Front L", "front_r": "Front R", "rear_l": "Rear L", "rear_r": "Rear R",
    "mid_l": "8\" L", "mid_r": "8\" R",
}


class StemStage(QWidget):

    changed = Signal(bool)          # True = drag finished (persist)
    selected_changed = Signal(str)

    PUCK_R = 30
    PICK_R = 60

    def __init__(self):
        super().__init__()
        self.state = S.default_state()
        self.selected = "vocals"
        self.levels = {name: 0.0 for name in S.STEMS}     # 0..1 for the glow
        self.dragging = None
        self.setMinimumSize(520, 600)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ------------------------------------------------------------

    def set_state(self, state):
        self.state = state
        self.update()

    def set_selected(self, name):
        if name != self.selected:
            self.selected = name
            self.update()

    def set_levels(self, levels):
        self.levels = levels
        self.update()

    # ------------------------------------------------------------
    # World (metres) <-> widget coordinates, front of the Jeep at the top

    def _geometry(self):
        rect = QRectF(self.rect()).adjusted(24, 52, -24, -20)
        (x0, x1), (y0, y1) = S.AREA["x"], S.AREA["y"]
        scale = min(rect.width() / (x1 - x0), rect.height() / (y1 - y0))
        cx = rect.center().x()
        top = rect.center().y() - (y1 - y0) * scale / 2
        return scale, cx, top

    def to_px(self, x, y):
        scale, cx, top = self._geometry()
        return QPointF(cx + x * scale, top + (S.AREA["y"][1] - y) * scale)

    def to_world(self, point):
        scale, cx, top = self._geometry()
        x = (point.x() - cx) / scale
        y = S.AREA["y"][1] - (point.y() - top) / scale
        return (min(max(x, S.AREA["x"][0]), S.AREA["x"][1]),
                min(max(y, S.AREA["y"][0]), S.AREA["y"][1]))

    # ------------------------------------------------------------
    # Input (touch arrives as synthesized mouse events)

    def _pick(self, pos):
        best, best_d = None, self.PICK_R
        for name in S.STEMS:
            stem = self.state["stems"][name]
            p = self.to_px(stem["x"], stem["y"])
            d = math.hypot(p.x() - pos.x(), p.y() - pos.y())
            if d < best_d:
                best, best_d = name, d
        return best

    def mousePressEvent(self, event):
        name = self._pick(event.position())
        if name:
            self.dragging = name
            self.set_selected(name)
            self.selected_changed.emit(name)

    def mouseMoveEvent(self, event):
        if self.dragging:
            x, y = self.to_world(event.position())
            stem = self.state["stems"][self.dragging]
            stem["x"], stem["y"] = round(x, 3), round(y, 3)
            self.update()
            self.changed.emit(False)

    def mouseReleaseEvent(self, event):
        if self.dragging:
            self.dragging = None
            self.changed.emit(True)

    # ------------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        draw_card_background(p, rect)

        p.setFont(make_font(15, QFont.Bold, spacing=2))
        p.setPen(QColor(COLORS["text_secondary"]))
        p.drawText(rect.adjusted(22, 16, 0, 0), Qt.AlignLeft | Qt.AlignTop, "STAGE · DRAG THE STEMS")
        p.setPen(QColor(COLORS["muted"]))
        p.drawText(rect.adjusted(0, 16, -22, 0), Qt.AlignRight | Qt.AlignTop, "FRONT ▲")

        scale, _, _ = self._geometry()
        self._draw_cabin(p, scale)
        self._draw_speakers(p, scale)
        if self.selected:
            self._draw_contributions(p, scale)
        for name in S.STEMS:
            if name != self.selected:
                self._draw_puck(p, name, scale)
        if self.selected:
            self._draw_puck(p, self.selected, scale)
        p.end()

    def _draw_cabin(self, p, scale):
        # Body, windshield, seats, cargo
        tl, br = self.to_px(-0.88, 1.38), self.to_px(0.88, -1.55)
        body = QRectF(tl, br)
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0, QColor(COLORS["card_alt"]))
        grad.setColorAt(1, QColor(COLORS["background_top"]))
        p.setPen(QPen(QColor(COLORS["border_bright"]), 2))
        p.setBrush(grad)
        p.drawRoundedRect(body, 0.28 * scale, 0.28 * scale)

        glass = QPainterPath()
        glass.moveTo(self.to_px(-0.74, 1.3))
        glass.lineTo(self.to_px(0.74, 1.3))
        glass.lineTo(self.to_px(0.66, 1.16))
        glass.lineTo(self.to_px(-0.66, 1.16))
        glass.closeSubpath()
        g = QColor(COLORS["accent"])
        g.setAlpha(40)
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawPath(glass)

        seat = QColor(COLORS["border"])
        p.setBrush(seat)
        for x in (-0.38, 0.38):                       # front buckets
            p.drawRoundedRect(QRectF(self.to_px(x - 0.22, 0.72), self.to_px(x + 0.22, 0.3)), 8, 8)
        p.drawRoundedRect(QRectF(self.to_px(-0.66, -0.12), self.to_px(0.66, -0.5)), 10, 10)  # bench

        p.setPen(QColor(COLORS["muted"]))
        p.setFont(make_font(12, QFont.Bold, spacing=2))
        p.drawText(QRectF(self.to_px(-0.6, -0.95), self.to_px(0.6, -1.2)), Qt.AlignCenter, "CARGO")
        # Driver marker
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(COLORS["muted"]), 2))
        p.drawEllipse(self.to_px(-0.38, 0.9), 0.09 * scale, 0.09 * scale)

    def _draw_speakers(self, p, scale):
        for key, (x, y, out, band) in S.SPEAKERS.items():
            c = self.to_px(x, y)
            r = 16 if band == "high" else 20
            p.setPen(QPen(QColor(COLORS["text_secondary"]), 2))
            p.setBrush(QColor(COLORS["card"]))
            p.drawEllipse(c, r, r)
            p.setBrush(QColor(COLORS["border_bright"]))
            p.drawEllipse(c, r * 0.45, r * 0.45)
            p.setFont(make_font(12, QFont.Bold))
            p.setPen(QColor(COLORS["text_secondary"]))
            dx = -r - 70 if x < 0 else r + 6
            p.drawText(QRectF(c.x() + dx, c.y() - 9, 64, 18),
                       (Qt.AlignRight if x < 0 else Qt.AlignLeft) | Qt.AlignVCenter, SPEAKER_LABELS[key])
        c = self.to_px(*S.SUB_POSITION)
        p.setPen(QPen(QColor(COLORS["text_secondary"]), 2))
        p.setBrush(QColor(COLORS["card"]))
        p.drawRoundedRect(QRectF(c.x() - 46, c.y() - 14, 92, 28), 6, 6)
        p.setFont(make_font(12, QFont.Bold, spacing=1))
        p.drawText(QRectF(c.x() - 46, c.y() - 14, 92, 28), Qt.AlignCenter, "SUBS")

    def _draw_contributions(self, p, scale):
        stem = self.state["stems"][self.selected]
        seat = self.state.get("seat", "driver")
        gains = S.stem_gains(stem, seat)
        # Lines show what the chosen seat hears from each speaker (gain over
        # distance, relative to the band's nearest speaker), not the raw
        # gains: seat compensation drives far speakers harder, which would
        # otherwise draw a front-placed stem as coming from the back.
        listener = S.SEATS.get(seat)
        near = {}
        if listener:
            for x, y, out, band in S.SPEAKERS.values():
                near[band] = min(near.get(band, 99), math.dist(listener, (x, y)))
        color = QColor(S.STEM_COLORS[self.selected])
        c = self.to_px(stem["x"], stem["y"])
        for key, (x, y, out, band) in S.SPEAKERS.items():
            g = sum(v for _, v in gains.get(out, [])) / 2      # average of L and R
            if listener:
                g *= near[band] / math.dist(listener, (x, y))
            if g < 0.03:
                continue
            line = QColor(color)
            line.setAlphaF(min(0.85, 0.2 + g))
            p.setPen(QPen(line, 1 + 9 * g, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c, self.to_px(x, y))

    def _draw_puck(self, p, name, scale):
        stem = self.state["stems"][name]
        solo_any = any(s.get("solo") for s in self.state["stems"].values())
        audible = not stem.get("mute") and (not solo_any or stem.get("solo"))
        color = QColor(S.STEM_COLORS[name])
        c = self.to_px(stem["x"], stem["y"])

        # Spread halo
        blur = (S.BASE_BLUR_M + stem["spread"] * S.MAX_EXTRA_BLUR_M) * scale
        halo = QRadialGradient(c, max(blur, self.PUCK_R))
        h = QColor(color)
        h.setAlphaF(0.22 if audible else 0.06)
        halo.setColorAt(0, h)
        h2 = QColor(color)
        h2.setAlphaF(0)
        halo.setColorAt(1, h2)
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, max(blur, self.PUCK_R), max(blur, self.PUCK_R))

        # Stereo width: L and R positions
        half = stem["width"] * S.MAX_HALF_WIDTH_M / 2 * scale
        if half > 4:
            p.setPen(QPen(color, 3, Qt.DashLine))
            p.drawLine(QPointF(c.x() - half, c.y()), QPointF(c.x() + half, c.y()))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            for dx, label in ((-half, "L"), (half, "R")):
                p.drawEllipse(QPointF(c.x() + dx, c.y()), 9, 9)
                p.setPen(QColor(COLORS["background"]))
                p.setFont(make_font(10, QFont.ExtraBold))
                p.drawText(QRectF(c.x() + dx - 9, c.y() - 9, 18, 18), Qt.AlignCenter, label)
                p.setPen(Qt.NoPen)

        # Level glow
        level = self.levels.get(name, 0.0) if audible else 0.0
        if level > 0.02:
            glow = QColor(color)
            glow.setAlphaF(0.55 * level)
            p.setPen(QPen(glow, 4 + 10 * level))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, self.PUCK_R + 4 + 6 * level, self.PUCK_R + 4 + 6 * level)

        body = QColor(color)
        if not audible:
            body.setAlphaF(0.3)
        p.setPen(QPen(QColor(COLORS["text"]), 3 if name == self.selected else 0))
        p.setBrush(body)
        p.drawEllipse(c, self.PUCK_R, self.PUCK_R)
        p.setPen(QColor(COLORS["background"]))
        p.setFont(make_font(13, QFont.ExtraBold))
        p.drawText(QRectF(c.x() - self.PUCK_R, c.y() - self.PUCK_R, 2 * self.PUCK_R, 2 * self.PUCK_R),
                   Qt.AlignCenter, S.STEM_LABELS[name][:3].upper())
        p.setPen(QColor(COLORS["text"] if audible else COLORS["muted"]))
        p.setFont(make_font(14, QFont.Bold))
        p.drawText(QRectF(c.x() - 70, c.y() + self.PUCK_R + 4, 140, 20), Qt.AlignCenter,
                   S.STEM_LABELS[name] + (" · M" if stem.get("mute") else " · S" if stem.get("solo") else ""))
