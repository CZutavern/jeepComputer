"""
Audio page: amplifier control and a touch-friendly CamillaDSP editor.

Tabs
  Overview        amps, volume / mute, DSP health, live level meters
  Crossover & EQ  every filter in the pipeline, editable live
  Mixer           per-output source gains, mute and polarity
  Configs         switch between the configs in ~/CamillaDSP

Edits are applied to the running CamillaDSP immediately (SetConfigJson)
and only written to the config file when SAVE is pressed. REVERT
reloads the file.
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QStackedWidget,
    QScrollArea,
    QSlider,
    QListWidget,
    QListWidgetItem,
    QFrame,
)

from audio.camilla_client import CamillaClient
from audio import stems as S
from audio.amp_client import amp_status, set_amp_mode, set_amp_idle_off
from dashboard.theme import COLORS, make_font
from dashboard.pages.base import DashboardPage
from dashboard.widgets.controls import (
    Panel,
    caption,
    text_label,
    button,
    SegmentedControl,
    Stepper,
    LevelMeters,
    StatRow,
)


CONFIG_DIR = Path.home() / "CamillaDSP"

# What each CamillaDSP input carries (see connect-camilladsp.sh)
INPUT_NAMES = [
    "CarPlay L", "CarPlay R",
    "Music FL", "Music FR", "Music C", "Music LFE", "Music RL", "Music RR",
]
# Stem-enabled configs (tools/enable_stems.py): 10 inputs, CarPlay then the
# stems. Stem routing is set by placing them on the Music page, not here.
STEM_INPUT_NAMES = ["Drums L", "Drums R", "Bass L", "Bass R", "Vocals L", "Vocals R", "Other L", "Other R"]
STEM_CONFIG_NAMES = ["CarPlay L", "CarPlay R"] + STEM_INPUT_NAMES

# Editable filter parameters: step, range, display format
PARAM_SPECS = {
    "freq": dict(label="Frequency", step="log", minimum=10, maximum=20000, fmt="{:.0f}", unit="Hz"),
    "gain": dict(label="Gain", step=0.5, minimum=-30, maximum=20, fmt="{:+.1f}", unit="dB"),
    "q": dict(label="Q", step=0.05, minimum=0.1, maximum=20, fmt="{:.2f}", unit=""),
    "slope": dict(label="Slope", step=1, minimum=1, maximum=24, fmt="{:.0f}", unit="dB/oct"),
    "order": dict(label="Order", step=1, minimum=1, maximum=8, fmt="{:.0f}", unit=""),
    # 0.05 ms = 1.7 cm of speaker distance
    "delay": dict(label="Delay", step=0.05, minimum=0, maximum=20, fmt="{:.2f}", unit="ms"),
    "reference_level": dict(label="Full effect below", step=1, minimum=-60, maximum=0, fmt="{:.0f}", unit="dB"),
    "low_boost": dict(label="Bass boost", step=0.5, minimum=0, maximum=20, fmt="{:.1f}", unit="dB"),
    "high_boost": dict(label="Treble boost", step=0.5, minimum=0, maximum=20, fmt="{:.1f}", unit="dB"),
    "clip_limit": dict(label="Clip limit", step=0.5, minimum=-20, maximum=0, fmt="{:+.1f}", unit="dB"),
}

TOGGLE_PARAMS = {
    "inverted": "Invert",
    "mute": "Mute",
    "soft_clip": "Soft clip",
    "attenuate_mid": "Cut mids",
}


def humanize(name):
    """ButterworthHighpass -> Butterworth Highpass"""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(name))


def scroll(widget):
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setStyleSheet(f"""
        QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; }}
        QScrollBar::handle:vertical {{
            background: {COLORS["border_bright"]}; border-radius: 5px; min-height: 60px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    """)
    return area


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


class AudioPage(DashboardPage):

    FAST_MS = 100      # level meters
    SLOW_MS = 1000     # status, volume, amps
    APPLY_DELAY_MS = 250

    def __init__(self):
        super().__init__("Audio", "CamillaDSP")

        self.camilla = CamillaClient(timeout=0.3, log_errors=False)

        self.config = None          # running config (dict)
        self.config_path = None
        self.dirty = False
        self.volume_dragging = False

        # --------------------
        # Tabs + editor toolbar
        # --------------------

        top = QHBoxLayout()
        top.setSpacing(self.SPACING)

        self.tabs = SegmentedControl([
            ("overview", "OVERVIEW"),
            ("filters", "CROSSOVER && EQ"),
            ("mixer", "MIXER"),
            ("configs", "CONFIGS"),
        ], height=58, size=18)
        self.tabs.selected.connect(self.show_tab)
        top.addWidget(self.tabs, stretch=3)

        self.toolbar = QWidget()
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(10)

        self.edit_status = text_label("", 17, QFont.Bold, COLORS["text_secondary"])
        self.edit_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.revert_button = button("REVERT", 58, 18)
        self.save_button = button("SAVE", 58, 18)
        self.revert_button.clicked.connect(self.revert)
        self.save_button.clicked.connect(self.save)

        bar.addWidget(self.edit_status, stretch=1)
        bar.addWidget(self.revert_button)
        bar.addWidget(self.save_button)
        top.addWidget(self.toolbar, stretch=2)

        self.layout_.addLayout(top)

        self.stack = QStackedWidget()
        self.layout_.addWidget(self.stack, stretch=1)

        self.tab_index = {}
        for key, builder in (
            ("overview", self.build_overview),
            ("filters", self.build_filters_tab),
            ("mixer", self.build_mixer_tab),
            ("configs", self.build_configs_tab),
        ):
            self.tab_index[key] = self.stack.count()
            self.stack.addWidget(builder())

        # --------------------
        # Timers (run only while the page is visible)
        # --------------------

        self.fast_timer = QTimer(self)
        self.fast_timer.timeout.connect(self.poll_levels)

        self.slow_timer = QTimer(self)
        self.slow_timer.timeout.connect(self.poll_status)

        self.apply_timer = QTimer(self)
        self.apply_timer.setSingleShot(True)
        self.apply_timer.timeout.connect(self.apply_config)

        self.tabs.set_selected("overview")
        self.show_tab("overview")
        self.update_edit_status()

    # =========================================================
    # Tab: Overview
    # =========================================================

    def build_overview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.SPACING)

        row = QHBoxLayout()
        row.setSpacing(self.SPACING)
        layout.addLayout(row, stretch=0)

        # --- Amplifiers ---------------------------------------
        amp = Panel()
        a = QVBoxLayout(amp)
        a.setContentsMargins(24, 18, 24, 20)
        a.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(caption("Amplifiers"))
        head.addStretch(1)
        # Amp box temperature (MCP9808 in the enclosure), when fitted
        self.amp_temp = text_label("", 15, QFont.Bold, COLORS["muted"])
        head.addWidget(self.amp_temp)
        head.addSpacing(14)
        self.amp_pin = text_label("", 15, QFont.Bold, COLORS["muted"])
        head.addWidget(self.amp_pin)
        a.addLayout(head)

        self.amp_state = text_label("—", 46, QFont.ExtraBold)
        a.addWidget(self.amp_state)
        self.amp_reason = text_label("", 18, QFont.DemiBold, COLORS["text_secondary"])
        a.addWidget(self.amp_reason)

        self.amp_mode = SegmentedControl([("auto", "AUTO"), ("on", "ON"), ("off", "OFF")])
        self.amp_mode.selected.connect(self.set_amp_mode)
        a.addWidget(self.amp_mode)

        idle = QHBoxLayout()
        idle.addWidget(text_label("Auto-off after", 18, QFont.DemiBold, COLORS["text_secondary"]))
        self.amp_idle = Stepper(
            30, step=10, minimum=0, maximum=600, height=54, value_width=110,
            formatter=lambda s: "Never" if s <= 0 else (f"{s:.0f} s" if s < 60 else f"{s / 60:g} min"),
        )
        self.amp_idle.value_changed.connect(lambda v: set_amp_idle_off(int(v)))
        idle.addWidget(self.amp_idle, stretch=1)
        a.addLayout(idle)

        row.addWidget(amp, stretch=5)

        # --- Volume -------------------------------------------
        vol = Panel()
        v = QVBoxLayout(vol)
        v.setContentsMargins(24, 18, 24, 20)
        v.setSpacing(10)

        v.addWidget(caption("Main Volume"))
        self.volume_label = text_label("—", 46, QFont.ExtraBold)
        v.addWidget(self.volume_label)
        self.volume_note = text_label("", 18, QFont.DemiBold, COLORS["text_secondary"])
        v.addWidget(self.volume_note)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(-160, 0)          # half-dB steps, -80..0 dB
        self.volume_slider.setFixedHeight(64)
        self.volume_slider.setFocusPolicy(Qt.NoFocus)
        self.volume_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 12px; border-radius: 6px; background: {COLORS["border"]};
            }}
            QSlider::sub-page:horizontal {{
                border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLORS["accent_dark"]}, stop:1 {COLORS["accent_bright"]});
            }}
            QSlider::handle:horizontal {{
                width: 44px; height: 44px; margin: -16px 0; border-radius: 22px;
                background: {COLORS["text"]}; border: 4px solid {COLORS["accent"]};
            }}
        """)
        self.volume_slider.sliderPressed.connect(lambda: setattr(self, "volume_dragging", True))
        self.volume_slider.sliderReleased.connect(self.volume_released)
        self.volume_slider.valueChanged.connect(self.volume_slider_moved)
        v.addWidget(self.volume_slider)

        vrow = QHBoxLayout()
        vrow.setSpacing(10)
        self.vol_down = button("−  1 dB", 58, 20)
        self.vol_up = button("+  1 dB", 58, 20)
        self.mute_button = button("MUTE", 58, 20, checkable=True)
        for b in (self.vol_down, self.vol_up):
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(120)
        self.vol_down.clicked.connect(lambda: self.nudge_volume(-1))
        self.vol_up.clicked.connect(lambda: self.nudge_volume(1))
        self.mute_button.clicked.connect(self.toggle_mute)
        vrow.addWidget(self.vol_down)
        vrow.addWidget(self.vol_up)
        vrow.addWidget(self.mute_button)
        v.addLayout(vrow)

        row.addWidget(vol, stretch=5)

        # --- DSP status ---------------------------------------
        stat = Panel()
        s = QVBoxLayout(stat)
        s.setContentsMargins(24, 18, 24, 20)
        s.setSpacing(6)

        s.addWidget(caption("CamillaDSP"))
        self.stats = {}
        for key, name in (
            ("state", "State"),
            ("config", "Config"),
            ("rate", "Sample rate"),
            ("clock", "Clock correction"),
            ("buffer", "Buffer"),
            ("load", "DSP load"),
            ("clipped", "Clipped samples"),
            ("underruns", "Underruns (24 h)"),
            ("resyncs", "DAC re-syncs"),
            ("dropouts", "CarPlay dropouts"),
        ):
            self.stats[key] = StatRow(name)
            s.addWidget(self.stats[key])
        s.addStretch(1)

        row.addWidget(stat, stretch=4)

        # --- Meters -------------------------------------------
        meters = Panel()
        m = QHBoxLayout(meters)
        m.setContentsMargins(24, 18, 24, 16)
        m.setSpacing(40)

        col = QVBoxLayout()
        col.addWidget(caption("Inputs"))
        self.input_meters = LevelMeters(STEM_CONFIG_NAMES)
        col.addWidget(self.input_meters, stretch=1)
        m.addLayout(col, stretch=1)

        col = QVBoxLayout()
        col.addWidget(caption("Outputs (to amps)"))
        self.output_meters = LevelMeters([f"Out {i + 1}" for i in range(8)])
        col.addWidget(self.output_meters, stretch=1)
        m.addLayout(col, stretch=1)

        layout.addWidget(meters, stretch=1)

        return page

    # =========================================================
    # Tab: Crossover & EQ
    # =========================================================

    def build_filters_tab(self):
        content = QWidget()
        self.filters_layout = QVBoxLayout(content)
        self.filters_layout.setContentsMargins(0, 0, 12, 0)
        self.filters_layout.setSpacing(self.SPACING)
        return scroll(content)

    def populate_filters(self):
        clear_layout(self.filters_layout)

        if not self.config:
            self.filters_layout.addWidget(text_label("CamillaDSP not connected", 22, color=COLORS["muted"]))
            return

        labels = self.output_labels()
        filters = self.config.get("filters") or {}

        # How many pipeline steps use each filter (editing one edits all)
        uses = {}
        for step in self.config.get("pipeline") or []:
            if step.get("type") == "Filter":
                for name in step.get("names") or []:
                    uses[name] = uses.get(name, 0) + 1

        for step in self.config.get("pipeline") or []:
            if step.get("type") != "Filter":
                continue

            panel = Panel()
            p = QVBoxLayout(panel)
            p.setContentsMargins(24, 16, 24, 18)
            p.setSpacing(12)

            head = QHBoxLayout()
            channels = step.get("channels") or []
            title = " · ".join(labels[c] if c < len(labels) else f"Out {c + 1}" for c in channels)
            head.addWidget(text_label(title or "All channels", 22, QFont.ExtraBold))
            if step.get("description"):
                head.addWidget(text_label(step["description"], 17, QFont.DemiBold, COLORS["text_secondary"]))
            head.addStretch(1)

            bypass = button("BYPASS", 50, 16, checkable=True)
            bypass.setChecked(bool(step.get("bypassed")))
            bypass.toggled.connect(lambda on, st=step: self.edit(lambda: st.__setitem__("bypassed", on)))
            head.addWidget(bypass)
            p.addLayout(head)

            for name in step.get("names") or []:
                spec = filters.get(name)
                if spec is None:
                    continue
                p.addWidget(self.filter_row(name, spec, uses.get(name, 1)))

            self.filters_layout.addWidget(panel)

        self.filters_layout.addStretch(1)

    def filter_row(self, name, spec, uses):
        row = QWidget()
        r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(16)

        params = spec.get("parameters") or {}
        kind = humanize(params.get("type") or spec.get("type"))

        info = QVBoxLayout()
        info.setSpacing(0)
        info.addWidget(text_label(kind, 20, QFont.ExtraBold, COLORS["accent_bright"]))
        shared = f" · shared by {uses} steps" if uses > 1 else ""
        info.addWidget(text_label(f"{name}{shared}", 15, QFont.DemiBold, COLORS["muted"]))
        info_box = QWidget()
        info_box.setLayout(info)
        info_box.setFixedWidth(330)
        r.addWidget(info_box)

        editable = False

        for key, pspec in PARAM_SPECS.items():
            value = params.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue

            editable = True
            step = pspec["step"]
            minimum = pspec["minimum"]
            formatter = None

            if key == "order":
                # Linkwitz-Riley needs even orders
                if "Linkwitz" in str(params.get("type")):
                    step, minimum = 2, 2
                formatter = lambda v: f"{v:.0f}  ({v * 6:.0f} dB/oct)"

            unit = params.get("unit", pspec["unit"]) if key == "delay" else pspec["unit"]

            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(text_label(pspec["label"], 14, QFont.Bold, COLORS["text_secondary"]))
            stepper = Stepper(
                value, step=step, minimum=minimum, maximum=pspec["maximum"],
                fmt=pspec["fmt"], unit=unit, height=52, value_width=150, formatter=formatter,
            )
            stepper.value_changed.connect(
                lambda v, p=params, k=key: self.edit(
                    lambda: p.__setitem__(k, int(v) if k == "order" else v)
                )
            )
            col.addWidget(stepper)
            r.addLayout(col)

        for key, label in TOGGLE_PARAMS.items():
            if isinstance(params.get(key), bool):
                editable = True
                t = button(label.upper(), 52, 16, checkable=True)
                t.setChecked(params[key])
                t.toggled.connect(
                    lambda on, p=params, k=key: self.edit(lambda: p.__setitem__(k, on))
                )
                r.addWidget(t, alignment=Qt.AlignBottom)

        if not editable:
            r.addWidget(text_label("No adjustable settings (edit in the web GUI)", 17, color=COLORS["muted"]))

        r.addStretch(1)
        return row

    # =========================================================
    # Tab: Mixer
    # =========================================================

    def build_mixer_tab(self):
        content = QWidget()
        self.mixer_layout = QGridLayout(content)
        self.mixer_layout.setContentsMargins(0, 0, 12, 0)
        self.mixer_layout.setSpacing(self.SPACING)
        return scroll(content)

    def populate_mixer(self):
        clear_layout(self.mixer_layout)

        mixer = self.main_mixer()
        if mixer is None:
            self.mixer_layout.addWidget(text_label("No mixer in this config", 22, color=COLORS["muted"]), 0, 0)
            return

        labels = self.output_labels()
        n_in = (mixer.get("channels") or {}).get("in", 0)
        stems_config = n_in == len(STEM_CONFIG_NAMES)
        in_names = (STEM_CONFIG_NAMES if stems_config else
                    INPUT_NAMES if n_in == len(INPUT_NAMES) else [f"In {i + 1}" for i in range(n_in)])

        for i, mapping in enumerate(sorted(mixer.get("mapping") or [], key=lambda m: m.get("dest", 0))):
            dest = mapping.get("dest", 0)

            panel = Panel()
            p = QVBoxLayout(panel)
            p.setContentsMargins(22, 14, 22, 16)
            p.setSpacing(8)

            head = QHBoxLayout()
            head.addWidget(text_label(labels[dest] if dest < len(labels) else f"Out {dest + 1}", 22, QFont.ExtraBold))
            head.addStretch(1)
            mute = button("MUTE OUTPUT", 48, 15, checkable=True)
            mute.setChecked(bool(mapping.get("mute")))
            mute.toggled.connect(lambda on, m=mapping: self.edit(lambda: m.__setitem__("mute", on)))
            head.addWidget(mute)
            p.addLayout(head)

            if stems_config:
                self.add_stems_row(p, dest)

            for source in mapping.get("sources") or []:
                ch = source.get("channel", 0)
                if stems_config and ch >= 2:
                    continue

                r = QHBoxLayout()
                r.setSpacing(10)
                name = text_label(in_names[ch] if ch < len(in_names) else f"In {ch + 1}", 18, QFont.Bold, COLORS["text_secondary"])
                name.setFixedWidth(130)
                r.addWidget(name)

                gain = Stepper(
                    source.get("gain") or 0.0, step=0.5, minimum=-40, maximum=20,
                    fmt="{:+.1f}", unit="dB", height=50, value_width=120,
                )
                gain.value_changed.connect(
                    lambda v, s=source: self.edit(lambda: (s.__setitem__("gain", v), s.__setitem__("scale", "dB")))
                )
                r.addWidget(gain, stretch=1)

                for key, label in (("inverted", "INV"), ("mute", "MUTE")):
                    t = button(label, 50, 15, checkable=True)
                    t.setFixedWidth(84)
                    own_mute = stems_config and key == "mute"
                    if own_mute:
                        # Remembered in the stem state too: the Music page's
                        # CarPlay mute sets these flags and would clear it
                        t.setChecked(bool(S.live_state().get("carplay_source_mute", {}).get(f"{dest}:{ch}")))
                        t.toggled.connect(lambda on, d=dest, c=ch: self.remember_carplay_mute(d, c, on))
                    else:
                        t.setChecked(bool(source.get(key)))
                    t.toggled.connect(lambda on, s=source, k=key: self.edit(lambda: s.__setitem__(k, on)))
                    r.addWidget(t)

                p.addLayout(r)

            p.addStretch(1)
            self.mixer_layout.addWidget(panel, i // 2, i % 2)

    def add_stems_row(self, layout, dest):
        """Everything the stems send to this output: gain, polarity, mute,
        on top of where they're placed on the Music page. Kept in the stem
        state (audio/stems.py `outputs`), not the config: placement rewrites
        the stem gains in the config every time a puck moves."""
        trim = S.output_trim(S.live_state(), dest)
        r = QHBoxLayout()
        r.setSpacing(10)
        name = text_label("Stems", 18, QFont.Bold, COLORS["text_secondary"])
        name.setFixedWidth(130)
        r.addWidget(name)
        gain = Stepper(trim["gain_db"], step=0.5, minimum=-40, maximum=12,
                       fmt="{:+.1f}", unit="dB", height=50, value_width=120)
        gain.value_changed.connect(lambda v, d=dest: self.set_stems_trim(d, "gain_db", v))
        r.addWidget(gain, stretch=1)
        for key, label in (("inverted", "INV"), ("mute", "MUTE")):
            t = button(label, 50, 15, checkable=True)
            t.setFixedWidth(84)
            t.setChecked(trim[key])
            t.toggled.connect(lambda on, d=dest, k=key: self.set_stems_trim(d, k, on))
            r.addWidget(t)
        layout.addLayout(r)

    def remember_carplay_mute(self, dest, ch, on):
        state = S.live_state()
        state.setdefault("carplay_source_mute", {})[f"{dest}:{ch}"] = on
        S.save_state(state)

    def set_stems_trim(self, dest, key, value):
        state = S.live_state()
        state.setdefault("outputs", {}).setdefault(str(dest), {})[key] = value
        S.save_state(state)
        S.live_renderer(CamillaClient).push(state, persist=True)

    # =========================================================
    # Tab: Configs
    # =========================================================

    def build_configs_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.SPACING)

        left = Panel()
        l = QVBoxLayout(left)
        l.setContentsMargins(22, 18, 22, 18)
        l.addWidget(caption(f"Configs in {CONFIG_DIR}"))

        self.config_list = QListWidget()
        self.config_list.setFont(make_font(21, QFont.DemiBold))
        self.config_list.setFocusPolicy(Qt.NoFocus)
        self.config_list.setStyleSheet(f"""
            QListWidget {{ background: transparent; border: none; color: {COLORS["text"]}; outline: none; }}
            QListWidget::item {{
                padding: 0px 18px; margin-bottom: 8px; border-radius: 12px;
                border: 1px solid {COLORS["border"]}; background: {COLORS["background_top"]};
            }}
            QListWidget::item:selected {{
                background: {COLORS["accent_dark"]}; border: 1px solid {COLORS["accent"]};
            }}
        """)
        self.config_list.currentItemChanged.connect(lambda item, _: self.show_config_details(item))
        l.addWidget(self.config_list, stretch=1)
        layout.addWidget(left, stretch=3)

        right = Panel()
        r = QVBoxLayout(right)
        r.setContentsMargins(26, 18, 26, 22)
        r.setSpacing(10)
        r.addWidget(caption("Details"))
        self.config_title = text_label("", 30, QFont.ExtraBold)
        self.config_title.setWordWrap(True)
        r.addWidget(self.config_title)
        self.config_desc = text_label("", 18, QFont.DemiBold, COLORS["text_secondary"])
        self.config_desc.setWordWrap(True)
        r.addWidget(self.config_desc)

        self.config_rows = {}
        for key, name in (("capture", "Input"), ("playback", "Output"), ("rate", "Sample rate"),
                          ("filters", "Filters"), ("file", "File")):
            self.config_rows[key] = StatRow(name)
            r.addWidget(self.config_rows[key])

        r.addStretch(1)
        self.config_message = text_label("", 18, QFont.Bold, COLORS["warning"])
        self.config_message.setWordWrap(True)
        r.addWidget(self.config_message)

        self.load_button = button("LOAD THIS CONFIG", 72, 22)
        self.load_button.clicked.connect(self.load_selected_config)
        r.addWidget(self.load_button)
        layout.addWidget(right, stretch=2)

        return page

    def populate_configs(self):
        current = self.config_list.currentItem()
        selected = current.data(Qt.UserRole) if current else self.config_path

        self.config_list.blockSignals(True)
        self.config_list.clear()

        for path in sorted(CONFIG_DIR.glob("*.yml")):
            if not self.is_camilla_config(path):
                continue

            active = str(path) == self.config_path
            item = QListWidgetItem(("●  " if active else "    ") + path.stem)
            item.setData(Qt.UserRole, str(path))
            item.setSizeHint(QSize(0, 70))
            self.config_list.addItem(item)
            if str(path) == selected:
                self.config_list.setCurrentItem(item)

        self.config_list.blockSignals(False)
        self.show_config_details(self.config_list.currentItem())

    @staticmethod
    def is_camilla_config(path):
        """Skip the statefile and anything else that isn't a DSP config."""

        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            return False

        return isinstance(data, dict) and "devices" in data and "pipeline" in data

    def show_config_details(self, item):
        self.config_message.setText("")

        if item is None:
            self.config_title.setText("Select a config")
            self.config_desc.setText("")
            self.load_button.setEnabled(False)
            return

        path = item.data(Qt.UserRole)
        active = path == self.config_path

        try:
            cfg = yaml.safe_load(Path(path).read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            cfg = {}
            self.config_message.setText(f"Can't read file: {e}")

        devices = cfg.get("devices") or {}
        cap = devices.get("capture") or {}
        pb = devices.get("playback") or {}

        self.config_title.setText(cfg.get("title") or Path(path).stem)
        self.config_desc.setText(cfg.get("description") or "")
        self.config_rows["capture"].set(f"{cap.get('type', '?')} · {cap.get('channels', '?')} ch")
        self.config_rows["playback"].set(f"{pb.get('type', '?')} · {pb.get('channels', '?')} ch")
        self.config_rows["rate"].set(f"{devices.get('samplerate', '?')} Hz")
        self.config_rows["filters"].set(str(len(cfg.get("filters") or {})))
        self.config_rows["file"].set(Path(path).name)

        self.load_button.setEnabled(not active)
        self.load_button.setText("ACTIVE" if active else "LOAD THIS CONFIG")

    def load_selected_config(self):
        item = self.config_list.currentItem()
        if item is None:
            return

        path = item.data(Qt.UserRole)

        try:
            self.camilla.call("ValidateConfig", Path(path).read_text())
            self.camilla.call("SetConfigFilePath", path)
            self.camilla.call("Reload")
        except (OSError, RuntimeError) as e:
            self.config_message.setText(f"Not loaded: {e}")
            return

        self.dirty = False
        self.refresh_config()

    # =========================================================
    # Config state / editing
    # =========================================================

    def main_mixer(self):
        if not self.config:
            return None

        mixers = self.config.get("mixers") or {}
        for step in self.config.get("pipeline") or []:
            if step.get("type") == "Mixer" and step.get("name") in mixers:
                return mixers[step["name"]]
        return None

    def output_labels(self):
        mixer = self.main_mixer()
        n_out = 8

        if mixer:
            n_out = (mixer.get("channels") or {}).get("out", 8)
            labels = mixer.get("labels") or []
            if len(labels) == n_out and all(labels):
                return list(labels)

        return [f"Out {i + 1}" for i in range(n_out)]

    def refresh_config(self):
        """Fetch the running config and rebuild the editors."""

        try:
            self.config = json.loads(self.camilla.call("GetConfigJson"))
            self.config_path = self.camilla.call("GetConfigFilePath")
        except (RuntimeError, ValueError, TypeError):
            self.config = None
            self.config_path = None

        self.output_meters.set_labels(self.output_labels())
        self.populate_filters()
        self.populate_mixer()
        self.populate_configs()
        self.update_edit_status()

    def edit(self, change):
        """Apply a change to the config dict, then push it live shortly."""

        if self.config is None:
            return

        change()
        self.dirty = True
        self.update_edit_status()
        self.apply_timer.start(self.APPLY_DELAY_MS)

    def apply_config(self):
        # self.config is a copy from when the page last loaded it: bring the
        # stem gains up to date first, or this would put back old placement
        S.apply_to_config(self.config, S.live_state())
        try:
            self.camilla.call("SetConfigJson", json.dumps(self.config))
        except RuntimeError as e:
            self.edit_status.setText(f"Rejected: {e}")
            self.edit_status.setStyleSheet(f"color: {COLORS['critical']}; background: transparent;")
            self.refresh_config()

    def save(self):
        if not self.config or not self.config_path:
            return

        self.apply_timer.stop()
        self.apply_config()

        path = Path(self.config_path)

        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            path.write_text(yaml.safe_dump(self.config, sort_keys=False))
        except OSError as e:
            self.edit_status.setText(f"Save failed: {e}")
            return

        self.dirty = False
        self.update_edit_status(f"Saved to {path.name}")

    def revert(self):
        try:
            self.camilla.call("Reload")
        except RuntimeError:
            pass

        self.dirty = False
        self.refresh_config()
        self.update_edit_status("Reverted to saved file")

    def update_edit_status(self, message=None):
        if message:
            text, color = message, COLORS["good"]
        elif self.config is None:
            text, color = "Not connected", COLORS["critical"]
        elif self.dirty:
            text, color = "Changed live · not saved", COLORS["warning"]
        else:
            text, color = "Matches saved file", COLORS["text_secondary"]

        self.edit_status.setText(text)
        self.edit_status.setStyleSheet(f"color: {color}; background: transparent;")

        editing = self.config is not None
        self.save_button.setEnabled(editing and self.dirty)
        self.revert_button.setEnabled(editing and self.dirty)

    # =========================================================
    # Volume / amps
    # =========================================================

    def show_volume(self, volume, mute):
        self.volume_label.setText("—" if volume is None else f"{volume:.1f} dB")
        self.volume_note.setText("MUTED" if mute else "W / S keys also change volume")
        self.volume_note.setStyleSheet(
            f"color: {COLORS['critical'] if mute else COLORS['text_secondary']}; background: transparent;"
        )
        self.mute_button.setChecked(bool(mute))

        if volume is not None and not self.volume_dragging:
            self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(int(round(volume * 2)))
            self.volume_slider.blockSignals(False)

    def volume_slider_moved(self, value):
        db = value / 2
        self.volume_label.setText(f"{db:.1f} dB")
        if not self.volume_dragging:
            self.set_volume(db)

    def volume_released(self):
        self.volume_dragging = False
        self.set_volume(self.volume_slider.value() / 2)

    def set_volume(self, db):
        try:
            volume = self.camilla.set_volume(max(-80.0, min(0.0, db)))
            self.show_volume(volume, self.camilla.get_mute())
        except RuntimeError:
            self.show_volume(None, False)

    def nudge_volume(self, db):
        try:
            self.set_volume(self.camilla.get_volume() + db)
        except RuntimeError:
            pass

    def toggle_mute(self, on):
        try:
            self.camilla.call("SetMute", bool(on))
            self.show_volume(self.camilla.get_volume(), self.camilla.get_mute())
        except RuntimeError:
            pass

    AMP_WARM_F = 140     # 60 °C
    AMP_HOT_F = 158      # 70 °C

    def update_values(self):
        """Sensor data (DashboardPage.update_sensors): amp box temperature."""
        info = self.sensor_data.get("amp_box") or {}
        temp_f = (info.get("data") or {}).get("temperature_f")
        if info.get("status") != "OK" or temp_f is None:
            self.amp_temp.setText("")
            return
        color = (COLORS["critical"] if temp_f >= self.AMP_HOT_F else
                 COLORS["warning"] if temp_f >= self.AMP_WARM_F else COLORS["text_secondary"])
        self.amp_temp.setText(f"BOX {temp_f:.0f}°F")
        self.amp_temp.setStyleSheet(f"color: {color}; background: transparent;")

    def set_amp_mode(self, mode):
        self.show_amps(set_amp_mode(mode))

    def show_amps(self, status):
        if status is None:
            self.amp_state.setText("OFFLINE")
            self.amp_state.setStyleSheet(f"color: {COLORS['critical']}; background: transparent;")
            self.amp_reason.setText("Amp controller service is not running")
            self.amp_pin.setText("")
            return

        resyncs = status.get("dac_resyncs")
        if resyncs is not None:
            self.stats["resyncs"].set(str(resyncs))
        # Phone audio that cut out mid-song since the Pi started (amp_controller.py)
        dropouts = status.get("carplay_dropouts")
        if dropouts is not None:
            self.stats["dropouts"].set(str(dropouts), COLORS["warning"] if dropouts else None)

        on = status.get("amps_on")
        self.amp_state.setText("ON" if on else "OFF")
        self.amp_state.setStyleSheet(
            f"color: {COLORS['good'] if on else COLORS['text_secondary']}; background: transparent;"
        )

        reason = status.get("reason", "")
        if status.get("error"):
            reason = status["error"]
        self.amp_reason.setText(reason)
        self.amp_pin.setText(f"GPIO {status.get('pin')}")

        self.amp_mode.set_selected(status.get("mode"))
        self.amp_idle.set_value(status.get("idle_off_s", 0))

    # =========================================================
    # Polling
    # =========================================================

    def poll_levels(self):
        if self.stack.currentIndex() != self.tab_index["overview"]:
            return

        try:
            levels = self.camilla.call("GetSignalLevels")
        except RuntimeError:
            return

        self.input_meters.set_levels(levels.get("capture_rms", []), levels.get("capture_peak", []))
        self.output_meters.set_levels(levels.get("playback_rms", []), levels.get("playback_peak", []))

    def poll_status(self):
        self.show_amps(amp_status())

        try:
            state = self.camilla.get_state()
            volume = self.camilla.get_volume()
            mute = self.camilla.get_mute()
            rate = self.camilla.call("GetCaptureRate")
            adjust = self.camilla.call("GetRateAdjust")
            buffer = self.camilla.call("GetBufferLevel")
            load = self.camilla.call("GetProcessingLoad")
            clipped = self.camilla.call("GetClippedSamples")
            path = self.camilla.call("GetConfigFilePath")

        except RuntimeError:
            self.header.set_subtitle("CamillaDSP offline", COLORS["critical"])
            self.stats["state"].set("OFFLINE", COLORS["critical"])
            self.show_volume(None, False)
            self.config = None
            self.update_edit_status()
            return

        if self.config is None or (path != self.config_path and not self.dirty):
            self.refresh_config()

        title = (self.config or {}).get("title") or Path(path or "").stem

        self.header.set_subtitle(
            f"CamillaDSP {state.lower()} · {title}",
            COLORS["good"] if state == "Running" else COLORS["warning"],
        )

        self.stats["state"].set(state.upper(), COLORS["good"] if state == "Running" else COLORS["warning"])
        self.stats["config"].set(Path(path or "").stem or "—")
        self.stats["rate"].set(f"{rate} Hz")
        self.stats["clock"].set("—" if not adjust else f"{(adjust - 1) * 1e6:+.0f} ppm")
        self.stats["buffer"].set(f"{buffer} frames")
        self.stats["load"].set(f"{load:.1f} %")
        self.stats["clipped"].set(str(clipped), COLORS["critical"] if clipped else None)
        self.update_underruns()

        self.show_volume(volume, mute)

    def update_underruns(self):
        """Playback underruns in CamillaDSP's log over the last 24 h. Each can
        leave the DAC8x's channels out of step until the amp controller's
        DAC guard reopens it (DAC re-syncs). Counted once a minute."""
        now = time.monotonic()
        if now - getattr(self, "underruns_checked", -1e9) < 60:
            return
        self.underruns_checked = now
        try:
            out = subprocess.run(["journalctl", "-u", "camilladsp", "--since", "-24h", "-q", "--no-pager",
                                  "-g", "underrun"], capture_output=True, text=True, timeout=5).stdout
            count = len([line for line in out.splitlines() if line.strip()])
            self.stats["underruns"].set(str(count), COLORS["good"] if count == 0 else COLORS["warning"])
        except (OSError, subprocess.TimeoutExpired):
            self.stats["underruns"].set("—")

    def show_tab(self, key):
        self.stack.setCurrentIndex(self.tab_index[key])
        self.toolbar.setVisible(key in ("filters", "mixer"))

        if key == "configs":
            self.populate_configs()

    def showEvent(self, event):
        super().showEvent(event)
        self.poll_status()
        self.fast_timer.start(self.FAST_MS)
        self.slow_timer.start(self.SLOW_MS)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.fast_timer.stop()
        self.slow_timer.stop()
