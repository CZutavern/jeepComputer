"""
Seed the weather history with real observations from College Station, TX
(NWS station KCLL, Easterwood Field), so trends and forecasts work before
the Jeep's own sensors have built up history.

  - temperature, dew point, humidity, pressure: NWS ASOS observations
    (every 5 minutes), https://api.weather.gov/stations/KCLL/observations
  - sunlight: Open-Meteo hourly shortwave radiation for the same spot,
    converted to lux (~120 lx per W/m² for daylight)

Observations are interpolated to one sample per minute (the format the
Jeep logs) but never across gaps longer than 90 minutes. Pressure is
converted from the altimeter setting to station pressure at KCLL's
elevation, matching what the Jeep's barometer records. Rows are tagged
source='KCLL' and never overwrite the Jeep's own readings.

Usage:
  .venv/bin/python -m weather.seed [days]     # default 7
  .venv/bin/python -m weather.seed --remove   # delete seeded rows
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

from weather.analysis import Sample
from weather.store import WeatherStore


STATION = "KCLL"
LAT, LON = 30.58222, -96.36167
ELEVATION_M = 96.9
SOURCE = "KCLL"
USER_AGENT = "jeep-computer weather seed (contact@coltonzutavern.com)"
MAX_GAP_S = 90 * 60
LUX_PER_WM2 = 120.0


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def station_pressure(altimeter_hpa, temp_c):
    """Altimeter / sea-level value back to pressure at the station."""
    t = 15.0 if temp_c is None else temp_c
    h = ELEVATION_M
    return altimeter_hpa * (1 - (0.0065 * h) / (t + 0.0065 * h + 273.15)) ** 5.257


def nws_observations(start, end):
    url = (f"https://api.weather.gov/stations/{STATION}/observations"
           f"?start={iso(start)}&end={iso(end)}&limit=500")
    obs = []

    while url:
        page = fetch_json(url)
        features = page.get("features", [])
        for f in features:
            p = f["properties"]
            ts = datetime.fromisoformat(p["timestamp"]).timestamp()
            temp = (p.get("temperature") or {}).get("value")
            rh = (p.get("relativeHumidity") or {}).get("value")
            baro = (p.get("barometricPressure") or {}).get("value")
            obs.append((ts, temp, rh, None if baro is None else baro / 100.0))

        url = (page.get("pagination") or {}).get("next") if features else None
        if url and len(features) < 500:
            url = None

    obs.sort()
    return obs


def open_meteo_lux(days):
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
           f"&hourly=shortwave_radiation&past_days={days}&forecast_days=1&timezone=UTC")
    data = fetch_json(url)["hourly"]
    points = []
    for t, w in zip(data["time"], data["shortwave_radiation"]):
        if w is not None:
            ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp()
            points.append((ts, w * LUX_PER_WM2))
    return points


def interpolate(points, ts):
    """Linear interpolation in a sorted (ts, value) list; None across gaps."""
    lo, hi = 0, len(points) - 1
    if not points or ts < points[0][0] or ts > points[-1][0]:
        return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= ts:
            lo = mid
        else:
            hi = mid
    (t0, v0), (t1, v1) = points[lo], points[hi]
    if t1 - t0 > MAX_GAP_S:
        return None
    if t1 == t0:
        return v0
    return v0 + (v1 - v0) * (ts - t0) / (t1 - t0)


def seed(days=7):
    now = time.time()
    start = now - days * 86400

    print(f"Fetching {days} days of {STATION} observations...", flush=True)
    obs = nws_observations(start, now)
    print(f"  {len(obs)} observations", flush=True)

    print("Fetching sunlight (Open-Meteo)...", flush=True)
    lux = open_meteo_lux(days)

    temp = [(t, v) for t, v, _, _ in obs if v is not None]
    rh = [(t, v) for t, _, v, _ in obs if v is not None]
    baro = [(t, v) for t, _, _, v in obs if v is not None]

    store = WeatherStore()
    columns = [row[1] for row in store.db.execute("PRAGMA table_info(minute)")]
    if "source" not in columns:
        store.db.execute("ALTER TABLE minute ADD COLUMN source TEXT")

    rows = []
    minute = int(start // 60) + 1
    last = int(now // 60)
    while minute <= last:
        ts = minute * 60 + 30
        t = interpolate(temp, ts)
        p = interpolate(baro, ts)
        if p is not None:
            rows.append((ts, station_pressure(p, t), t, interpolate(rh, ts),
                         interpolate(lux, ts), 0, SOURCE))
        minute += 1

    # INSERT OR IGNORE: never overwrite the Jeep's own readings
    store.db.executemany(
        "INSERT OR IGNORE INTO minute (ts, pressure, temp_c, humidity, lux, moving, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    store.db.commit()

    print(f"Seeded {len(rows)} minutes of College Station weather "
          f"({iso(rows[0][0]) if rows else '-'} to {iso(rows[-1][0]) if rows else '-'})")


def remove():
    store = WeatherStore()
    columns = [row[1] for row in store.db.execute("PRAGMA table_info(minute)")]
    if "source" not in columns:
        print("No seeded data")
        return
    n = store.db.execute("DELETE FROM minute WHERE source = ?", (SOURCE,)).rowcount
    store.db.commit()
    print(f"Removed {n} seeded minutes")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        seed(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 7)
