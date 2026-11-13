import os
import sys
import json
import time
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QWidget,
    QVBoxLayout,
    QMessageBox,
    QSizePolicy,
)

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtNetwork import QLocalServer

from dashboard.widgets.top_bar import TopBar
from dashboard.widgets.nav_bar import NavBar
from dashboard.widgets.volume_overlay import VolumeOverlay
from dashboard.widgets.alert_banner import AlertBanner
from dashboard.widgets.page_fader import PageFader
from dashboard.theme import COLORS

from dashboard.pages.carplay import CarPlayPage
from dashboard.pages.home import HomePage
from dashboard.pages.weather import WeatherPage
from dashboard.pages.offroad import OffroadPage
from dashboard.pages.electrical import ElectricalPage
from dashboard.pages.diagnostics import DiagnosticsPage
from dashboard.pages.music import MusicPlayerPage
from dashboard.pages.audio import AudioPage
from dashboard.pages.settings import SettingsPage

from audio.camilla_client import CamillaClient
from audio.amp_client import amp_status
from audio.routing import route_ok
from dashboard.shell_client import in_shell, place_carplay, shell_request, shell_status, show_volume_osd, set_dim, show_livi_ui
from dashboard import livi_control
from dashboard.carplay_shrink import picture_shrunk
from dashboard.night_dim import NightDimmer

from sensors.sensor_manager import SensorManager
from sensors.sensor_worker import SensorWorker
from sensors.ups import read_ups_status


def run_quiet(args, timeout=2):
    """
    Run a short helper command; True if it exited 0.
    Never raises and never hangs the GUI for long.
    """

    try:
        return subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        ).returncode == 0

    except (OSError, subprocess.TimeoutExpired):
        return False


