import json
from pathlib import Path

from PySide6.QtCore import (
    QMetaObject,
    QObject,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)

from weather.analysis import analyse
from weather.logger import WeatherLogger
from weather.store import WeatherStore
from sensors.live import write_live
from engine.logger import EngineLogger


WEATHER_SETTINGS = Path.home() / ".config/jeep_computer/weather.json"


def weather_settings():
    """{"elevation_ft": None | float, "northern": bool}"""
    try:
        settings = json.loads(WEATHER_SETTINGS.read_text())
    except (OSError, ValueError):
        settings = {}
    settings.setdefault("elevation_ft", None)
    settings.setdefault("northern", True)
    return settings


class _Poller(QObject):
    """
    Lives on the sensor thread. All I2C work (including the slow
    sensor start-up) happens here so the dashboard never freezes.
    """

    sensor_update = Signal(dict)
    weather_update = Signal(object)

    READ_INTERVAL_MS = 1000
    RETRY_INTERVAL_MS = 60000
    WEATHER_INTERVAL_MS = 60000

    def __init__(self, manager_factory):
        super().__init__()

        self.manager_factory = manager_factory
        self.manager = None
        self.read_timer = None
        self.retry_timer = None
        self.weather_timer = None
        self.weather_logger = WeatherLogger()
        self.weather_store = None
        self.engine_logger = EngineLogger()

    @Slot()
    def start(self):
        try:
            self.manager = self.manager_factory()
        except Exception as e:
            print("Sensor manager failed to start:", e)
            return

        self.read_timer = QTimer(self)
        self.read_timer.timeout.connect(self.read)
        self.read_timer.start(self.READ_INTERVAL_MS)

        # Sensors that were not ready at boot get another chance
        self.retry_timer = QTimer(self)
        self.retry_timer.timeout.connect(self.manager.retry_failed)
        self.retry_timer.start(self.RETRY_INTERVAL_MS)

        # Weather analysis once a minute (right away too, from history)
        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.analyse_weather)
        self.weather_timer.start(self.WEATHER_INTERVAL_MS)

        self.read()
        self.analyse_weather()

    @Slot()
    def read(self):
        try:
            data = self.manager.get_data()
        except Exception as e:
            print("Sensor read error:", e)
            return

        # Engine log, fuel use and gauge self-calibration (engine/logger.py);
        # its summary rides along as data["engine"]
        try:
            data["engine"] = {"status": "OK", "error": None, "data": self.engine_logger.add(data)}
        except Exception as e:
            data["engine"] = {"status": "ERROR", "error": str(e), "data": None}
            print("Engine logger error:", e)

        self.sensor_update.emit(data)
        write_live(data, "dashboard")

        try:
            self.weather_logger.add(data)
        except Exception as e:
            print("Weather logger error:", e)

    @Slot()
    def analyse_weather(self):
        try:
            if self.weather_store is None:
                self.weather_store = WeatherStore()

            settings = weather_settings()
            elevation_ft = settings["elevation_ft"]

            summary = analyse(
                self.weather_store.since(72 * 3600),
                elevation_m=None if elevation_ft is None else elevation_ft * 0.3048,
                northern=settings["northern"],
            )
        except Exception as e:
            print("Weather analysis error:", e)
            return

        self.weather_update.emit(summary)

    @Slot()
    def stop(self):
        for timer in (self.read_timer, self.retry_timer, self.weather_timer):
            if timer is not None:
                timer.stop()


class SensorWorker(QObject):
    """
    Runs the SensorManager on a background thread and emits
    sensor_update(dict) on the GUI thread once per second, and
    weather_update(WeatherSummary) once a minute.
    """

    sensor_update = Signal(dict)
    weather_update = Signal(object)

    def __init__(self, manager_factory):
        super().__init__()

        self.thread = QThread()
        self.thread.setObjectName("sensor-thread")

        self.poller = _Poller(manager_factory)
        self.poller.moveToThread(self.thread)

        self.thread.started.connect(self.poller.start)
        self.poller.sensor_update.connect(self.sensor_update)
        self.poller.weather_update.connect(self.weather_update)

    def start(self):
        self.thread.start()

    def refresh_weather(self):
        """Re-run the weather analysis now (e.g. after a settings change)."""
        QMetaObject.invokeMethod(self.poller, "analyse_weather", Qt.QueuedConnection)

    def stop(self):
        if not self.thread.isRunning():
            return

        # Stop the timers on their own thread, then shut it down
        QMetaObject.invokeMethod(
            self.poller,
            "stop",
            Qt.BlockingQueuedConnection
        )

        self.thread.quit()
        self.thread.wait(3000)
