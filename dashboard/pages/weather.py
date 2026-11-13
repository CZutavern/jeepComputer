"""
Weather page: forecast, storm risk and trend charts from the weather
analysis engine (weather/analysis.py), which runs once a minute on the
sensor thread and arrives here as a WeatherSummary.
"""

import json
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from dashboard.theme import COLORS, STATUS_COLORS, make_font
from dashboard.pages.base import DashboardPage
from dashboard.widgets.arc_gauge import ArcGauge
from dashboard.widgets.controls import Panel, caption, text_label, SegmentedControl, Stepper
from dashboard.widgets.trend_chart import TrendChart
from dashboard.widgets.weather_widgets import MetricCard, WeatherIcon
from weather.analysis import c_to_f


SETTINGS_PATH = Path.home() / ".config/jeep_computer/weather.json"

# key, title, colour, min chart span, |rate/h| that counts as steady
METRICS = [
    ("pressure", "Pressure", COLORS["accent_bright"], 2.0, 0.2),
    ("temp", "Temperature", "#ffa24a", 4.0, 0.5),
    ("humidity", "Humidity", COLORS["good"], 10.0, 1.0),
    ("dew", "Dew Point", "#a48cff", 4.0, 0.5),
    ("light", "Sunlight", "#ffd23f", 200.0, 200.0),
]

RANGES = [("3h", "3 H", 3 * 3600), ("24h", "24 H", 24 * 3600), ("72h", "3 DAYS", 72 * 3600)]


def load_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")


