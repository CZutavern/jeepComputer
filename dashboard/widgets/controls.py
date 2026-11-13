"""
Touch-friendly controls shared by the dashboard pages, in the
dashboard's blue theme.
"""

import math

from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QFont, QPainter, QColor, QLinearGradient, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QWidget,
    QHBoxLayout,
    QButtonGroup,
    QSizePolicy,
)

from dashboard.theme import COLORS, make_font


# -------------------------------------------------------------
# Containers and labels
# -------------------------------------------------------------

class Panel(QFrame):
    """Rounded card container for Qt child widgets."""

    def __init__(self):
        super().__init__()

        self.setObjectName("panel")
        self.setStyleSheet(f"""
            #panel {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {COLORS["card_top"]}, stop:1 {COLORS["card"]});
                border: 1px solid {COLORS["border"]};
                border-radius: 18px;
            }}
        """)


def caption(text):
    """Small uppercase card caption."""

    label = QLabel(text.upper())
    label.setFont(make_font(15, QFont.Bold, spacing=2))
    label.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent;")
    return label


def text_label(text="", size=20, weight=QFont.DemiBold, color=None):
    label = QLabel(text)
    label.setFont(make_font(size, weight))
    label.setStyleSheet(f"color: {color or COLORS['text']}; background: transparent;")
    return label


# -------------------------------------------------------------
# Buttons
# -------------------------------------------------------------

BUTTON_STYLE = f"""
    QPushButton {{
        background: {COLORS["card_alt"]};
        color: {COLORS["text"]};
        border: 1px solid {COLORS["border_bright"]};
        border-radius: 12px;
        padding: 0px 18px;
    }}
    QPushButton:pressed {{
        background: {COLORS["accent_dark"]};
    }}
    QPushButton:checked {{
        background: {COLORS["accent"]};
        border-color: {COLORS["accent_bright"]};
    }}
    QPushButton:disabled {{
        color: {COLORS["muted"]};
        border-color: {COLORS["border"]};
    }}
"""


def button(text, height=64, size=20, checkable=False):
    b = QPushButton(text)
    b.setFixedHeight(height)
    b.setFont(make_font(size, QFont.ExtraBold, spacing=1))
    b.setFocusPolicy(Qt.NoFocus)
    b.setCheckable(checkable)
    b.setStyleSheet(BUTTON_STYLE)
    return b


