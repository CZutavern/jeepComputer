"""
Jeep Computer shell.

A full-screen Wayland compositor (shell.qml) that the dashboard and LIVI
run inside, so CarPlay can be placed anywhere in the dashboard's layout.

  - launches the dashboard inside the shell, restarts it after a crash
    (CarPlay keeps running meanwhile) and quits when the dashboard exits
    normally (EXIT button)
  - control socket $XDG_RUNTIME_DIR/jeep-shell.sock, one JSON request per
    connection:
        {"cmd": "carplay", "rect": [x, y, w, h], "visible": true}
        {"cmd": "osd", "volume": -32.5, "message": ""}   (volume pop-up)
        {"cmd": "dim", "level": 0.4}                      (night dimming, 0 = off)
        {"cmd": "livi_ui", "show": true}                  (LIVI's own screen, e.g. settings)
        {"cmd": "banner", "level": "warning", "text": "...", "detail": "..."}
                                                          (alert over CarPlay, "" clears)
        {"cmd": "status"}

Exit codes: 0 = dashboard exited normally, 3 = the compositor couldn't
start (run_dashboard.sh then falls back to the classic layout).

Needs LD_LIBRARY_PATH=vendor/qtwayland/lib (see vendor/qtwayland/README.md).
"""

import json
import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQml import QQmlApplicationEngine


ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv/bin/python"

SOCKET_NAME = "jeep-shell-0"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
CONTROL_PATH = RUNTIME_DIR / "jeep-shell.sock"
DASHBOARD_PATH = RUNTIME_DIR / "jeep-dashboard.sock"

# Volume keys work on every page: the shell catches them before they reach
# whichever window has keyboard focus (dashboard, CarPlay or nothing) and
# hands them to the dashboard, which drives CamillaDSP.
VOLUME_KEYS = {Qt.Key_W: 1, Qt.Key_VolumeUp: 1, Qt.Key_S: -1, Qt.Key_VolumeDown: -1}

EXIT_INIT_FAILED = 3
RESTART_DELAY_MS = 2000


