import shutil

from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics

from dashboard.widgets.gauge_card import GaugeCard
from dashboard.theme import COLORS, make_font, format_value
from dashboard.pages.base import DashboardPage


SENSOR_LABELS = {
    "amp_box": "Amp box MCP9808",
    "outside": "Outside SHT45",
    "pressure": "Baro BMP388",
    "light": "Light TSL2591",
    "analog": "Analog ADS1115",
    "imu": "IMU BNO085",
}


class SectionLabel(QLabel):

    def __init__(self, text):
        super().__init__(text.upper())

        self.setFont(make_font(15, QFont.Bold, spacing=3))
        self.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        self.setFixedHeight(20)


SENSOR_ADDRESS = {
    "amp_box": "0x18", "outside": "0x44", "pressure": "0x77", "light": "0x29",
    "analog": "0x48", "imu": "0x4A", "tach": "GPIO 12", "acc": "GPIO 10", "battery": "ADS A3",
}
SENSOR_LABELS["tach"] = "Tach (HEI)"
SENSOR_LABELS["acc"] = "Key (ACC)"
SENSOR_LABELS["battery"] = "Start battery"


def _num(v, fmt):
    return "--" if v is None else format(v, fmt)


def sensor_detail(name, info):
    """(ok, one-line detail) for a sensor entry from SensorManager.get_data()."""
    status = (info or {}).get("status")
    data = (info or {}).get("data") or {}
    error = (info or {}).get("error") or ""

    if status != "OK":
        if "No I2C device" in error:
            return False, "Not connected"
        if "Remote I/O" in error or "Errno 121" in error:
            return False, "Not responding"
        if "holding the I2C bus" in error:
            return False, "Held in reset: was holding the I2C bus low"
        if name == "tach":
            return None, "No ignition signal yet"
        if name == "battery":
            return None, "Not connected to A3" if "not connected" in error else "No A3 reading"
        if name == "acc":
            return False, (error[:60] or "Can't read GPIO 10")
        return False, (error[:60] or "Offline")

    if name == "amp_box":
        return True, f"{_num(data.get('temperature_f'), '.0f')} °F"
    if name == "outside":
        t = data.get("temperature_c")
        return True, f"{_num(None if t is None else t * 9 / 5 + 32, '.0f')} °F · {_num(data.get('humidity'), '.0f')} %"
    if name == "pressure":
        return True, f"{_num(data.get('pressure'), '.1f')} hPa · {_num(data.get('temperature_c'), '.1f')} °C"
    if name == "light":
        return True, f"{_num(data.get('lux'), ',.0f')} lx"
    if name == "battery":
        return True, f"{_num(data.get('voltage'), '.2f')} V · A3 {_num(data.get('a3_volts'), '.2f')} V"
    if name == "acc":
        state = "ON" if data.get("key_on") else "OFF"
        return True, f"Key {state} · {_num(data.get('since_s'), '.0f')} s"
    if name == "analog":
        volts = [(data.get(c) or {}).get("voltage") for c in ("A0", "A1", "A2")]
        return True, "  ".join(f"A{i} {_num(v, '.2f')}" for i, v in enumerate(volts)) + " V"
    if name == "imu":
        return True, (f"{_num(data.get('heading'), '.0f')}° · roll {_num(data.get('roll'), '+.0f')}°"
                      f" · pitch {_num(data.get('pitch'), '+.0f')}°")
    if name == "tach":
        return True, f"{_num(data.get('rpm'), '.0f')} rpm"
    return True, "OK"


