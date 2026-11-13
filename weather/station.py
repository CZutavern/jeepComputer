"""
Headless weather-station logger for when the Jeep is switched off.

The UPS monitor starts this (jeep-weather-station.service) after it has
stopped the dashboard, CarPlay and CamillaDSP, so the Pi can keep
recording weather on the UPS batteries at low power. It reads only the
weather sensors and marks every minute as parked (not moving).

Run:  .venv/bin/python -u -m weather.station
"""

import signal
import sys
import time

from sensors.sensor_manager import SensorManager
from weather.logger import WeatherLogger
from sensors.live import write_live


WEATHER_SENSORS = ("outside", "pressure", "light")
READ_INTERVAL_S = 1.0
RETRY_INTERVAL_S = 60.0


def main():
    running = True

    def stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    manager = SensorManager(only=WEATHER_SENSORS)
    logger = WeatherLogger(moving=False)   # parked while the Jeep is off

    print("Weather station logging (vehicle off)", flush=True)

    next_read = time.monotonic()
    next_retry = next_read + RETRY_INTERVAL_S

    while running:
        now = time.monotonic()

        if now >= next_retry:
            manager.retry_failed()
            next_retry = now + RETRY_INTERVAL_S

        try:
            data = manager.get_data()
            logger.add(data)
            write_live(data, "station")
        except Exception as e:
            print("Weather station read error:", e, flush=True)

        next_read += READ_INTERVAL_S
        time.sleep(max(0.0, next_read - time.monotonic()))

    logger.flush_now()
    print("Weather station stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