class Shell(QObject):

    def __init__(self, app):
        super().__init__()

        self.app = app
        self.dashboard = None
        self.quitting = False

        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(
            lambda warnings: [print("shell QML:", w.toString(), flush=True) for w in warnings]
        )
        self.engine.load(QUrl.fromLocalFile(str(ROOT / "shell/shell.qml")))

        roots = self.engine.rootObjects()
        if not roots:
            print("shell: compositor failed to start", flush=True)
            sys.exit(EXIT_INIT_FAILED)

        self.comp = roots[0]

        self.start_control_server()
        self.start_dashboard()

        app.installEventFilter(self)

        app.aboutToQuit.connect(self.stop_dashboard)

    # ---------------------------------------------------------
    # Volume keys
    # ---------------------------------------------------------

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.KeyPress, QEvent.KeyRelease):
            step = VOLUME_KEYS.get(event.key())
            no_modifiers = not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            if step is not None and no_modifiers:
                if kind == QEvent.KeyPress:
                    self.send_dashboard({"cmd": "volume", "step": step})
                return True     # swallowed: CarPlay/dashboard never see it
        return False

    def send_dashboard(self, request):
        sock = QLocalSocket(self)
        sock.connectToServer(str(DASHBOARD_PATH))
        if sock.waitForConnected(200):
            sock.write(json.dumps(request).encode() + b"\n")
            sock.waitForBytesWritten(200)
            sock.disconnectFromServer()
        sock.deleteLater()

    # ---------------------------------------------------------
    # Dashboard process
    # ---------------------------------------------------------

    def start_dashboard(self):
        if self.quitting:
            return

        env = QProcessEnvironment.systemEnvironment()
        env.insert("WAYLAND_DISPLAY", SOCKET_NAME)
        env.insert("QT_QPA_PLATFORM", "wayland")
        env.insert("JEEP_SHELL_CONTROL", str(CONTROL_PATH))
        env.remove("LD_LIBRARY_PATH")

        process = QProcess(self)
        process.setProgram(str(PYTHON))
        process.setArguments(["-u", str(ROOT / "main.py")])
        process.setWorkingDirectory(str(ROOT))
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.ForwardedChannels)

        process.started.connect(
            lambda: self.comp.setProperty("dashboardPid", int(process.processId()))
        )
        process.finished.connect(
            lambda code, status, p=process: self.dashboard_finished(p, code, status)
        )

        self.dashboard = process
        process.start()
        print("shell: dashboard starting", flush=True)

    def dashboard_finished(self, process, code, status):
        if process is not self.dashboard or self.quitting:
            return

        if status == QProcess.NormalExit and code == 0:
            print("shell: dashboard exited normally, closing shell", flush=True)
            self.app.quit()
            return

        print(f"shell: dashboard crashed (exit {code}), restarting", flush=True)
        QTimer.singleShot(RESTART_DELAY_MS, self.start_dashboard)

    def stop_dashboard(self):
        self.quitting = True
        process = self.dashboard

        if process and process.state() != QProcess.NotRunning:
            process.terminate()
            if not process.waitForFinished(3000):
                process.kill()
                process.waitForFinished(1000)

        if CONTROL_PATH.exists():
            CONTROL_PATH.unlink()

    # ---------------------------------------------------------
    # Control socket
    # ---------------------------------------------------------

    def start_control_server(self):
        QLocalServer.removeServer(str(CONTROL_PATH))

        self.server = QLocalServer(self)
        if not self.server.listen(str(CONTROL_PATH)):
            print("shell: control socket failed:", self.server.errorString(), flush=True)
            return

        self.server.newConnection.connect(self.accept)

    def accept(self):
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self.handle(c))
            conn.disconnected.connect(conn.deleteLater)

    def handle(self, conn):
        if not conn.canReadLine():
            return

        try:
            request = json.loads(bytes(conn.readLine()).decode())
            reply = self.execute(request)
        except (ValueError, TypeError) as e:
            reply = {"ok": False, "error": str(e)}

        conn.write((json.dumps(reply) + "\n").encode())
        conn.flush()
        conn.disconnectFromServer()

    def execute(self, request):
        cmd = request.get("cmd")

        if cmd == "carplay":
            rect = request.get("rect")
            if rect:
                x, y, w, h = (int(v) for v in rect)
                self.comp.setProperty("carplayX", x)
                self.comp.setProperty("carplayY", y)
                self.comp.setProperty("carplayW", max(1, w))
                self.comp.setProperty("carplayH", max(1, h))

            if "visible" in request:
                self.comp.setProperty("carplayVisible", bool(request["visible"]))

        elif cmd == "osd":
            volume = request.get("volume")
            self.comp.setProperty("osdOffline", volume is None)
            if volume is not None:
                self.comp.setProperty("osdVolume", float(volume))
            self.comp.setProperty("osdMessage", str(request.get("message") or ""))
            self.comp.setProperty("osdSerial", int(self.comp.property("osdSerial")) + 1)

        elif cmd == "banner":
            # Alert strip over CarPlay (level "" clears it)
            self.comp.setProperty("bannerLevel", str(request.get("level") or ""))
            self.comp.setProperty("bannerText", str(request.get("text") or ""))
            self.comp.setProperty("bannerDetail", str(request.get("detail") or ""))

        elif cmd == "livi_ui":
            self.comp.setProperty("liviUi", bool(request.get("show")))

        elif cmd == "dim":
            level = max(0.0, min(0.8, float(request.get("level", 0))))
            self.comp.setProperty("dimLevel", level)

        elif cmd != "status":
            return {"ok": False, "error": f"unknown command {cmd!r}"}

        return {
            "ok": True,
            "carplay_connected": bool(self.comp.property("carplayConnected")),
            "carplay_visible": bool(self.comp.property("carplayVisible")),
            "carplay_streaming": bool(self.comp.property("carplayStreaming")),
            "dashboard_connected": bool(self.comp.property("dashboardConnected")),
            "carplay_slot": [
                int(self.comp.property("carplayW")), int(self.comp.property("carplayH"))
            ],
            "carplay_buffer": [
                int(self.comp.property("carplayBufferSize").width()),
                int(self.comp.property("carplayBufferSize").height()),
            ],
            "carplay_dest": [
                int(self.comp.property("carplayDestSize").width()),
                int(self.comp.property("carplayDestSize").height()),
            ],
        }


def main():
    app = QGuiApplication(sys.argv)
    app.setDesktopFileName("jeep-shell")

    # Let Python see SIGTERM while Qt's event loop is running
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer()
    tick.start(500)
    tick.timeout.connect(lambda: None)

    shell = Shell(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
