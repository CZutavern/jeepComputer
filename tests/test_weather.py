"""
Synthetic-weather tests for weather/analysis.py.

Run:  .venv/bin/python -m tests.test_weather
"""

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weather.analysis import Sample, analyse, dew_point_c, heat_index_f, weather_pressure_series

random.seed(7)
NOW = 1_790_000_000  # fixed "now" (a September afternoon)


def noisy(p):
    """BMP388-like noise plus the odd spike."""
    v = p + random.gauss(0, 0.03)
    if random.random() < 0.005:
        v += random.choice([-1.5, 1.5])
    return v


def minutes(n, pressure, temp=20.0, humidity=55.0, moving=None, start=None):
    """n one-minute samples ending at NOW (or starting at `start`)."""
    start = NOW - (n - 1) * 60 if start is None else start
    out = []
    for i in range(n):
        t = start + i * 60
        p = pressure(i) if callable(pressure) else pressure
        tc = temp(i) if callable(temp) else temp
        h = humidity(i) if callable(humidity) else humidity
        mv = moving(i) if callable(moving) else moving
        out.append(Sample(t, noisy(p), tc, h, 5000, mv))
    return out


results = []


def check(name, ok, detail):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# 1. Settled high pressure --------------------------------------------------
s = analyse(minutes(24 * 60, 1026.0, humidity=45), NOW, elevation_m=0)
check("settled high", s.tendency == "Steady" and s.storm_probability < 25 and s.forecast_index <= 2,
      f"{s.tendency}, '{s.forecast}', storm {s.storm_probability}% ({s.storm_level})")

# 2. Approaching storm: falling 5 hPa / 3 h, humid, humidity rising ---------
storm = minutes(6 * 60, lambda i: 1012 - max(0, i - 180) * (5 / 180),
                temp=24, humidity=lambda i: 70 + max(0, i - 180) * (15 / 180))
s = analyse(storm, NOW, elevation_m=0)
check("approaching storm", s.change_3h is not None and -5.6 < s.change_3h < -4.4
      and s.storm_probability >= 60 and s.forecast_index >= 17,
      f"3h {s.change_3h:+.2f} hPa ({s.tendency}), '{s.forecast}', storm {s.storm_probability}% "
      f"{s.storm_level}, eta {s.storm_eta}, reasons {s.storm_reasons}")

# 3. Mountain drive: 600 m climb in 30 min (IMU says moving), weather steady
climb = minutes(5 * 60, lambda i: 1015 - (0 if i < 200 else min(i - 200, 30) * (70 / 30)),
                moving=lambda i: 200 <= i < 230)
s = analyse(climb, NOW, elevation_m=0)
check("mountain drive (IMU)", s.change_3h is not None and abs(s.change_3h) < 0.6
      and s.storm_probability < 25,
      f"station dropped 70 hPa, weather 3h {s.change_3h:+.2f} hPa, storm {s.storm_probability}%")

# 4. Same drive with no IMU: the rate filter must catch it
climb_no_imu = minutes(5 * 60, lambda i: 1015 - (0 if i < 200 else min(i - 200, 30) * (70 / 30)))
s = analyse(climb_no_imu, NOW, elevation_m=0)
check("mountain drive (no IMU)", s.change_3h is not None and abs(s.change_3h) < 0.6,
      f"weather 3h {s.change_3h:+.2f} hPa")

# 5. Overnight off, parked in the same place, weather fell 4 hPa overnight
evening = minutes(3 * 60, 1016.0, start=NOW - 14 * 3600)
morning = minutes(4 * 60, lambda i: 1012 - i * (1.0 / 240))
series = weather_pressure_series(evening + morning)
drop = series[-1].pressure - series[0].pressure
check("overnight same spot", -5.3 < drop < -4.5,
      f"weather change across the gap {drop:+.2f} hPa (real -5.0)")

# 6. Overnight off, moved 800 m higher: jump must not be read as weather
morning_high = minutes(4 * 60, lambda i: 925 - i * (1.0 / 240))
series = weather_pressure_series(evening + morning_high)
drop = series[-1].pressure - series[0].pressure
check("overnight relocated", abs(drop) < 3.0,
      f"station jumped -91 hPa, weather change {drop:+.2f} hPa")

# 7. Thunderstorm gust front: +2 hPa and -4 °C in 10 minutes
gust = minutes(4 * 60, lambda i: 1008 + (0 if i < 225 else min(i - 225, 10) * 0.2),
               temp=lambda i: 30 - (0 if i < 225 else min(i - 225, 10) * 0.4), humidity=65)
s = analyse(gust, NOW, elevation_m=0)
check("gust front", any("Thunderstorm" in a[1] for a in s.alerts) and s.storm_probability >= 90,
      f"alerts {s.alerts}")

# 8. Fog: temperature at dew point, 97% humidity
s = analyse(minutes(4 * 60, 1018.0, temp=8.0, humidity=97), NOW, elevation_m=0)
check("fog risk", any("Fog" in a[1] for a in s.alerts), f"alerts {s.alerts}")

# 9. Sea level from elevation (Denver-ish: 1609 m, 835 hPa station)
s = analyse(minutes(4 * 60, 835.0, temp=15), NOW, elevation_m=1609)
check("sea-level reduction", 1005 < s.slp_hpa < 1025, f"835 hPa at 1609 m -> {s.slp_hpa:.1f} hPa")

# 10. Physics spot checks
dp = dew_point_c(30, 70)
hi = heat_index_f(96, 65)
check("dew point / heat index", abs(dp - 23.9) < 0.3 and abs(hi - 121) < 2,
      f"dew point 30°C/70% = {dp:.1f}°C (23.9), heat index 96°F/65% = {hi:.0f}°F (NWS 121)")

# 11. Atmospheric tide: 4 days of flat weather with a Texas-sized daily
#     pressure swing must not read as a trend; a real fall on top must.
def tide(i, start=NOW - (4 * 1440 - 1) * 60):
    h = ((start + i * 60) % 86400) / 3600
    return 1.2 * math.cos(2 * math.pi * (h - 15) / 24) + 0.9 * math.cos(4 * math.pi * (h - 15) / 24)

s = analyse(minutes(4 * 1440, lambda i: 1013.0 + tide(i)), NOW, elevation_m=0)
raw = tide(4 * 1440 - 1) - tide(4 * 1440 - 181)
check("tide removed", s.tide_fitted and abs(raw) > 1.0 and abs(s.change_3h) < 0.4,
      f"raw tide 3h {raw:+.2f} hPa -> trend {s.change_3h:+.2f} hPa ({s.tendency}), "
      f"fitted swing {s.tide_amplitude:.2f} hPa")

storm = lambda i: 1013.0 + tide(i) - max(0, i - (4 * 1440 - 360)) * (6.0 / 360)
s = analyse(minutes(4 * 1440, storm), NOW, elevation_m=0)
check("tide + real fall", s.change_3h is not None and -3.6 < s.change_3h < -2.4,
      f"real fall -3.0 hPa/3h -> {s.change_3h:+.2f} hPa ({s.tendency}), storm {s.storm_probability}%")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
