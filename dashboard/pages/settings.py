"""
Settings: display brightness and night dimming, CarPlay (LIVI's own
settings, restart), UPS behaviour when parked, network, and system
(restart the dashboard, exit). Replaces the old EXIT button.

main.py wires the actions: on_livi_settings, on_restart_carplay,
on_restart_dashboard, on_exit, and gives it the NightDimmer.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QSlider, QVBoxLayout

from dashboard.pages.base import DashboardPage
from dashboard.theme import COLORS
from dashboard.widgets.controls import Panel, SegmentedControl, StatRow, button, caption, text_label

ROOT = Path(__file__).resolve().parent.parent.parent
UPS_SETTINGS = Path.home() / ".config/jeep_computer/ups.json"
UPS_STATUS = Path("/run/jeep-ups/status.json")
LIVI_LOWLATENCY = Path.home() / "LIVI/LIVI-lowlatency/JEEP-PATCH.txt"
HOTSPOT = Path.home() / ".config/jeep_computer/hotspot.json"

SLIDER_STYLE = f"""
    QSlider::groove:horizontal {{
        height: 12px; border-radius: 6px; background: {COLORS["border"]};
    }}
    QSlider::sub-page:horizontal {{
        border-radius: 6px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {COLORS["accent_dark"]}, stop:1 {COLORS["accent_bright"]});
    }}
    QSlider::handle:horizontal {{
        width: 40px; height: 40px; margin: -14px 0; border-radius: 20px;
        background: {COLORS["text"]}; border: 3px solid {COLORS["accent"]};
    }}