class SegmentedControl(QWidget):
    """Row of exclusive buttons, e.g. AUTO | ON | OFF."""

    selected = Signal(str)

    def __init__(self, options, height=64, size=20):
        """options: list of (key, label)."""

        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}

        for key, label in options:
            b = button(label, height, size, checkable=True)
            b.clicked.connect(lambda checked=False, k=key: self.selected.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            layout.addWidget(b)

    def set_selected(self, key):
        b = self.buttons.get(key)
        if b is not None and not b.isChecked():
            b.setChecked(True)


# -------------------------------------------------------------
# Stepper
# -------------------------------------------------------------

class Stepper(QWidget):
    """
    [ − ]  value  [ + ]  with press-and-hold repeat.

    step can be a number, or "log" for frequencies (1/12 octave
    steps, rounded to tidy values).
    """

    value_changed = Signal(float)

    def __init__(
        self,
        value=0.0,
        step=1.0,
        minimum=-math.inf,
        maximum=math.inf,
        fmt="{:.1f}",
        unit="",
        height=58,
        value_width=150,
        formatter=None,
    ):
        super().__init__()

        self.value = float(value)
        self.step = step
        self.minimum = minimum
        self.maximum = maximum
        self.fmt = fmt
        self.unit = unit
        self.formatter = formatter

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.minus = button("−", height, 28)
        self.plus = button("+", height, 28)

        for b in (self.minus, self.plus):
            b.setFixedWidth(height + 6)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(90)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setMinimumWidth(value_width)
        self.label.setFont(make_font(22, QFont.ExtraBold))
        self.label.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")

        layout.addWidget(self.minus)
        layout.addWidget(self.label, stretch=1)
        layout.addWidget(self.plus)

        self.minus.clicked.connect(lambda: self.nudge(-1))
        self.plus.clicked.connect(lambda: self.nudge(1))

        self.refresh()

    def text(self):
        if self.formatter:
            return self.formatter(self.value)

        text = self.fmt.format(self.value)
        return f"{text} {self.unit}".strip()

    def refresh(self):
        self.label.setText(self.text())

    def set_value(self, value, emit=False):
        value = max(self.minimum, min(self.maximum, float(value)))

        if value == self.value:
            return

        self.value = value
        self.refresh()

        if emit:
            self.value_changed.emit(self.value)

    def nudge(self, direction):
        if self.step == "log":
            new = self.value * 2 ** (direction / 12)
            # Tidy: whole Hz below 1 kHz, 10 Hz steps above
            new = round(new) if new < 1000 else round(new, -1)
            if new == self.value:
                new = self.value + direction
        else:
            new = round(self.value + direction * self.step, 4)

        self.set_value(new, emit=True)


# -------------------------------------------------------------
# Level meters
# -------------------------------------------------------------

class LevelMeters(QWidget):
    """
    Vertical meter per channel: RMS bar with a peak tick,
    -60 dBFS .. 0 dBFS, turning amber/red near full scale.
    """

    FLOOR_DB = -60.0

    def __init__(self, labels):
        super().__init__()

        self.labels = list(labels)
        self.rms = [None] * len(self.labels)
        self.peak = [None] * len(self.labels)

        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_labels(self, labels):
        labels = list(labels)
        if labels != self.labels:
            self.labels = labels
            self.rms = [None] * len(labels)
            self.peak = [None] * len(labels)
            self.update()

    def set_levels(self, rms, peak):
        n = len(self.labels)
        self.rms = (list(rms) + [None] * n)[:n]
        self.peak = (list(peak) + [None] * n)[:n]
        self.update()

    def fraction(self, db):
        if db is None or db <= self.FLOOR_DB:
            return 0.0
        return min(1.0, (db - self.FLOOR_DB) / -self.FLOOR_DB)

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        n = max(1, len(self.labels))
        label_h = 30
        scale_w = 44
        gap = 14

        area = QRectF(scale_w, 12, self.width() - scale_w, self.height() - label_h - 16)
        bar_w = (area.width() - gap * (n - 1)) / n

        # dB scale
        p.setFont(make_font(13, QFont.Bold))
        for db in (0, -12, -24, -36, -48, -60):
            y = area.bottom() - self.fraction(db + 0.0001) * area.height() if db > self.FLOOR_DB else area.bottom()
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(QRectF(0, y - 9, scale_w - 8, 18), Qt.AlignRight | Qt.AlignVCenter, str(db))
            p.setPen(QColor(COLORS["border"]))
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

        label_font = make_font(14, QFont.Bold)
        metrics = QFontMetrics(label_font)

        for i in range(len(self.labels)):
            x = area.left() + i * (bar_w + gap)
            track = QRectF(x, area.top(), bar_w, area.height())

            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS["background_top"]))
            p.drawRoundedRect(track, 6, 6)

            level = self.fraction(self.rms[i])
            if level > 0:
                fill = QRectF(x, track.bottom() - level * track.height(), bar_w, level * track.height())
                gradient = QLinearGradient(QPointF(0, track.bottom()), QPointF(0, track.top()))
                gradient.setColorAt(0.0, QColor(COLORS["accent_dark"]))
                gradient.setColorAt(0.75, QColor(COLORS["accent_bright"]))
                gradient.setColorAt(0.9, QColor(COLORS["warning"]))
                gradient.setColorAt(1.0, QColor(COLORS["critical"]))
                p.setBrush(gradient)
                p.drawRoundedRect(fill, 6, 6)

            peak = self.fraction(self.peak[i])
            if peak > 0:
                y = track.bottom() - peak * track.height()
                color = COLORS["critical"] if (self.peak[i] or -999) > -1 else COLORS["text"]
                p.setBrush(QColor(color))
                p.drawRect(QRectF(x, y - 1.5, bar_w, 3))

            p.setFont(label_font)
            p.setPen(QColor(COLORS["text_secondary"]))
            text = metrics.elidedText(self.labels[i], Qt.ElideRight, int(bar_w + gap - 2))
            p.drawText(
                QRectF(x - gap / 2, area.bottom() + 6, bar_w + gap, label_h),
                Qt.AlignHCenter | Qt.AlignTop,
                text,
            )

        p.end()


class StatRow(QWidget):
    """Label on the left, value on the right."""

    def __init__(self, name):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.name = text_label(name, 18, QFont.DemiBold, COLORS["text_secondary"])
        self.value = text_label("—", 20, QFont.ExtraBold)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(self.name)
        layout.addStretch(1)
        layout.addWidget(self.value)

    def set(self, text, color=None):
        self.value.setText(text)
        self.value.setStyleSheet(
            f"color: {color or COLORS['text']}; background: transparent;"
        )
