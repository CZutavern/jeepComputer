"""
Turns the once-a-second sensor readings into one averaged row per minute
in the weather store. Runs on the sensor thread.
"""

import statistics
import time

from weather.analysis import Sample
from weather.store import WeatherStore


def _reading(data, sensor, key):
    return ((data.get(sensor) or {}).get("data") or {}).get(key)


class WeatherLogger:

    # IMU movement within one minute that means the Jeep was driving
    MOVING_ANGLE_DEG = 3.0
    MOVING_HEADING_DEG = 5.0

    def __init__(self, moving=None):
        """moving: force the motion flag (False when parked, e.g. the
        weather-station mode); None = work it out from the IMU."""
        self.store = None
        self.minute = None
        self.buffer = []
        self.moving_override = moving

    def add(self, data):
        now = time.time()
        minute = int(now // 60)

        if self.minute is not None and minute != self.minute:
            self.flush(self.minute)

        self.minute = minute
        self.buffer.append({
            "pressure": _reading(data, "pressure", "pressure"),
            "temp_c": _reading(data, "outside", "temperature_c"),
            "humidity": _reading(data, "outside", "humidity"),
            "lux": _reading(data, "light", "lux"),
            "heading": _reading(data, "imu", "heading"),
            "pitch": _reading(data, "imu", "pitch"),
            "roll": _reading(data, "imu", "roll"),
        })

    def flush_now(self):
        """Write out the minute in progress (e.g. when stopping)."""
        if self.minute is not None and self.buffer:
            self.flush(self.minute)

    def flush(self, minute):
        rows, self.buffer = self.buffer, []

        def values(key):
            return [r[key] for r in rows if r[key] is not None]

        pressures = values("pressure")
        if not pressures:
            return   # nothing useful without the barometer

        def mean(key):
            v = values(key)
            return statistics.fmean(v) if v else None

        sample = Sample(
            ts=minute * 60 + 30,
            pressure=statistics.median(pressures),   # robust to spikes
            temp_c=mean("temp_c"),
            humidity=mean("humidity"),
            lux=mean("lux"),
            moving=self.moving_override if self.moving_override is not None else self.moving(rows),
        )

        try:
            if self.store is None:
                self.store = WeatherStore()
                self.store.prune()
            self.store.add(sample)
        except Exception as e:
            print("Weather log error:", e)

    def moving(self, rows):
        """IMU-based: did the Jeep move this minute? None without an IMU."""
        pitch = [r["pitch"] for r in rows if r["pitch"] is not None]
        roll = [r["roll"] for r in rows if r["roll"] is not None]
        heading = [r["heading"] for r in rows if r["heading"] is not None]

        if len(pitch) < 10:
            return None

        # Heading spread, allowing for the 359 -> 0 wrap
        spread = 0.0
        if heading:
            ref = heading[0]
            diffs = [((h - ref + 180) % 360) - 180 for h in heading]
            spread = max(diffs) - min(diffs)

        return (
            max(pitch) - min(pitch) > self.MOVING_ANGLE_DEG
            or max(roll) - min(roll) > self.MOVING_ANGLE_DEG
            or spread > self.MOVING_HEADING_DEG
        )
