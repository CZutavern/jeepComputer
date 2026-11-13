"""
Trail page: heading, roll / pitch against the Jeep's tip-over limits,
elevation history with climb rate and trip ascent, and daylight left.

Vehicle geometry and the IMU's mounting offsets live in
~/.config/jeep_computer/trail.json:

  track_in       average track width (1975 Cherokee SJ: ~58.6")
  cg_height_in   centre of gravity height (stock ~28", lifted higher)
  wheelbase_in   108.7" for the SJ
  roll_offset / pitch_offset   set by the LEVEL button

The static tip angle is atan((track / 2) / CG height). Real limits are
lower (suspension lean, bumps, sliding), so warnings start at half of it.
"""

import json
import math
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from dashboard.widgets.gauge_card import GaugeCard
from dashboard.widgets.compass import Compass, Inclinometer
from dashboard.widgets.controls import Panel, caption, text_label, button, StatRow
from dashboard.widgets.trend_chart import TrendChart
from dashboard.theme import COLORS, format_value
from dashboard.pages.base import DashboardPage, reading, online
from weather.sun import sun_times

SETTINGS = Path.home() / ".config/jeep_computer/trail.json"
DEFAULTS = {
    "track_in": 58.6,
    "cg_height_in": 30.0,
    "wheelbase_in": 108.7,
    "roll_offset": 0.0,
    "pitch_offset": 0.0,
}

PITCH_WARNING = 25     # traction / oil pickup, well before a flip
PITCH_CRITICAL = 35
ELEVATION_RANGE_S = 2 * 3600
ELEVATION_STEP_S = 10  # chart resolution
ASCENT_HYSTERESIS_FT = 10
M_TO_FT = 3.28084


def load_settings():
    try:
        settings = {**DEFAULTS, **json.loads(SETTINGS.read_text())}
    except (OSError, ValueError):
        settings = dict(DEFAULTS)
    return settings


def save_settings(settings):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")


def tip_angles(settings):
    """(roll tip, pitch tip) in degrees from the static stability factor."""
    cg = max(1.0, settings["cg_height_in"])
    roll = math.degrees(math.atan(settings["track_in"] / 2 / cg))
    pitch = math.degrees(math.atan(settings["wheelbase_in"] / 2 / cg))
    return roll, pitch


def elevation_ft(pressure_hpa, sea_level_hpa):
    """Barometric elevation (international standard atmosphere formula)."""
    return 44330.77 * (1 - (pressure_hpa / sea_level_hpa) ** 0.190263) * M_TO_FT