"""


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def run(args, timeout=3):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


class SettingsPage(DashboardPage):

    REFRESH_MS = 3000

    def __init__(self):
        super().__init__("Settings")

        self.dimmer = None
        self.on_livi_settings = None
        self.on_restart_carplay = None
        self.on_restart_dashboard = None
        self.on_exit = None
        self.carplay_state = ("—", None)

        grid = QGridLayout()
        grid.setSpacing(self.SPACING)
        self.layout_.addLayout(grid, stretch=1)

        grid.addWidget(self.display_panel(), 0, 0)
        grid.addWidget(self.carplay_panel(), 0, 1)
        grid.addWidget(self.power_panel(), 0, 2)
        grid.addWidget(self.network_panel(), 1, 0)
        grid.addWidget(self.speaker_panel(), 1, 1)
        grid.addWidget(self.system_panel(), 1, 2)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 2)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.version.set(self.git_version())

    # ------------------------------------------------------------
    # Panels
    # ------------------------------------------------------------

    def panel(self, title):
        panel = Panel()
        col = QVBoxLayout(panel)
        col.setContentsMargins(24, 18, 24, 20)
        col.setSpacing(10)
        col.addWidget(caption(title))
        return panel, col

    def display_panel(self):
        panel, col = self.panel("Display")

        row = QHBoxLayout()
        row.addWidget(text_label("Brightness", 20, QFont.DemiBold, COLORS["text_secondary"]))
        row.addStretch(1)
        self.brightness_value = text_label("100 %", 24, QFont.ExtraBold)
        row.addWidget(self.brightness_value)
        col.addLayout(row)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(30, 100)
        self.slider.setSingleStep(5)
        self.slider.setPageStep(10)
        self.slider.setFixedHeight(56)
        self.slider.setStyleSheet(SLIDER_STYLE)
        self.slider.valueChanged.connect(self.brightness_changed)
        col.addWidget(self.slider)

        col.addWidget(text_label("Dim automatically at night", 18, QFont.DemiBold, COLORS["text_secondary"]))
        self.auto_dim = SegmentedControl([("on", "AUTO"), ("off", "OFF")], height=58, size=18)
        self.auto_dim.selected.connect(lambda key: self.dimmer and self.dimmer.set_auto(key == "on"))
        col.addWidget(self.auto_dim)

        self.light = StatRow("Cabin light")
        self.dimming = StatRow("Dimming now")
        col.addWidget(self.light)
        col.addWidget(self.dimming)
        col.addStretch(1)
        return panel

    def carplay_panel(self):
        panel, col = self.panel("CarPlay")
        self.phone = StatRow("iPhone")
        self.buffer = StatRow("Audio buffer")
        col.addWidget(self.phone)
        col.addWidget(self.buffer)
        col.addStretch(1)

        livi = button("LIVI SETTINGS", height=64, size=19)
        livi.clicked.connect(lambda: self.on_livi_settings and self.on_livi_settings())
        col.addWidget(livi)
        note = text_label("Opens in the CarPlay tab (even while CarPlay is running). "
                          "Leave the tab to close them.", 15, QFont.DemiBold, COLORS["muted"])
        note.setWordWrap(True)
        col.addWidget(note)

        restart = button("RESTART CARPLAY", height=58, size=17)
        restart.clicked.connect(lambda: self.on_restart_carplay and self.on_restart_carplay())
        col.addWidget(restart)
        return panel

    def power_panel(self):
        panel, col = self.panel("Power · UPS")
        self.ups_mode = StatRow("Mode")
        self.ups_battery = StatRow("UPS battery")
        col.addWidget(self.ups_mode)
        col.addWidget(self.ups_battery)

        col.addWidget(text_label("Log weather while parked", 18, QFont.DemiBold, COLORS["text_secondary"]))
        self.station = SegmentedControl([("on", "ON"), ("off", "OFF")], height=54, size=17)
        self.station.selected.connect(lambda key: self.set_ups("station_mode", key == "on"))
        col.addWidget(self.station)

        col.addWidget(text_label("Sleep between readings on low battery", 18, QFont.DemiBold,
                                 COLORS["text_secondary"]))
        self.sleep = SegmentedControl([("on", "ON"), ("off", "OFF")], height=54, size=17)
        self.sleep.selected.connect(lambda key: self.set_ups("duty_cycle", key == "on"))
        col.addWidget(self.sleep)

        self.wake = StatRow("Clock wake")
        col.addWidget(self.wake)
        col.addStretch(1)
        return panel

    def network_panel(self):
        panel, col = self.panel("Network")
        self.wifi = StatRow("Wi-Fi")
        self.signal = StatRow("Signal")
        self.ip = StatRow("Address")
        self.web = StatRow("Phone page")
        self.hotspot = StatRow("Parked hotspot")
        for row in (self.wifi, self.signal, self.ip, self.web, self.hotspot):
            col.addWidget(row)
        col.addStretch(1)

        hot = QHBoxLayout()
        hot.addWidget(text_label("Hotspot when parked away", 16, QFont.DemiBold, COLORS["text_secondary"]))
        self.hotspot_switch = SegmentedControl([("on", "ON"), ("off", "OFF")], height=46, size=15)
        self.hotspot_switch.selected.connect(self.set_hotspot)
        hot.addWidget(self.hotspot_switch)
        col.addLayout(hot)
        return panel

    SPEAKERS = [("FRONT L", 1), ("FRONT R", 2), ("REAR L", 3), ("REAR R", 4),
                ("8\" L", 5), ("8\" R", 6), ("SUBS", 7)]

    def speaker_panel(self):
        """Tap a speaker: pink noise on that DAC output only (tools/speaker_test.py)."""
        panel, col = self.panel("Speaker check")
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (label, out) in enumerate(self.SPEAKERS):
            b = button(label, height=48, size=15)
            outs = ["7", "8"] if out == 7 else [str(out)]      # both sub outputs
            b.clicked.connect(lambda _=False, o=outs, l=label: self.test_speaker([*o, "--no-beeps"], l))
            grid.addWidget(b, i // 2, i % 2)
        every = button("ALL IN ORDER", height=48, size=15)
        every.clicked.connect(lambda: self.test_speaker([], "all 8 outputs (N beeps = output N)"))
        grid.addWidget(every, 3, 1)
        col.addLayout(grid)
        resync = button("RE-SYNC DAC", height=48, size=15)
        resync.clicked.connect(lambda: self.test_speaker(["--resync", "1", "--no-beeps"], "DAC re-synced, front L"))
        col.addWidget(resync)
        self.speaker_status = text_label("Plays at the current volume", 14, QFont.DemiBold, COLORS["muted"])
        self.speaker_status.setWordWrap(True)
        col.addWidget(self.speaker_status)
        col.addStretch(1)
        self.speaker_process = None
        return panel

    def test_speaker(self, args, label):
        if self.speaker_process is not None and self.speaker_process.state() != QProcess.NotRunning:
            return                                       # one test at a time
        self.speaker_process = QProcess(self)
        self.speaker_process.setWorkingDirectory(str(ROOT))
        self.speaker_process.finished.connect(
            lambda *_: self.speaker_status.setText("Done. Wrong speaker? Try RE-SYNC DAC"))
        self.speaker_process.start(str(ROOT / ".venv/bin/python"), ["tools/speaker_test.py", *args])
        self.speaker_status.setText(f"Playing: {label}")

    def system_panel(self):
        panel, col = self.panel("System")
        stats = QGridLayout()
        stats.setHorizontalSpacing(40)
        self.version = StatRow("Version")
        self.uptime = StatRow("Uptime")
        self.cpu = StatRow("CPU")
        self.disk = StatRow("Storage")
        for i, row in enumerate((self.version, self.uptime, self.cpu, self.disk)):
            stats.addWidget(row, i, 0)
        col.addLayout(stats)
        col.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        restart = button("RESTART", height=58, size=17)
        restart.clicked.connect(lambda: self.on_restart_dashboard and self.on_restart_dashboard())
        leave = button("EXIT", height=58, size=17)
        leave.clicked.connect(lambda: self.on_exit and self.on_exit())
        buttons.addWidget(restart, stretch=2)
        buttons.addWidget(leave, stretch=1)
        col.addLayout(buttons)
        return panel

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------

    def attach_dimmer(self, dimmer):
        self.dimmer = dimmer
        if dimmer is None:
            self.slider.setEnabled(False)
            return
        self.slider.blockSignals(True)
        self.slider.setValue(round(dimmer.settings["brightness"] * 100))
        self.slider.blockSignals(False)
        self.brightness_value.setText(f"{self.slider.value()} %")
        self.auto_dim.set_selected("on" if dimmer.settings["auto_dim"] else "off")

    def brightness_changed(self, value):
        self.brightness_value.setText(f"{value} %")
        if self.dimmer is not None:
            self.dimmer.set_brightness(value / 100)

    def set_ups(self, key, value):
        settings = read_json(UPS_SETTINGS)
        settings[key] = value
        UPS_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        UPS_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")

    def set_hotspot(self, key):
        settings = read_json(HOTSPOT)
        if not settings:
            return
        settings["enabled"] = key == "on"
        HOTSPOT.write_text(json.dumps(settings, indent=2) + "\n")

    def set_carplay_state(self, text, color=None):
        self.carplay_state = (text, color)

    # ------------------------------------------------------------
    # Live values (only while the page is open)
    # ------------------------------------------------------------

    def showEvent(self, event):
        self.refresh()
        self.timer.start(self.REFRESH_MS)

    def hideEvent(self, event):
        self.timer.stop()

    @staticmethod
    def git_version():
        out = run(["git", "-C", str(ROOT), "log", "-1", "--format=%h · %cd", "--date=format:%b %d %H:%M"])
        return out or "—"

    def refresh(self):
        # Display
        if self.dimmer is not None:
            lux = self.dimmer.lux
            self.light.set("—" if lux is None else f"{lux:,.0f} lx")
            self.dimming.set(f"{self.dimmer.level * 100:.0f} %")

        # CarPlay
        self.phone.set(*self.carplay_state)
        if LIVI_LOWLATENCY.exists():
            first = LIVI_LOWLATENCY.read_text().splitlines()[0]    # "mediaDelay 1000 ms -> 500 ms ..."
            ms = first.split("->")[-1].split("ms")[0].strip()
            self.buffer.set(f"{ms} ms (low latency)", COLORS["good"])
        else:
            self.buffer.set("1000 ms (stock LIVI)")

        # UPS
        ups = read_json(UPS_STATUS)
        mode = {"vehicle": "Jeep on", "grace": "Jeep off · waiting", "station": "Weather station",
                "duty": "Sleep cycle", "shutting_down": "Shutting down"}.get(ups.get("mode"), "UPS monitor off")
        self.ups_mode.set(mode)
        v, pct = ups.get("battery_voltage"), ups.get("battery_percent")
        text = "—" if v is None else f"{v:.2f} V · {pct:.0f} %"
        rate, full = ups.get("charge_rate"), ups.get("hours_to_full")
        if full is not None:
            text += f" · full in ~{full:.1f} h"
        elif rate is not None and ups.get("vehicle_power") and rate > 0.5:
            text += " · charging"
        self.ups_battery.set(text)
        settings = {"station_mode": True, "duty_cycle": True, **read_json(UPS_SETTINGS)}
        self.station.set_selected("on" if settings["station_mode"] else "off")
        self.sleep.set_selected("on" if settings["duty_cycle"] else "off")
        verified = ups.get("duty_verified")
        self.wake.set("Works" if verified else "Not tested yet",
                      COLORS["good"] if verified else COLORS["text_secondary"])

        # Network
        fields = run(["nmcli", "-t", "-g", "GENERAL.CONNECTION,IP4.ADDRESS", "device", "show", "wlan0"]).splitlines()
        ssid = fields[0] if fields and fields[0] else None
        ip = fields[1].split("/")[0] if len(fields) > 1 and fields[1] else None
        link = run(["iw", "dev", "wlan0", "link"])
        signal = next((line.split()[1] for line in link.splitlines() if "signal:" in line), None)
        self.wifi.set(ssid or "Not connected", COLORS["good"] if ssid else COLORS["warning"])
        self.signal.set("—" if signal is None else f"{signal} dBm")
        self.ip.set(ip or "—")
        on_hotspot = ssid == "jeep-hotspot"
        if on_hotspot:
            self.wifi.set("Hotspot on", COLORS["good"])
        self.web.set("10.42.0.1:8080" if on_hotspot else "raspberrypi.local:8080" if ssid else "—")
        hotspot = read_json(HOTSPOT)
        if hotspot:
            self.hotspot.set(f"{hotspot.get('ssid')} · {hotspot.get('password')}")
            self.hotspot_switch.set_selected("on" if hotspot.get("enabled") else "off")
        else:
            self.hotspot.set("Not set up")

        # System
        try:
            seconds = float(Path("/proc/uptime").read_text().split()[0])
            self.uptime.set(f"{int(seconds // 3600)} h {int(seconds % 3600 // 60):02d} m")
        except OSError:
            pass
        try:
            temp = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
            load = os.getloadavg()[0]
            self.cpu.set(f"{temp:.0f} °C · load {load:.1f}",
                         COLORS["warning"] if temp > 75 else None)
        except OSError:
            pass
        usage = shutil.disk_usage("/")
        self.disk.set(f"{usage.free / 1e9:.0f} GB free")