class SensorTable(QWidget):
    """
    Every sensor with its address, a status dot and a one-line detail (the
    live reading, or why it's down), plus I2C bus health as the last row.
    """

    COLUMNS = 3
    ROW_H = 48

    def __init__(self):
        super().__init__()

        self.rows = []              # (label, address, ok, detail)
        self.setMinimumHeight(self.ROW_H * 3 + 16)

    def set_rows(self, rows):
        if rows != self.rows:
            self.rows = rows
            self.setMinimumHeight(self.ROW_H * max(3, -(-len(rows) // self.COLUMNS)) + 16)
            self.update()

    def paintEvent(self, event):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QColor(COLORS["border"]))
        p.setBrush(QColor(COLORS["card"]))
        p.drawRoundedRect(rect, 16, 16)

        gap = 22
        col_w = (rect.width() - 36 - gap * (self.COLUMNS - 1)) / self.COLUMNS
        name_font = make_font(16, QFont.Bold)
        addr_font = make_font(13, QFont.Bold)
        detail_font = make_font(15, QFont.DemiBold)

        for i, (label, address, ok, detail) in enumerate(self.rows):
            col, row = i % self.COLUMNS, i // self.COLUMNS
            x = rect.left() + 18 + col * (col_w + gap)
            y = rect.top() + 8 + row * self.ROW_H
            dot = COLORS["muted"] if ok is None else COLORS["good"] if ok else COLORS["critical"]

            p.setPen(Qt.NoPen)
            p.setBrush(QColor(dot))
            p.drawEllipse(QRectF(x, y + self.ROW_H / 2 - 6, 12, 12))

            p.setFont(name_font)
            p.setPen(QColor(COLORS["text"]))
            p.drawText(QRectF(x + 22, y + 3, col_w * 0.42, 24), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(name_font).elidedText(label, Qt.ElideRight, int(col_w * 0.42)))

            p.setFont(addr_font)
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(QRectF(x + 22, y + 26, col_w * 0.42, 16), Qt.AlignLeft | Qt.AlignVCenter, address)

            p.setFont(detail_font)
            p.setPen(QColor(COLORS["text_secondary"] if ok else COLORS["text_secondary"]))
            text = QFontMetrics(detail_font).elidedText(detail, Qt.ElideRight, int(col_w * 0.58 - 26))
            p.drawText(QRectF(x + col_w * 0.42 + 26, y, col_w * 0.58 - 26, self.ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter, text)

        p.end()


class DiagnosticsPage(DashboardPage):

    def __init__(self):
        super().__init__("System Diagnostics")

        # CPU usage calculation state
        self.last_cpu_total = None
        self.last_cpu_idle = None

        # --------------------
        # System
        # --------------------

        self.layout_.addWidget(SectionLabel("Raspberry Pi"))

        self.cards = {
            "cpu_temp": GaugeCard("CPU Temp", "°C", 30, 90),
            "cpu_usage": GaugeCard("CPU Usage", "%", 0, 100),
            "ram": GaugeCard("RAM Used", "%", 0, 100),
            "disk": GaugeCard("Disk Used", "%", 0, 100),
            "cpu_freq": GaugeCard("CPU Speed", "MHz"),
            "uptime": GaugeCard("Uptime"),
        }

        self.layout_.addLayout(
            self.add_card_grid(list(self.cards.values()), columns=6),
            stretch=1
        )

        # --------------------
        # Audio / CarPlay
        # --------------------

        self.layout_.addWidget(SectionLabel("Audio & CarPlay"))

        self.audio_cards = {
            "camilla": GaugeCard("CamillaDSP"),
            "volume": GaugeCard("Main Volume", "dB", -60, 0),
            "carplay": GaugeCard("CarPlay (LIVI)"),
            "route": GaugeCard("Audio Route"),
            "amps": GaugeCard("Amplifiers"),
        }

        self.layout_.addLayout(
            self.add_card_grid(list(self.audio_cards.values()), columns=5),
            stretch=1
        )

        # --------------------
        # Sensors
        # --------------------

        self.layout_.addWidget(SectionLabel("Sensors"))

        self.sensor_table = SensorTable()
        self.layout_.addWidget(self.sensor_table)
        self.i2c_health = (None, "Checking…")
        self.i2c_timer = QTimer(self)
        self.i2c_timer.timeout.connect(self.check_i2c)
        self.i2c_timer.start(15000)
        QTimer.singleShot(500, self.check_i2c)

        # System stats refresh on their own so this page never
        # freezes, even if the sensor thread stalls.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_values)
        self.timer.start(2000)

        self.update_values()

    # --------------------------------------------------
    # Called by main.py
    # --------------------------------------------------

    def update_sensors(self, data):
        self.sensor_data = data

        self.refresh_sensor_table()

        sensors = {name: info for name, info in data.items() if name != "engine"}   # engine log summary
        online = sum(1 for info in sensors.values() if info.get("status") == "OK")
        self.header.set_subtitle(
            f"{online} of {len(sensors)} sensors online",
            COLORS["good"] if online == len(sensors) else COLORS["warning"],
        )

    def refresh_sensor_table(self):
        data = self.sensor_data or {}
        rows = []
        for name in list(SENSOR_LABELS):
            ok, detail = sensor_detail(name, data.get(name))
            rows.append((SENSOR_LABELS[name], SENSOR_ADDRESS.get(name, ""), ok, detail))
        ok, detail = self.i2c_health
        rows.append(("I2C bus", "GPIO 2 / 3", ok, detail))
        self.sensor_table.set_rows(rows)

    def check_i2c(self):
        """Kernel I2C controller timeouts in the last 10 minutes (a stuck bus
        shows up here first). Needs journal access (adm group)."""
        import subprocess
        try:
            out = subprocess.run(
                ["journalctl", "-k", "--since", "-10min", "-q", "--no-pager", "-g", "controller timed out"],
                capture_output=True, text=True, timeout=5).stdout
            count = len([line for line in out.splitlines() if line.strip()])
            self.i2c_health = (count == 0, "Healthy · no timeouts (10 min)" if count == 0
                               else f"{count} timeouts in 10 min")
        except (OSError, subprocess.TimeoutExpired):
            self.i2c_health = (None, "Can't read the kernel log")
        self.refresh_sensor_table()

    def set_audio_status(self, status):
        """
        status keys: camilla_state, volume_db, carplay_running,
        carplay_window, route_ok, amps (amp controller status or None)
        """

        state = status.get("camilla_state")
        card = self.audio_cards["camilla"]

        if state is None:
            card.set_value("OFFLINE")
            card.set_status("critical")
        else:
            card.set_value(state.upper())
            card.set_status("good" if state == "Running" else "warning")

        volume = status.get("volume_db")
        self.audio_cards["volume"].set_value(format_value(volume))

        card = self.audio_cards["carplay"]

        if status.get("carplay_window"):
            card.set_value("RUNNING")
            card.set_status("good")
        elif status.get("carplay_running"):
            card.set_value("STARTING")
            card.set_status("warning")
        else:
            card.set_value("STOPPED")
            card.set_status("critical")

        card = self.audio_cards["route"]

        if status.get("route_ok"):
            card.set_value("LINKED")
            card.set_status("good")
        else:
            card.set_value("NOT LINKED")
            card.set_status("critical")

        card = self.audio_cards["amps"]
        amps = status.get("amps")

        if amps is None:
            card.set_value("NO SERVICE")
            card.set_status("critical")
        elif amps.get("error"):
            card.set_value("GPIO ERROR")
            card.set_status("critical")
        else:
            mode = amps.get("mode", "auto").upper()
            card.set_value(f"{'ON' if amps.get('amps_on') else 'OFF'} · {mode}")
            card.set_status("good" if amps.get("amps_on") else "normal")

    # --------------------------------------------------
    # CPU temperature
    # --------------------------------------------------

    def get_cpu_temp(self):

        paths = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/hwmon/hwmon0/temp1_input",
        ]

        for path in paths:

            try:

                with open(path, "r") as f:
                    value = float(f.read().strip())

                # Most Linux thermal sensors report millidegrees C
                if value > 1000:
                    value /= 1000.0

                return value

            except (FileNotFoundError, ValueError, PermissionError):
                continue

        return None

    # --------------------------------------------------
    # CPU usage
    # --------------------------------------------------

    def get_cpu_usage(self):

        try:

            with open("/proc/stat", "r") as f:
                line = f.readline()

            parts = line.split()

            if parts[0] != "cpu":
                return None

            values = [int(x) for x in parts[1:]]

            total = sum(values)

            # idle + iowait
            idle = values[3] + values[4]

            if self.last_cpu_total is None:

                self.last_cpu_total = total
                self.last_cpu_idle = idle

                return 0.0

            total_delta = total - self.last_cpu_total
            idle_delta = idle - self.last_cpu_idle

            self.last_cpu_total = total
            self.last_cpu_idle = idle

            if total_delta <= 0:
                return 0.0

            usage = (1.0 - idle_delta / total_delta) * 100.0

            return max(0.0, min(100.0, usage))

        except (FileNotFoundError, ValueError, IndexError):
            return None

    # --------------------------------------------------
    # RAM usage
    # --------------------------------------------------

    def get_ram_usage(self):

        try:

            mem_total = None
            mem_available = None

            with open("/proc/meminfo", "r") as f:

                for line in f:

                    if line.startswith("MemTotal:"):
                        mem_total = int(line.split()[1])

                    elif line.startswith("MemAvailable:"):
                        mem_available = int(line.split()[1])

            if mem_total is None or mem_available is None:
                return None

            return (mem_total - mem_available) / mem_total * 100.0

        except (FileNotFoundError, ValueError, ZeroDivisionError):
            return None

    # --------------------------------------------------
    # Disk usage
    # --------------------------------------------------

    def get_disk_usage(self):

        try:

            usage = shutil.disk_usage("/")

            return usage.used / usage.total * 100.0

        except OSError:
            return None

    # --------------------------------------------------
    # CPU frequency
    # --------------------------------------------------

    def get_cpu_freq(self):

        paths = [
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq",
            "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq",
        ]

        for path in paths:

            try:

                with open(path, "r") as f:
                    value = float(f.read().strip())

                # Linux reports kHz
                return value / 1000.0

            except (FileNotFoundError, ValueError, PermissionError):
                continue

        return None

    # --------------------------------------------------
    # Uptime
    # --------------------------------------------------

    def get_uptime(self):

        try:

            with open("/proc/uptime", "r") as f:
                seconds = int(float(f.readline().split()[0]))

            days = seconds // 86400
            seconds %= 86400

            hours = seconds // 3600
            seconds %= 3600

            minutes = seconds // 60

            if days > 0:
                return f"{days}d {hours}h"

            if hours > 0:
                return f"{hours}h {minutes}m"

            return f"{minutes}m"

        except (FileNotFoundError, ValueError, IndexError):
            return None

    # --------------------------------------------------
    # Update displayed values
    # --------------------------------------------------

    @staticmethod
    def _threshold_status(card, value, warning, critical):
        if value is None:
            return

        if value > critical:
            card.set_status("critical")
        elif value > warning:
            card.set_status("warning")
        else:
            card.set_status("good")

    def update_values(self):

        cpu_temp = self.get_cpu_temp()
        cpu_usage = self.get_cpu_usage()
        ram = self.get_ram_usage()
        disk = self.get_disk_usage()
        cpu_freq = self.get_cpu_freq()

        self.cards["cpu_temp"].set_value(format_value(cpu_temp))
        self.cards["cpu_usage"].set_value(format_value(cpu_usage, 0))
        self.cards["ram"].set_value(format_value(ram, 0))
        self.cards["disk"].set_value(format_value(disk, 0))
        self.cards["cpu_freq"].set_value(format_value(cpu_freq, 0))
        self.cards["uptime"].set_value(self.get_uptime())

        self._threshold_status(self.cards["cpu_temp"], cpu_temp, 65, 80)
        self._threshold_status(self.cards["cpu_usage"], cpu_usage, 70, 90)
        self._threshold_status(self.cards["ram"], ram, 75, 90)
        self._threshold_status(self.cards["disk"], disk, 85, 95)

        if cpu_freq is not None:
            self.cards["cpu_freq"].set_status("good")