def duration_text(seconds):
    minutes = int(seconds // 60)
    return f"{minutes // 60} h {minutes % 60:02d} m" if minutes >= 60 else f"{minutes} min"


class OffroadPage(DashboardPage):

    def __init__(self):
        super().__init__("Off Road")

        self.settings = load_settings()
        self.raw_roll = None
        self.raw_pitch = None
        self.peak = {"roll": [0.0, 0.0], "pitch": [0.0, 0.0]}   # [most negative, most positive]

        self.sea_level_hpa = None          # from the weather analysis
        self.elev_recent = deque()         # (ts, ft) at sensor rate, last 90 s
        self.elev_history = deque()        # (ts, ft) every ELEVATION_STEP_S
        self.ascent = 0.0
        self.descent = 0.0
        self.ascent_ref = None

        # -------------------------
        # Compass + inclinometers
        # -------------------------

        top = QHBoxLayout()
        top.setSpacing(self.SPACING)

        self.compass = Compass()
        self.roll = Inclinometer("Roll", view="rear")
        self.pitch = Inclinometer("Pitch", view="side")

        top.addWidget(self.compass, stretch=4)
        top.addWidget(self.roll, stretch=3)
        top.addWidget(self.pitch, stretch=3)

        self.layout_.addLayout(top, stretch=5)

        # -------------------------
        # Elevation chart + slope limits
        # -------------------------

        middle = QHBoxLayout()
        middle.setSpacing(self.SPACING)

        self.chart = TrendChart()
        self.chart.setMinimumHeight(220)
        middle.addWidget(self.chart, stretch=3)

        limits = Panel()
        col = QVBoxLayout(limits)
        col.setContentsMargins(22, 16, 22, 18)
        col.setSpacing(8)
        col.addWidget(caption("Slope limits"))

        self.row_side = StatRow("Side slope")
        self.row_climb = StatRow("Climb / descent")
        self.row_peak = StatRow("Peak this trip")
        for row in (self.row_side, self.row_climb, self.row_peak):
            col.addWidget(row)

        self.geometry_note = text_label("", 14, QFont.DemiBold, COLORS["muted"])
        self.geometry_note.setWordWrap(True)
        col.addWidget(self.geometry_note)
        col.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        self.level_button = button("LEVEL IMU", height=56, size=17)
        self.reset_button = button("RESET PEAKS", height=56, size=17)
        self.level_button.clicked.connect(self.level_pressed)
        self.reset_button.clicked.connect(self.reset_peaks)
        buttons.addWidget(self.level_button)
        buttons.addWidget(self.reset_button)
        col.addLayout(buttons)

        middle.addWidget(limits, stretch=2)
        self.layout_.addLayout(middle, stretch=4)

        # LEVEL needs a second tap within 3 s, so a stray touch on a
        # slope can't zero the inclinometers
        self.level_armed = False
        self.level_timer = QTimer(self)
        self.level_timer.setSingleShot(True)
        self.level_timer.timeout.connect(self.disarm_level)

        # -------------------------
        # Cards
        # -------------------------

        self.cards = {
            "elevation": GaugeCard("Elevation", "ft", value_size=64),
            "climb": GaugeCard("Climb rate", "ft/min", value_size=64),
            "trip": GaugeCard("Trip ascent / descent", "ft", value_size=52),
            "daylight": GaugeCard("Daylight left", value_size=52),
        }
        for card in self.cards.values():
            card.setFixedHeight(150)

        self.layout_.addLayout(self.add_card_grid(list(self.cards.values()), columns=4), stretch=0)

        self.apply_limits()

        # Daylight doesn't depend on sensors
        self.sun_timer = QTimer(self)
        self.sun_timer.timeout.connect(self.update_daylight)
        self.sun_timer.start(30_000)
        self.update_daylight()

        self.update_values()

    # ------------------------------------------------
    # Settings / buttons
    # ------------------------------------------------

    def apply_limits(self):
        roll_tip, pitch_tip = tip_angles(self.settings)
        self.roll_tip = roll_tip
        self.roll.set_limits(round(roll_tip * 0.5), round(roll_tip * 0.7), roll_tip)
        self.pitch.set_limits(PITCH_WARNING, PITCH_CRITICAL, pitch_tip if pitch_tip <= 50 else None)
        self.geometry_note.setText(
            f'Tip angle {roll_tip:.0f}° from {self.settings["track_in"]:.1f}" track and '
            f'{self.settings["cg_height_in"]:.0f}" CG height (static). Suspension lean and bumps '
            f"tip you sooner — warnings start at half.")

    def level_pressed(self):
        if self.raw_roll is None or self.raw_pitch is None:
            return
        if not self.level_armed:
            self.level_armed = True
            self.level_button.setText("TAP AGAIN ON FLAT GROUND")
            self.level_timer.start(3000)
            return

        self.settings["roll_offset"] = round(self.raw_roll, 2)
        self.settings["pitch_offset"] = round(self.raw_pitch, 2)
        save_settings(self.settings)
        self.disarm_level()
        self.reset_peaks()
        self.update_values()

    def disarm_level(self):
        self.level_armed = False
        self.level_timer.stop()
        self.level_button.setText("LEVEL IMU")

    def reset_peaks(self):
        self.peak = {"roll": [0.0, 0.0], "pitch": [0.0, 0.0]}
        self.update_values()

    def set_weather(self, summary):
        """Sea-level pressure from the weather analysis corrects the elevation."""
        slp = getattr(summary, "slp_hpa", None)
        if slp and 950 < slp < 1060:
            self.sea_level_hpa = slp

    # ------------------------------------------------
    # Elevation
    # ------------------------------------------------

    def add_elevation(self, ft):
        now = time.time()

        self.elev_recent.append((now, ft))
        while self.elev_recent and now - self.elev_recent[0][0] > 90:
            self.elev_recent.popleft()

        if not self.elev_history or now - self.elev_history[-1][0] >= ELEVATION_STEP_S:
            # Chart point: mean of the last step, which hides sensor noise
            recent = [v for t, v in self.elev_recent if now - t <= ELEVATION_STEP_S]
            self.elev_history.append((now, sum(recent) / len(recent)))
            while now - self.elev_history[0][0] > ELEVATION_RANGE_S:
                self.elev_history.popleft()

            # Trip ascent / descent with hysteresis so noise doesn't add up
            smoothed = self.elev_history[-1][1]
            if self.ascent_ref is None:
                self.ascent_ref = smoothed
            elif smoothed - self.ascent_ref >= ASCENT_HYSTERESIS_FT:
                self.ascent += smoothed - self.ascent_ref
                self.ascent_ref = smoothed
            elif self.ascent_ref - smoothed >= ASCENT_HYSTERESIS_FT:
                self.descent += self.ascent_ref - smoothed
                self.ascent_ref = smoothed

            self.chart.set_data("Elevation · 2 h", {
                "points": list(self.elev_history), "unit": "ft", "decimals": 0, "slope_h": None,
            }, ELEVATION_RANGE_S, color=COLORS["accent_bright"], min_span=40, project=False)

    def climb_rate(self):
        """ft/min: least-squares slope over the last 60 s."""
        now = time.time()
        pts = [(t, v) for t, v in self.elev_recent if now - t <= 60]
        if len(pts) < 10 or pts[-1][0] - pts[0][0] < 20:
            return None
        n = len(pts)
        mt = sum(t for t, _ in pts) / n
        mv = sum(v for _, v in pts) / n
        den = sum((t - mt) ** 2 for t, _ in pts)
        return None if den == 0 else sum((t - mt) * (v - mv) for t, v in pts) / den * 60

    # ------------------------------------------------
    # Daylight
    # ------------------------------------------------

    def update_daylight(self):
        card = self.cards["daylight"]
        sun = sun_times()
        if sun is None:
            card.set_value(None)
            return

        now = time.time()
        if sun["daylight"]:
            left = sun["sunset"] - now
            card.set_value(duration_text(left))
            card.set_status("critical" if left < 1800 else "warning" if left < 3600 else "good")
        else:
            rise = sun["next_sunrise"]
            card.set_value("Sunrise " + time.strftime("%-I:%M%p", time.localtime(rise)).lower()
                           if rise else "Dark")
            card.set_status("normal")

    # ------------------------------------------------

    def update_values(self):

        data = self.sensor_data

        heading = reading(data, "imu", "heading")
        self.raw_roll = reading(data, "imu", "roll")
        self.raw_pitch = reading(data, "imu", "pitch")

        roll = None if self.raw_roll is None else self.raw_roll - self.settings["roll_offset"]
        pitch = None if self.raw_pitch is None else self.raw_pitch - self.settings["pitch_offset"]

        self.compass.set_heading(heading)
        self.roll.set_angle(roll)
        self.pitch.set_angle(pitch)
        self.level_button.setEnabled(roll is not None)

        # Peaks and slope rows
        for name, angle in (("roll", roll), ("pitch", pitch)):
            if angle is not None:
                lo, hi = self.peak[name]
                self.peak[name] = [min(lo, angle), max(hi, angle)]
        self.roll.set_peaks(*self.peak["roll"])
        self.pitch.set_peaks(*self.peak["pitch"])

        if roll is None:
            self.row_side.set("—", COLORS["muted"])
            self.row_climb.set("—", COLORS["muted"])
            self.row_peak.set("—", COLORS["muted"])
        else:
            share = abs(roll) / self.roll_tip
            color = (COLORS["critical"] if abs(roll) > self.roll.CRITICAL else
                     COLORS["warning"] if abs(roll) > self.roll.WARNING else COLORS["good"])
            self.row_side.set(f"{abs(roll):.0f}° of {self.roll_tip:.0f}°  ({share * 100:.0f}%)", color)

            direction = "up" if pitch is not None and pitch > 0 else "down"
            pcolor = (COLORS["critical"] if pitch is not None and abs(pitch) > PITCH_CRITICAL else
                      COLORS["warning"] if pitch is not None and abs(pitch) > PITCH_WARNING else COLORS["text"])
            self.row_climb.set("—" if pitch is None else f"{abs(pitch):.0f}° {direction}", pcolor)

            peak_roll = max(-self.peak["roll"][0], self.peak["roll"][1])
            peak_pitch = max(-self.peak["pitch"][0], self.peak["pitch"][1])
            self.row_peak.set(f"{peak_roll:.0f}° roll  ·  {peak_pitch:.0f}° pitch",
                              COLORS["warning"] if peak_roll > self.roll.WARNING else COLORS["text"])

        # Elevation (barometric, corrected to today's sea-level pressure)
        pressure = reading(data, "pressure", "pressure")
        if pressure:
            ft = elevation_ft(pressure, self.sea_level_hpa or 1013.25)
            self.add_elevation(ft)
            self.cards["elevation"].set_value(format_value(ft, 0))

            rate = self.climb_rate()
            self.cards["climb"].set_value(None if rate is None else f"{rate:+.0f}")
            self.cards["trip"].set_value(f"↑{self.ascent:,.0f}  ↓{self.descent:,.0f}")
        else:
            for key in ("elevation", "climb", "trip"):
                self.cards[key].set_value(None)

        if online(data, "imu"):
            self.header.set_subtitle("IMU online", COLORS["good"])
        elif data:
            self.header.set_subtitle("IMU offline", COLORS["warning"])