class JeepComputer(QMainWindow):

    CARPLAY_APP_ID = "dev.f-io.livi"
    # LIVI-lowlatency: LIVI extracted from its AppImage (starts faster), built
    # by tools/livi_lowlatency.py. Its audio buffers are stock again (dongle
    # 1000 ms): the 500 ms version dropped phone audio 10-20% of the time on
    # the road. Used when present; delete that folder to go back to the
    # AppImage. CARPLAY_MATCH finds its processes (pgrep -f).
    CARPLAY_LOWLATENCY = os.path.expanduser("~/LIVI/LIVI-lowlatency")
    if os.path.exists(f"{CARPLAY_LOWLATENCY}/AppRun"):
        CARPLAY_PATH = f"{CARPLAY_LOWLATENCY}/AppRun"
        # The binary itself: LIVI's root helper (resources/driver/...) lives in
        # the same folder and outlives LIVI, so matching the folder would
        # make a stopped LIVI look like it's still running
        CARPLAY_MATCH = f"{CARPLAY_LOWLATENCY}/livi( |$)"
    else:
        CARPLAY_PATH = CARPLAY_MATCH = os.path.expanduser("~/LIVI/LIVI.AppImage")

    # ---------------------------------------------------------
    # CamillaDSP
    # ---------------------------------------------------------

    # CamillaDSP's control websocket (camilladsp -p 1234).
    # The GUI on :5005 is a separate web app and can't be used here.
    CAMILLADSP_URL = "ws://127.0.0.1:1234"

    # Volume change per W/S key press
    VOLUME_STEP_DB = 0.5
    STARTUP_VOLUME_DB = -40.0
    MIN_VOLUME_DB = -80.0
    MAX_VOLUME_DB = 0.0

    # How often to check LIVI / CamillaDSP health
    WATCHDOG_MS = 3000

    # Restart LIVI if its window has been missing this long
    CARPLAY_HUNG_S = 45
    LIVI_DEBUG_PORT = 9223          # 127.0.0.1 only (Chromium's default)

    # Minimum time between LIVI start attempts
    CARPLAY_RETRY_S = 30

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Jeep Computer")

        self.setStyleSheet(f"""
            QMainWindow {{
                background: {COLORS["background"]};
            }}
        """)

        # -----------------------------------------------------
        # CarPlay state
        # -----------------------------------------------------

        self.carplay_process = None
        self.carplay_window = False
        self.carplay_last_seen = time.monotonic()
        self.carplay_last_start = 0.0
        self.carplay_detect_timer = None
        self.current_page = None

        # -----------------------------------------------------
        # CamillaDSP state
        # -----------------------------------------------------

        self.camilla = CamillaClient(self.CAMILLADSP_URL)
        self.volume = None
        self.startup_volume_applied = False

        # -----------------------------------------------------
        # Main layout
        # -----------------------------------------------------

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -----------------------------------------------------
        # Top bar
        # -----------------------------------------------------

        self.top_bar = TopBar()
        layout.addWidget(self.top_bar)

        # Engine alarms / UPS shutdown countdown, on every page
        self.alert_banner = AlertBanner()
        layout.addWidget(self.alert_banner)

        # -----------------------------------------------------
        # Pages
        # -----------------------------------------------------

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.setStyleSheet(f"""
            #pages {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {COLORS["background_top"]}, stop:1 {COLORS["background"]});
            }}
        """)

        self.page_index = {}
        self.page_objects = {}

        pages = [
            ("carplay", CarPlayPage()),
            ("home", HomePage()),
            ("weather", WeatherPage()),
            ("offroad", OffroadPage()),
            ("electrical", ElectricalPage()),
            ("diagnostics", DiagnosticsPage()),
            ("music", MusicPlayerPage()),
            ("audio", AudioPage()),
            ("settings", SettingsPage()),
        ]

        for name, page in pages:
            self.page_index[name] = self.pages.count()
            self.page_objects[name] = page
            self.pages.addWidget(page)

        self.page_fader = PageFader(self.pages)

        # Pages give up height (clipping at the bottom) rather than grow the
        # window past the screen: several want 830-930 px, and with the alert
        # banner showing only ~826 are left. The window grew to 1184 px, the
        # nav bar went off-screen and CarPlay stretched to 1034 px, zooming
        # and cropping the phone's picture.
        self.pages.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)

        layout.addWidget(
            self.pages,
            stretch=1
        )

        # -----------------------------------------------------
        # Navigation bar
        # -----------------------------------------------------

        self.nav_bar = NavBar()

        self.nav_bar.page_changed.connect(
            self.change_page
        )

        self.nav_bar.exit_requested.connect(
            self.confirm_exit
        )

        layout.addWidget(self.nav_bar)

        self.volume_overlay = VolumeOverlay(central)

        # -----------------------------------------------------
        # Sensors (read on a background thread)
        # -----------------------------------------------------

        self.sensor_worker = SensorWorker(SensorManager)

        self.sensor_worker.sensor_update.connect(
            self.update_sensors
        )

        # Weather analysis (once a minute, computed on the sensor thread)
        weather = self.page_objects["weather"]
        self.sensor_worker.weather_update.connect(weather.set_summary)
        self.sensor_worker.weather_update.connect(self.page_objects["offroad"].set_weather)
        weather.on_settings_changed = self.sensor_worker.refresh_weather

        self.sensor_worker.start()

        # Volume keys caught by the shell arrive here (see start_command_server)
        self.start_command_server()

        # Screen dims itself at night (light sensor); shell only
        self.night_dimmer = NightDimmer(set_dim) if in_shell() else None

        settings = self.page_objects["settings"]
        settings.attach_dimmer(self.night_dimmer)
        settings.on_livi_settings = self.open_livi_settings
        settings.on_restart_carplay = self.restart_carplay
        settings.on_restart_dashboard = self.restart_dashboard
        settings.on_exit = self.confirm_exit
        self.livi_settings_open = False

        # -----------------------------------------------------
        # CamillaDSP + CarPlay
        # -----------------------------------------------------

        self.connect_camilladsp()

        self.start_carplay()

        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.timeout.connect(self.watchdog)
        self.watchdog_timer.start(self.WATCHDOG_MS)

        # In the shell, CarPlay follows the page area if the layout moves
        # (e.g. the alert banner appearing)
        self.carplay_place_timer = QTimer(self)
        self.carplay_place_timer.setSingleShot(True)
        self.carplay_place_timer.setInterval(150)
        self.carplay_place_timer.timeout.connect(self.place_carplay_now)
        self.pages.installEventFilter(self)

        self.alert_timer = QTimer(self)
        self.alert_timer.timeout.connect(self.check_alerts)
        self.alert_timer.start(1000)

        self.change_page("carplay")

    # =========================================================
    # SENSOR HANDLING
    # =========================================================

    def update_sensors(self, data):
        """
        Pass sensor data to all pages that support it.
        """

        for page in self.page_objects.values():

            if hasattr(page, "update_sensors"):
                page.update_sensors(data)

        if self.night_dimmer is not None:
            light = data.get("light") or {}
            lux = (light.get("data") or {}).get("lux") if light.get("status") == "OK" else None
            self.night_dimmer.update(lux)

        outside = (data.get("outside") or {}).get("data") or {}
        battery = (data.get("battery") or {}).get("data") or {}

        self.top_bar.set_temperature(outside.get("temperature_f"))
        self.top_bar.set_voltage(battery.get("voltage"))

    def eventFilter(self, obj, event):
        if (
            obj is self.pages
            and event.type() in (QEvent.Resize, QEvent.Move)
            and in_shell()
            and self.current_page == "carplay"
        ):
            QTimer.singleShot(0, self.show_carplay)

        return super().eventFilter(obj, event)

    # =========================================================
    # ALERTS (UPS + engine)
    # =========================================================

    def check_alerts(self):
        ups = read_ups_status()
        self.page_objects["electrical"].set_ups(ups)

        level, title, detail = None, "", ""
        engine = self.page_objects["home"].alert

        if ups and ups.get("shutting_down"):
            level, title, detail = "critical", "Shutting down", "Vehicle power is off"

        elif ups and ups.get("shutdown_in") is not None:
            seconds = f"{ups['shutdown_in']:.0f} s"
            if ups.get("grace_action") == "station":
                level = "warning"
                title = f"Vehicle power off · weather-station mode in {seconds}"
            elif ups.get("grace_action") == "sleep":
                level = "warning"
                title = f"Vehicle power off · battery low, sleep cycles in {seconds}"
            else:
                level = "critical"
                title = f"Vehicle power off · shutting down in {seconds}"
            detail = "Start the Jeep to cancel"

        elif engine:
            level, title = engine
            detail = "See Home"

        elif self.page_objects["weather"].alert:
            level, title = self.page_objects["weather"].alert
            detail = "See Weather"

        elif ups and not ups.get("vehicle_power"):
            level, title, detail = "warning", "Running on UPS battery", ""

        # The banner shows on every page and over CarPlay; it never changes the page
        self.alert_banner.show_alert(level, title, detail)
        self.update_carplay_banner()

    def update_carplay_banner(self):
        """Mirror the alert banner over CarPlay (the shell draws it there)."""

        if not in_shell():
            return
        banner = self.alert_banner
        state = (banner.level or "", banner.title, banner.detail) if banner.isVisibleTo(self) else ("", "", "")
        if state != getattr(self, "carplay_banner_state", None):
            if shell_request({"cmd": "banner", "level": state[0], "text": state[1],
                              "detail": state[2]}) is not None:
                self.carplay_banner_state = state

    # =========================================================
    # CAMILLADSP
    # =========================================================

    def connect_camilladsp(self):
        """
        Connect to CamillaDSP. The startup volume is applied only
        on the first connection, so a CamillaDSP restart later on
        doesn't suddenly change the listening level.
        """

        if not self.camilla.connect():
            self.volume = None
            return False

        try:
            current = self.camilla.get_volume()

            if not self.startup_volume_applied:
                current = self.camilla.set_volume(self.STARTUP_VOLUME_DB)
                self.startup_volume_applied = True

            self.volume = current

            print(f"Connected to CamillaDSP, volume {self.volume:.1f} dB")
            return True

        except RuntimeError as e:
            print(e)
            self.volume = None
            return False

    def change_volume(self, amount):
        """
        Adjust CamillaDSP Main volume, clamped to
        MIN_VOLUME_DB .. MAX_VOLUME_DB (0 dB maximum).
        """

        if not self.camilla.connected and not self.connect_camilladsp():
            self.show_volume(None)
            return

        if self.volume is None:
            self.volume = self.STARTUP_VOLUME_DB

        target = max(
            self.MIN_VOLUME_DB,
            min(self.MAX_VOLUME_DB, self.volume + amount)
        )

        try:
            self.volume = self.camilla.set_volume(target)

        except RuntimeError as e:
            print(e)
            self.volume = None
            self.show_volume(None)
            return

        at_max = self.volume >= self.MAX_VOLUME_DB
        self.show_volume(
            self.volume,
            f"{self.volume:.1f} dB · MAX" if at_max else ""
        )

    def show_volume(self, volume_db, message=""):
        """Volume pop-up: drawn by the shell when there is one, so it also
        shows over CarPlay; otherwise the dashboard's own overlay."""

        if in_shell() and show_volume_osd(volume_db, message) is not None:
            return

        self.volume_overlay.show_volume(volume_db, message)

    # =========================================================
    # KEYBOARD CONTROLS
    # =========================================================

    # W / S and the keyboard's media volume keys
    VOLUME_KEYS = {Qt.Key_W: 1, Qt.Key_VolumeUp: 1, Qt.Key_S: -1, Qt.Key_VolumeDown: -1}

    def keyPressEvent(self, event):

        # Classic layout only: in the shell, the shell catches these keys
        # itself (whatever has focus) and sends them to the command server
        step = self.VOLUME_KEYS.get(event.key())
        if step is not None:
            self.change_volume(step * self.VOLUME_STEP_DB)
            return

        super().keyPressEvent(event)

    def start_command_server(self):
        """
        $XDG_RUNTIME_DIR/jeep-dashboard.sock, one JSON line per request:
            {"cmd": "volume", "step": 1}     (or -1)
            {"cmd": "mute"}                  (toggle)
        Sent by the shell (W / S) and tools/volume_key.sh (media keys).
        """

        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                            "jeep-dashboard.sock")
        QLocalServer.removeServer(path)
        self.command_server = QLocalServer(self)
        self.command_server.newConnection.connect(self.accept_command)
        if not self.command_server.listen(path):
            print("Command socket failed:", self.command_server.errorString())

    def accept_command(self):
        while self.command_server.hasPendingConnections():
            sock = self.command_server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self.read_command(s))
            sock.disconnected.connect(sock.deleteLater)

    def read_command(self, sock):
        while sock.canReadLine():
            try:
                request = json.loads(bytes(sock.readLine()).decode())
            except ValueError:
                continue
            if request.get("cmd") == "volume" and request.get("step") in (1, -1):
                self.change_volume(request["step"] * self.VOLUME_STEP_DB)
            elif request.get("cmd") == "mute":
                self.toggle_mute()

    def toggle_mute(self):
        """Mute key: flip CamillaDSP's Main mute and show it in the pop-up."""

        if not self.camilla.connected and not self.connect_camilladsp():
            self.show_volume(None)
            return

        try:
            muted = not self.camilla.get_mute()
            self.camilla.call("SetMute", muted)
            self.volume = self.camilla.get_volume()
        except RuntimeError as e:
            print(e)
            self.show_volume(None)
            return

        self.show_volume(self.volume, "MUTED" if muted else "")

    # =========================================================
    # WATCHDOG: keep LIVI and CamillaDSP connected
    # =========================================================

    def watchdog(self):

        # --- CamillaDSP ---------------------------------------
        state = None

        if not self.camilla.connected:
            self.connect_camilladsp()

        if self.camilla.connected:
            try:
                state = self.camilla.get_state()
                self.volume = self.camilla.get_volume()
            except RuntimeError:
                state = None

        # --- LIVI -----------------------------------------------
        livi_running = self.carplay_process_running()

        if livi_running:
            # Confirm the window still exists (LIVI's inner app can die
            # while the AppImage wrapper keeps running)
            window = self.carplay_exists()

            if window and not self.carplay_window:
                # Newly appeared: show or minimize it for the current page
                self.check_carplay_window()

            self.carplay_window = window

            if window:
                self.carplay_last_seen = time.monotonic()

            elif time.monotonic() - self.carplay_last_seen > self.CARPLAY_HUNG_S:
                print("LIVI has no window, restarting it")
                self.restart_carplay()

        elif time.monotonic() - self.carplay_last_start > self.CARPLAY_RETRY_S:
            print("LIVI is not running, starting it")
            self.carplay_window = False
            self.start_carplay()

        else:
            self.carplay_window = False

        livi_running = livi_running or self.carplay_process is not None

        carplay_page = self.page_objects["carplay"]

        settings = self.page_objects["settings"]
        if getattr(self, "carplay_streaming", False):
            settings.set_carplay_state("Connected", COLORS["good"])
        elif self.carplay_window:
            settings.set_carplay_state("Waiting for iPhone")
        elif livi_running:
            settings.set_carplay_state("Starting…")
        else:
            settings.set_carplay_state("CarPlay not running", COLORS["warning"])

        if getattr(self, "carplay_streaming", False):
            carplay_page.set_state("CarPlay connected", "", busy=False)
        elif self.carplay_window:
            carplay_page.set_state("Starting CarPlay…", "Connecting to your iPhone")
        elif livi_running:
            carplay_page.set_state("Starting CarPlay…", "Opening CarPlay")
        else:
            carplay_page.set_state("CarPlay stopped", "Restarting it in a moment", busy=False)

        # --- Audio route -----------------------------------------
        self.page_objects["diagnostics"].set_audio_status({
            "camilla_state": state,
            "volume_db": self.volume,
            "carplay_running": livi_running,
            "carplay_window": self.carplay_window,
            "route_ok": route_ok(),
            "amps": amp_status(),
        })

    # =========================================================
    # CARPLAY
    # =========================================================

    def carplay_process_running(self):
        return run_quiet(["pgrep", "-f", self.CARPLAY_MATCH])

    def carplay_exists(self):
        if in_shell():
            status = shell_status()
            streaming = bool(status and status.get("carplay_streaming"))
            if streaming and not getattr(self, "carplay_streaming", False):
                QTimer.singleShot(8000, self.capture_carplay_evidence)
            self.carplay_streaming = streaming
            return bool(status and status.get("carplay_connected"))

        return run_quiet([
            "wlrctl",
            "toplevel",
            "find",
            f"app_id:{self.CARPLAY_APP_ID}"
        ])

    CARPLAY_SHOTS = Path.home() / ".cache/jeep_computer/carplay-shots"
    CARPLAY_SHOTS_KEEP = 8

    def capture_carplay_evidence(self):
        """
        CarPlay sometimes comes up the wrong size on a drive, and nothing in
        LIVI's or the shell's logs shows why (the slot is always 1920x930).
        A few seconds into each phone stream: log the video window's sizes,
        and keep a small screenshot of the CarPlay area (the last few, in
        CARPLAY_SHOTS), so the next time it happens there's a picture of it.
        """
        status = shell_status() or {}
        print(f"CarPlay stream: slot {status.get('carplay_slot')} buffer {status.get('carplay_buffer')} "
              f"dest {status.get('carplay_dest')}", flush=True)
        if self.current_page != "carplay" or not self.isVisible():
            self.carplay_shot_pending = True        # take it when the tab opens
            return
        self.carplay_shot_pending = False
        try:
            self.CARPLAY_SHOTS.mkdir(parents=True, exist_ok=True)
            for old in sorted(self.CARPLAY_SHOTS.glob("*.png"))[:-(self.CARPLAY_SHOTS_KEEP - 1)]:
                old.unlink()
            x, y, w, h = self.carplay_rect()
            path = self.CARPLAY_SHOTS / time.strftime("%Y-%m-%d_%H%M%S.png")
            # The shell's own output is labwc's wayland-0
            subprocess.Popen(["grim", "-g", f"{x},{y} {w}x{h}", "-s", "0.4", str(path)],
                             env=dict(os.environ, WAYLAND_DISPLAY="wayland-0"),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            QTimer.singleShot(2000, lambda: self.check_carplay_size(path))
        except OSError as e:
            print("CarPlay screenshot failed:", e)

    # Dongle resets for a wrong-size picture: at most this many per window,
    # so a misjudged screenshot can't keep dropping the phone
    CARPLAY_RESETS_MAX = 2
    CARPLAY_RESET_WINDOW_S = 600
    CARPLAY_RECHECK_S = 45

    def check_carplay_size(self, path):
        """
        The picture came up at the dongle's fallback 1280x720 in the corner
        (dashboard/carplay_shrink.py): USB-reset the dongle through LIVI so
        it takes LIVI's 1920x928 again and the phone reconnects at that size.
        """
        if picture_shrunk(path) is not True:
            return
        now = time.monotonic()
        self.carplay_resets = [t for t in getattr(self, "carplay_resets", [])
                               if now - t < self.CARPLAY_RESET_WINDOW_S]
        if len(self.carplay_resets) >= self.CARPLAY_RESETS_MAX:
            print(f"CarPlay at the fallback size ({path.name}), but the dongle was already "
                  f"reset {len(self.carplay_resets)} times recently; leaving it", flush=True)
            return
        self.carplay_resets.append(now)
        print(f"CarPlay at the fallback size ({path.name}): resetting the dongle", flush=True)
        if not livi_control.reset_dongle():
            print("Dongle reset failed: LIVI not reachable", flush=True)
            return
        # The phone reconnecting starts a new stream, which carplay_exists
        # checks again; this catches one that never looked disconnected
        QTimer.singleShot(self.CARPLAY_RECHECK_S * 1000, self.capture_carplay_evidence)

    @staticmethod
    def carplay_environment():
        """
        Clean environment for LIVI, so nothing from the dashboard's
        Python venv or a VS Code terminal leaks into it:
          - ELECTRON_RUN_AS_NODE makes LIVI quit immediately
          - the venv's python3 lacks dbus/gi/smbus2, which LIVI's
            Bluetooth / Wi-Fi / MFi helper needs
        """

        env = dict(os.environ)

        for key in ("ELECTRON_RUN_AS_NODE", "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)

        venv_bin = os.path.join(sys.prefix, "bin")

        env["PATH"] = os.pathsep.join(
            part for part in env.get("PATH", "").split(os.pathsep)
            if part and part != venv_bin
        )

        env.setdefault("XDG_CURRENT_DESKTOP", "labwc:wlroots")

        return env

    LIVI_CONFIG = Path.home() / ".config/LIVI/config.json"

    def ensure_livi_windowed(self):
        """
        LIVI turns on (and saves) kiosk mode when its window fills its
        whole screen. In the classic layout that would cover the nav bar
        and lock the screen in CarPlay, so always start it windowed.

        Also keeps LIVI's own wireless CarPlay / Android Auto off: CarPlay
        goes through the Carlinkit dongle. LIVI's version needs an MFi chip
        the Pi doesn't have, and it turns wlan0 into an access point (the
        Pi drops off the house Wi-Fi) and offers CarPlay over the Pi's
        Bluetooth, so the phone keeps trying the Pi and never the dongle.
        Only called while LIVI isn't running (it rewrites its config).
        """

        try:
            config = json.loads(self.LIVI_CONFIG.read_text())
        except (OSError, ValueError):
            return

        changed = False

        kiosk = config.get("kiosk") or {}

        if kiosk.get("main"):
            kiosk["main"] = False
            config["kiosk"] = kiosk
            changed = True
            print("LIVI kiosk mode was on; turned it off")

        for key in ("wirelessCpEnabled", "wirelessAaEnabled"):
            if config.get(key):
                config[key] = False
                changed = True
                print(f"LIVI {key} was on; turned it off (CarPlay uses the dongle)")

        if changed:
            self.LIVI_CONFIG.write_text(json.dumps(config, indent=2))

    def restart_carplay(self):

        # CARPLAY_MATCH, not the launch path: the extracted copy's processes
        # are .../LIVI-lowlatency/livi, not the AppRun that started them
        run_quiet(["pkill", "-TERM", "-f", self.CARPLAY_MATCH])

        # Give LIVI a moment to clean up, then start it fresh.
        # Updating last_start keeps the watchdog from starting it too.
        self.carplay_window = False
        self.carplay_last_seen = time.monotonic()
        self.carplay_last_start = time.monotonic()
        QTimer.singleShot(3000, self.start_carplay)

    def open_livi_settings(self):
        """LIVI's own settings in the CarPlay tab, even while a phone streams
        (LIVI hides them then; its router is driven over DevTools)."""
        if not in_shell():
            self.change_page("carplay")
            return
        self.livi_settings_open = True
        show_livi_ui(True)
        livi_control.open_settings()
        self.change_page("carplay")

    def close_livi_settings(self):
        self.livi_settings_open = False
        livi_control.close_settings()
        show_livi_ui(False)

    def restart_dashboard(self):
        """Quit with a non-zero code: the shell (or run_dashboard.sh) starts
        the dashboard again right away; CarPlay keeps running."""
        QApplication.instance().exit(75)

    def restart_carplay_if_windowless(self):
        if self.carplay_process_running() and not self.carplay_exists():
            print("LIVI is running without a window in this shell, restarting it")
            self.restart_carplay()

    def start_carplay(self):

        # Grace period for LIVI to open its window
        self.carplay_last_seen = time.monotonic()
        self.carplay_last_start = time.monotonic()

        if self.carplay_process_running():

            print("CarPlay already running")

            # After a dashboard/shell restart LIVI can survive with its window
            # gone (it was drawing into the old shell). Check soon rather
            # than waiting CARPLAY_HUNG_S for the watchdog.
            if in_shell():
                QTimer.singleShot(8000, self.restart_carplay_if_windowless)

        else:

            print("Starting CarPlay...")

            self.ensure_livi_windowed()
            self.reset_dongle_before_launch()

            env = self.carplay_environment()
            command = [self.CARPLAY_PATH]

            if in_shell():
                # Run LIVI's app straight inside the shell instead of in its
                # own nested compositor (which needs a newer wl_seat than Qt
                # offers). LIVI_COMPOSITOR=1 is how LIVI's launcher tells the
                # app it is already inside a compositor.
                env["LIVI_COMPOSITOR"] = "1"
                command.append("--ozone-platform=wayland")

            # Local-only DevTools port: lets the dashboard open LIVI's own
            # settings (dashboard/livi_control.py) even while CarPlay streams
            command.append(f"--remote-debugging-port={self.LIVI_DEBUG_PORT}")

            try:
                # Own session so LIVI keeps running if the dashboard restarts
                self.carplay_process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                )

            except OSError as e:
                print("Unable to start CarPlay:", e)
                self.carplay_process = None
                return

        self.start_carplay_detect_timer()

    CARPLAY_DONGLE_ID = "1314:1520"     # Carlinkit "Auto Box"

    def reset_dongle_before_launch(self):
        """
        USB-reset the dongle just before LIVI starts. A dongle that is already
        up when LIVI starts (always, now the Pi boots with the Jeep) streams at
        its own default size first, and LIVI then force-resets it to apply
        1920x928: a wrong-size CarPlay and a reconnect ~15 s in. Reset here, it
        re-enumerates ~3 s later, after LIVI's startup scan, so LIVI sees a
        fresh plug-in and configures it before the phone connects (the launches
        where the dongle arrived after LIVI never needed the reset, 2026-10-05/06).
        """
        try:
            found = subprocess.run(["lsusb", "-d", self.CARPLAY_DONGLE_ID],
                                   capture_output=True, timeout=5).returncode == 0
            if not found:
                return
            result = subprocess.run(["usbreset", self.CARPLAY_DONGLE_ID],
                                    capture_output=True, text=True, timeout=10)
            out = (result.stdout + result.stderr).strip()
            # The dongle drops off the bus mid-reset, so usbreset ends with
            # "failed [No such device]" when it worked (LIVI logs its own reset
            # the same way, as a success)
            ok = result.returncode == 0 or "No such device" in out
            print(f"Dongle reset before LIVI starts: {'done' if ok else out}", flush=True)
        except (OSError, subprocess.SubprocessError) as e:
            print("Dongle reset before LIVI failed:", e, flush=True)

    def start_carplay_detect_timer(self):
        """
        Poll quickly for the LIVI window after (re)starting it.
        """

        if self.carplay_detect_timer is None:
            self.carplay_detect_timer = QTimer(self)
            self.carplay_detect_timer.timeout.connect(self.check_carplay_window)

        self.carplay_detect_timer.start(250)

    def check_carplay_window(self):

        if not self.carplay_exists():
            return

        print("CarPlay window detected")

        self.carplay_window = True

        if self.carplay_detect_timer is not None:
            self.carplay_detect_timer.stop()

        # Put LIVI where it belongs for the current page
        if self.current_page == "carplay":
            self.show_carplay()
        else:
            self.hide_carplay()

    def carplay_rect(self):
        """
        Where CarPlay goes on screen: everything between the top bar and the
        nav bar. Deliberately *not* the page area, which shrinks while the
        alert banner shows: resizing LIVI while a phone is streaming leaves
        its picture zoomed and cropped. Alerts show over CarPlay instead
        (drawn by the shell, see update_carplay_banner).
        """

        central = self.centralWidget()
        origin = central.mapTo(self, central.rect().topLeft())
        top = origin.y() + self.top_bar.geometry().bottom() + 1
        bottom = origin.y() + self.nav_bar.geometry().top()
        rect = (origin.x(), top, central.width(), bottom - top)
        if rect != getattr(self, "logged_carplay_rect", None):
            print("CarPlay area:", rect, flush=True)
            self.logged_carplay_rect = rect
        return rect

    def place_carplay_now(self):
        # Before the window is laid out the page area has a bogus size;
        # the resize/show events will place CarPlay once it's real.
        if not self.isVisible() or self.current_page != "carplay":
            return

        x, y, w, h = self.carplay_rect()
        place_carplay(x, y, w, h, visible=True)

    def show_carplay(self):

        if in_shell():
            # Wait for the layout to settle so LIVI only gets the final size
            # (a burst of resizes can leave its picture stuck at an old size)
            self.carplay_place_timer.start()
            return

        if not self.carplay_window:
            return

        if not run_quiet([
            "wlrctl",
            "toplevel",
            "focus",
            f"app_id:{self.CARPLAY_APP_ID}"
        ]):
            # Window went away; the watchdog will find it again
            self.carplay_window = False

    def hide_carplay(self):

        if in_shell():
            x, y, w, h = self.carplay_rect()
            place_carplay(x, y, w, h, visible=False)
            return

        if not self.carplay_window:
            return

        if not run_quiet([
            "wlrctl",
            "toplevel",
            "minimize",
            f"app_id:{self.CARPLAY_APP_ID}"
        ]):
            self.carplay_window = False

    # =========================================================
    # PAGE NAVIGATION
    # =========================================================

    def change_page(self, page):

        if page not in self.page_index:
            return

        self.current_page = page

        # Leaving the CarPlay tab closes LIVI's settings again
        if page != "carplay" and self.livi_settings_open:
            self.close_livi_settings()

        self.page_fader.switch(self.page_index[page])

        self.nav_bar.set_active(
            page
        )

        if page == "carplay":

            self.show_carplay()

            if getattr(self, "carplay_shot_pending", False):
                QTimer.singleShot(3000, self.capture_carplay_evidence)

        else:

            self.hide_carplay()

            self.raise_()
            self.activateWindow()

    # =========================================================
    # CARPLAY SHUTDOWN
    # =========================================================

    def stop_carplay(self):

        # LIVI is intentionally left running when the dashboard exits.
        return

    # =========================================================
    # APPLICATION SHUTDOWN
    # =========================================================

    def closeEvent(self, event):

        print(
            "Shutting down Jeep Computer..."
        )

        self.watchdog_timer.stop()
        self.alert_timer.stop()

        if self.carplay_detect_timer is not None:
            self.carplay_detect_timer.stop()

        print("Stopping sensor worker...")
        self.sensor_worker.stop()

        self.page_objects["music"].player.stop()

        self.stop_carplay()

        self.camilla.close()
        print("Disconnected from CamillaDSP")

        print(
            "Jeep Computer shutdown complete."
        )

        event.accept()

    # =========================================================
    # EXIT CONFIRMATION
    # =========================================================

    def confirm_exit(self):

        dialog = QMessageBox(self)

        dialog.setWindowTitle(
            "EXIT JEEP COMPUTER"
        )

        dialog.setText(
            "Are you sure you want to exit?"
        )

        dialog.setInformativeText(
            "The Jeep Computer dashboard will shut down."
        )

        dialog.setStandardButtons(
            QMessageBox.Yes |
            QMessageBox.No
        )

        dialog.setDefaultButton(
            QMessageBox.No
        )

        dialog.setStyleSheet(
            f"""
            QMessageBox {{
                background: {COLORS["background"]};
            }}

            QLabel {{
                color: {COLORS["text"]};
                font-size: 24px;
                padding: 20px;
            }}

            QPushButton {{
                min-width: 160px;
                min-height: 70px;
                font-size: 22px;
                font-weight: bold;
                background: {COLORS["card"]};
                color: {COLORS["text"]};
                border: 2px solid {COLORS["border"]};
                border-radius: 10px;
                padding: 10px;
            }}

            QPushButton:hover {{
                background: {COLORS["accent"]};
            }}
            """
        )

        result = dialog.exec()

        if result == QMessageBox.Yes:

            self.close()


# =============================================================
# APPLICATION ENTRY POINT
# =============================================================

if __name__ == "__main__":

    app = QApplication(sys.argv)
    app.setDesktopFileName("jeep-dashboard")

    window = JeepComputer()

    window.showFullScreen()

    QTimer.singleShot(
        1000,
        window.show_carplay
    )

    sys.exit(
        app.exec()
    )