class WeatherPage(DashboardPage):

    def __init__(self):
        super().__init__("Weather")

        self.summary = None
        self.selected = "pressure"
        self.range_key = "24h"
        self.alert = None
        # Set by main.py: re-run the analysis now (after a settings change)
        self.on_settings_changed = None

        # ==================== top row ====================
        top = QHBoxLayout()
        top.setSpacing(self.SPACING)

        # --- Forecast -------------------------------------
        forecast = Panel()
        f = QHBoxLayout(forecast)
        f.setContentsMargins(20, 16, 24, 16)
        f.setSpacing(16)

        self.icon = WeatherIcon()
        self.icon.setFixedWidth(170)
        f.addWidget(self.icon)

        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(caption("Forecast · next 12 h"))
        self.forecast_text = text_label("Collecting data…", 32, QFont.ExtraBold)
        self.forecast_text.setWordWrap(True)
        col.addWidget(self.forecast_text)
        self.forecast_detail = text_label("", 17, QFont.DemiBold, COLORS["text_secondary"])
        self.forecast_detail.setWordWrap(True)
        col.addWidget(self.forecast_detail)
        col.addStretch(1)

        elev = QHBoxLayout()
        elev.addWidget(text_label("Elevation", 16, QFont.Bold, COLORS["text_secondary"]))
        elevation_ft = load_settings().get("elevation_ft")
        self.elevation = Stepper(
            -50 if elevation_ft is None else elevation_ft,
            step=50, minimum=-50, maximum=14000, height=46, value_width=150,
            formatter=lambda v: "Auto" if v < 0 else f"{v:,.0f} ft",
        )
        self.elevation.value_changed.connect(self.set_elevation)
        elev.addWidget(self.elevation, stretch=1)
        col.addLayout(elev)

        f.addLayout(col, stretch=1)
        top.addWidget(forecast, stretch=5)

        # --- Storm risk -----------------------------------
        self.storm = ArcGauge(
            "Storm risk", "% next 12 h", 0, 100,
            zones=[(25, 50, "normal"), (50, 75, "warning"), (75, 100, "critical")],
            ticks=[(0, "0"), (25, "25"), (50, "50"), (75, "75"), (100, "100")],
        )
        top.addWidget(self.storm, stretch=3)

        # --- Pressure trend + reasons ---------------------
        trend = Panel()
        t = QVBoxLayout(trend)
        t.setContentsMargins(24, 16, 24, 16)
        t.setSpacing(4)
        t.addWidget(caption("Pressure trend · 3 h"))
        self.change_label = text_label("—", 38, QFont.ExtraBold)
        t.addWidget(self.change_label)
        self.tendency_label = text_label("Measuring", 20, QFont.ExtraBold, COLORS["text_secondary"])
        t.addWidget(self.tendency_label)
        self.character_label = text_label("", 16, QFont.DemiBold, COLORS["text_secondary"])
        t.addWidget(self.character_label)
        t.addSpacing(8)
        self.reasons_label = text_label("", 16, QFont.DemiBold, COLORS["text"])
        self.reasons_label.setWordWrap(True)
        t.addWidget(self.reasons_label)
        t.addStretch(1)
        top.addWidget(trend, stretch=4)

        top_box = QWidget()
        top_box.setLayout(top)
        top_box.setFixedHeight(290)
        self.layout_.addWidget(top_box)

        # ==================== chart ====================
        chart_col = QVBoxLayout()
        chart_col.setSpacing(0)

        self.chart = TrendChart()

        self.ranges = SegmentedControl([(k, label) for k, label, _ in RANGES], height=44, size=15)
        self.ranges.setFixedWidth(360)
        self.ranges.selected.connect(self.set_range)
        self.ranges.set_selected(self.range_key)

        # Range buttons sit in the chart's top-right corner
        self.ranges.setParent(self.chart)
        self.chart.resizeEvent = self._place_ranges

        chart_col.addWidget(self.chart)
        self.layout_.addLayout(chart_col, stretch=1)

        # ==================== metric cards ====================
        cards = QHBoxLayout()
        cards.setSpacing(self.SPACING)
        self.cards = {}
        for key, title, color, _span, steady in METRICS:
            card = MetricCard(key, title, color, steady)
            card.clicked.connect(self.select_metric)
            self.cards[key] = card
            cards.addWidget(card)

        cards_box = QWidget()
        cards_box.setLayout(cards)
        cards_box.setFixedHeight(128)
        self.layout_.addWidget(cards_box)

        self.select_metric(self.selected)

    # ---------------------------------------------------------

    def _place_ranges(self, event):
        self.ranges.move(self.chart.width() - self.ranges.width() - 20, 10)
        self.ranges.raise_()

    def select_metric(self, key):
        self.selected = key
        for k, card in self.cards.items():
            card.set_selected(k == key)
        self.refresh_chart()

    def set_range(self, key):
        self.range_key = key
        self.refresh_chart()

    def refresh_chart(self):
        key, title, color, span, _ = next(m for m in METRICS if m[0] == self.selected)
        seconds = next(s for k, _, s in RANGES if k == self.range_key)
        metric = (self.summary.metrics.get(key) if self.summary else None) or {}
        self.chart.set_data(title, metric, seconds, color, span)

    def set_elevation(self, value):
        settings = load_settings()
        settings["elevation_ft"] = None if value < 0 else value
        save_settings(settings)
        if self.on_settings_changed:
            self.on_settings_changed()

    # ---------------------------------------------------------
    # Live readings (every second) keep the cards' current values fresh
    # ---------------------------------------------------------

    def update_sensors(self, data):
        self.sensor_data = data

        def reading(sensor, key):
            return ((data.get(sensor) or {}).get("data") or {}).get(key)

        live = {
            "temp": c_to_f(reading("outside", "temperature_c")),
            "humidity": reading("outside", "humidity"),
            "light": reading("light", "lux"),
        }
        for key, value in live.items():
            if value is not None and self.summary:
                metric = self.summary.metrics.get(key)
                if metric is not None:
                    metric["now"] = value
                    self.cards[key].update()

    # ---------------------------------------------------------
    # Analysis results (every minute)
    # ---------------------------------------------------------

    def set_summary(self, s):
        self.summary = s

        for key, card in self.cards.items():
            card.set_metric(s.metrics.get(key))
        self.refresh_chart()

        # Forecast
        hour = time.localtime().tm_hour
        self.icon.set_forecast(s.forecast_index, night=hour >= 20 or hour < 6)
        self.forecast_text.setText(s.forecast or "Collecting data…")

        detail = []
        if s.slp_hpa is not None:
            source = "estimated, set elevation for accuracy" if s.slp_source == "auto" else "sea level"
            detail.append(f"{s.slp_hpa:.1f} hPa ({source})")
        if s.change_3h is not None:
            detail.append(f"confidence {s.confidence * 100:.0f}%")
        if s.temp_min_24h_c is not None:
            detail.append(f"24 h low {c_to_f(s.temp_min_24h_c):.0f}° / high {c_to_f(s.temp_max_24h_c):.0f}°")
        self.forecast_detail.setText("  ·  ".join(detail))

        # Storm risk dial
        level_status = {"Low": "good", "Moderate": "normal", "High": "warning", "Severe": "critical"}
        if s.storm_probability is None:
            self.storm.set_value(None)
        else:
            self.storm.set_value(s.storm_probability, f"{s.storm_probability}%")
            self.storm.set_status(level_status.get(s.storm_level, "normal"))

        # Pressure trend
        if s.change_3h is None:
            self.change_label.setText("—")
            self.tendency_label.setText("Measuring (needs ~90 min)")
            color = COLORS["text_secondary"]
        else:
            self.change_label.setText(f"{s.change_3h:+.1f} hPa")
            self.tendency_label.setText(s.tendency)
            color = (COLORS["critical"] if s.change_3h <= -3.6 else
                     COLORS["warning"] if s.change_3h <= -1.6 else
                     COLORS["good"] if s.change_3h >= 1.6 else COLORS["text_secondary"])
        self.tendency_label.setStyleSheet(f"color: {color}; background: transparent;")
        parts = [s.characteristic] if s.characteristic else []
        if getattr(s, "tide_fitted", False) and s.tide_amplitude:
            parts.append(f"daily ±{s.tide_amplitude:.1f} hPa tide removed")
        self.character_label.setText("  ·  ".join(parts))

        lines = [text for _, text in s.alerts] + s.storm_reasons
        if s.storm_eta and s.storm_probability and s.storm_probability >= 50:
            lines.append(f"Expect it {s.storm_eta}")
        self.reasons_label.setText("\n".join(f"•  {line}" for line in lines[:5]) or "No warning signs")

        # Dashboard-wide banner
        self.alert = s.alerts[0] if s.alerts else None
        self.header.set_subtitle(
            f"Storm risk {s.storm_level.lower()}" if s.storm_level else "Collecting data",
            STATUS_COLORS.get(level_status.get(s.storm_level, "normal"), COLORS["text_secondary"]),
        )
