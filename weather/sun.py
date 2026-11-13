"""
Sunrise / sunset (NOAA solar calculator equations, accurate to about a
minute between the polar circles).

Location comes from ~/.config/jeep_computer/weather.json ("latitude",
"longitude"), defaulting to College Station, TX until a GPS provides it.
"""

import json
import math
import time
from pathlib import Path

SETTINGS = Path.home() / ".config/jeep_computer/weather.json"
DEFAULT_LAT, DEFAULT_LON = 30.58222, -96.36167   # KCLL, College Station
ZENITH = 90.833                                  # refraction + solar disc


def location():
    try:
        s = json.loads(SETTINGS.read_text())
        return float(s.get("latitude", DEFAULT_LAT)), float(s.get("longitude", DEFAULT_LON))
    except (OSError, ValueError, TypeError):
        return DEFAULT_LAT, DEFAULT_LON


def _sun_times_utc(day_ts, lat, lon):
    """(sunrise, sunset) as unix times for the UTC day containing day_ts,
    or None for polar day/night."""
    jd = day_ts / 86400.0 + 2440587.5
    jd = math.floor(jd - 0.5) + 0.5 + 0.5          # noon UTC of that date
    t = (jd - 2451545.0) / 36525.0

    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t) + math.sin(3 * mr) * 0.000289)
    omega = 125.04 - 1934.136 * t
    lam = l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(lam)))

    y = math.tan(math.radians(eps / 2)) ** 2
    l0r = math.radians(l0)
    eq_time = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
                               + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                               - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))

    latr = math.radians(lat)
    cos_ha = (math.cos(math.radians(ZENITH)) / (math.cos(latr) * math.cos(decl))
              - math.tan(latr) * math.tan(decl))
    if abs(cos_ha) > 1:
        return None
    ha = math.degrees(math.acos(cos_ha))

    noon_min = 720 - 4 * lon - eq_time                 # minutes after 00:00 UTC
    midnight = (jd - 0.5 - 2440587.5) * 86400
    return midnight + (noon_min - 4 * ha) * 60, midnight + (noon_min + 4 * ha) * 60


def sun_times(now=None, lat=None, lon=None):
    """
    {"sunrise", "sunset", "daylight": bool, "next_sunrise"} around `now`.
    The local solar day can straddle two UTC dates, so check neighbours.
    """
    now = time.time() if now is None else now
    if lat is None or lon is None:
        lat, lon = location()

    events = []
    for d in (-1, 0, 1, 2):
        times = _sun_times_utc(now + d * 86400, lat, lon)
        if times:
            events.append(times)
    if not events:
        return None

    # Today's pair: the one whose sunset is the first still ahead, or the last one passed
    for rise, set_ in events:
        if set_ >= now:
            break
    next_rise = next((r for r, _ in events if r > now), None)
    return {"sunrise": rise, "sunset": set_, "daylight": rise <= now < set_, "next_sunrise": next_rise}
