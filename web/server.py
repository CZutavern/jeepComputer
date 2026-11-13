"""
Jeep Computer on your phone: http://raspberrypi.local:8080

A small read-only web page (jeep-web.service) with the weather analysis and
trend charts, live vehicle readings, UPS / battery state and a download of
the weather history. It never touches the I2C bus: live readings come from
the snapshot the sensor owner writes (sensors/live.py), weather from the
SQLite history, the UPS from /run/jeep-ups/status.json.

Endpoints:
    /                    the page (web/static/index.html)
    /api/live            vehicle + sensors + UPS, refreshed every second
    /api/weather         weather analysis with chart data (recomputed each minute)
    /api/history.csv     minute history (?days=7, max 60)

Run by hand:  .venv/bin/python web/server.py   (JEEP_WEB_PORT, default 8080)
"""

import csv
import io
import json
import math
import os
import sqlite3
import sys
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sensors.live import read_live               # noqa: E402
from sensors.senders import read_senders         # noqa: E402
from sensors.tach import gauge_scale             # noqa: E402
from weather.analysis import analyse, c_to_f     # noqa: E402
from weather.store import DB_PATH, WeatherStore  # noqa: E402
from weather.sun import sun_times                # noqa: E402

PORT = int(os.environ.get("JEEP_WEB_PORT", "8080"))
STATIC = ROOT / "web/static"
UPS_STATUS = Path("/run/jeep-ups/status.json")
WEATHER_SETTINGS = Path.home() / ".config/jeep_computer/weather.json"
WEATHER_CACHE_S = 60
LIVE_STALE_S = 10

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


def clean(value):
    """JSON-safe: NaN/inf -> None, tuples -> lists, recursively."""
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else round(value, 4)
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def sensor(data, name, key):
    info = (data or {}).get(name) or {}
    if info.get("status") != "OK":
        return None
    return (info.get("data") or {}).get(key)


# -------------------------------------------------------------
# Live vehicle data
# -------------------------------------------------------------

def live_payload():
    snap = read_live()
    now = time.time()
    data = (snap or {}).get("sensors") or {}
    age = None if snap is None else now - snap.get("time", 0)
    fresh = age is not None and age < LIVE_STALE_S
    if not fresh:
        data = {}

    senders = read_senders(data) if data else {}
    cabin_c = sensor(data, "pressure", "temperature_c")
    outside_c = sensor(data, "outside", "temperature_c")

    sun = sun_times()
    ups = read_json(UPS_STATUS)

    return clean({
        "time": now,
        "source": (snap or {}).get("source"),
        "age_s": age,
        "fresh": fresh,
        "vehicle": {
            "rpm": sensor(data, "tach", "rpm"),
            "rpm_redline": gauge_scale()[1],
            "coolant_f": senders.get("coolant"),
            "oil_psi": senders.get("oil_pressure"),
            "fuel_pct": senders.get("fuel"),
            "battery_v": battery if (battery := sensor(data, "battery", "voltage")) and battery > 1 else None,
            "key_on": sensor(data, "acc", "key_on"),
            "cabin_f": None if cabin_c is None else c_to_f(cabin_c),
            "outside_f": None if outside_c is None else c_to_f(outside_c),
            "humidity": sensor(data, "outside", "humidity"),
            "amp_box_f": sensor(data, "amp_box", "temperature_f"),
            "heading": sensor(data, "imu", "heading"),
            "roll": sensor(data, "imu", "roll"),
            "pitch": sensor(data, "imu", "pitch"),
            "pressure_hpa": sensor(data, "pressure", "pressure"),
            "lux": sensor(data, "light", "lux"),
        },
        "sensors": {name: (info or {}).get("status") for name, info in data.items()},
        "ups": ups,
        "sun": sun,
    })


# -------------------------------------------------------------
# Weather (cached: the analysis takes a moment on 72 h of minutes)
# -------------------------------------------------------------

_weather = {"time": 0.0, "payload": None}
_weather_lock = threading.Lock()


def weather_payload():
    with _weather_lock:
        if _weather["payload"] is not None and time.time() - _weather["time"] < WEATHER_CACHE_S:
            return _weather["payload"]

        settings = read_json(WEATHER_SETTINGS) or {}
        elevation_ft = settings.get("elevation_ft")
        store = WeatherStore()
        try:
            summary = analyse(
                store.since(72 * 3600),
                elevation_m=None if elevation_ft is None else elevation_ft * 0.3048,
                northern=settings.get("northern", True),
            )
        finally:
            store.db.close()

        body = asdict(summary)
        body.pop("history", None)          # the metrics carry the chart data
        body["elevation_ft"] = elevation_ft
        body["time"] = time.time()
        payload = json.dumps(clean(body)).encode()

        _weather.update(time=time.time(), payload=payload)
        return payload


def history_csv(days):
    days = max(1, min(60, days))
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        columns = [row[1] for row in db.execute("PRAGMA table_info(minute)")]
        source = "source" if "source" in columns else "NULL"
        rows = db.execute(
            f"SELECT ts, pressure, temp_c, humidity, lux, moving, {source} FROM minute "
            "WHERE ts >= ? ORDER BY ts", (time.time() - days * 86400,)).fetchall()
    finally:
        db.close()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["time_utc", "local_time", "pressure_hpa", "temp_c", "temp_f",
                     "humidity_pct", "lux", "moving", "source"])
    for ts, p, t, h, lux, moving, src in rows:
        writer.writerow([
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)),
            None if p is None else round(p, 2),
            None if t is None else round(t, 2),
            None if t is None else round(c_to_f(t), 1),
            None if h is None else round(h, 1),
            None if lux is None else round(lux),
            "" if moving is None else moving,
            src or "jeep",
        ])
    return out.getvalue().encode()


# -------------------------------------------------------------
# HTTP
# -------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "JeepComputer/1.0"

    def log_message(self, fmt, *args):
        pass                                # quiet: the phone polls every few seconds

    def send(self, body, content_type, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/api/live":
                self.send(json.dumps(live_payload()).encode(), "application/json")
            elif url.path == "/api/weather":
                self.send(weather_payload(), "application/json")
            elif url.path == "/api/history.csv":
                days = int((parse_qs(url.query).get("days") or ["7"])[0])
                name = time.strftime("jeep-weather-%Y%m%d.csv")
                self.send(history_csv(days), "text/csv; charset=utf-8",
                          extra={"Content-Disposition": f'attachment; filename="{name}"'})
            else:
                self.static(url.path)
        except BrokenPipeError:
            pass
        except Exception as e:
            print("web: error serving", url.path, "-", e, flush=True)
            self.send(json.dumps({"error": str(e)}).encode(), "application/json", 500)

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            self.send(b"not found", "text/plain", 404)
            return
        self.send(target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"))


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"Jeep Computer web page on http://raspberrypi.local:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
