"""
GPS position, speed and a trip odometer, with a Wi-Fi position as the fallback until the GPS has a fix.

GPS: a USB u-blox dongle (VK-162, GPYes 2.0, ...) that sends NMEA text over a serial port (/dev/ttyACM*).
It is found through /dev/serial/by-id/*u-blox* (or "device" in gps.json). No gpsd needed.

Wi-Fi fallback: while there is no GPS fix (dongle not plugged in yet, still searching, parked indoors), and
the Pi is on a Wi-Fi network, the nearby access points are scanned (nmcli) and sent to a geolocation service
(beaconDB, an open MLS-compatible service) that answers with a latitude / longitude and an accuracy. This SENDS
the MAC addresses and signal strengths of the Wi-Fi networks around the Jeep, and the Pi's IP address, to that
service. Turn it off with {"wifi_fallback": false} in ~/.config/jeep_computer/gps.json. The moment the GPS has
a fix it takes over and the Wi-Fi lookups stop. Hotspot networks (the Jeep's own "jeep-hotspot") and networks
whose name ends in "_nomap" are never sent.

Settings (~/.config/jeep_computer/gps.json), all optional:
    device          serial port, default: auto-detect a u-blox in /dev/serial/by-id
    baud            9600
    wifi_fallback   true
    geolocate_url   https://api.beacondb.net/v1/geolocate
    wifi_after_s    30    no GPS fix for this long before the first Wi-Fi lookup
    wifi_refresh_s  600   minimum time between Wi-Fi lookups

Files written:
    ~/.cache/jeep_computer/position.json       latest position (weather/sun.py uses it for sunrise / sunset)
    ~/.local/share/jeep_computer/odometer.json miles driven since the dongle was first used (GPS speed x time)
"""

import glob
import json
import math
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

try:
    import serial
except ImportError:          # development machines
    serial = None

SETTINGS = Path.home() / ".config/jeep_computer/gps.json"
POSITION_FILE = Path.home() / ".cache/jeep_computer/position.json"
ODOMETER_FILE = Path.home() / ".local/share/jeep_computer/odometer.json"

DEFAULTS = {
    "device": None,
    "baud": 9600,
    "wifi_fallback": True,
    "geolocate_url": "https://api.beacondb.net/v1/geolocate",
    "wifi_after_s": 30,
    "wifi_refresh_s": 600,
}

KNOT_MPH = 1.15077945
M_FT = 3.28084
FIX_STALE_S = 5.0               # no good sentence for this long = no GPS fix
WIFI_STALE_S = 3600.0           # a Wi-Fi position older than this is dropped
MOVING_MPH = 2.0                # below this the odometer ignores GPS speed (it is noise when stopped)
MAX_STEP_S = 5.0                # a longer gap between fixes is not integrated
HOTSPOT_NAMES = {"jeep-hotspot", "Jeep Computer"}
USER_AGENT = "jeep-computer-gps/1.0"


def load_settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


# ---------------------------------------------------------------- NMEA

def checksum_ok(line):
    """True if '$...*HH' has the right checksum (a sentence without one is rejected)."""
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, given = line[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(given.strip()[:2], 16)
    except ValueError:
        return False


def _latlon(value, hemi):
    """'4807.038', 'N' -> 48.1173; 'dddmm.mmmm' for longitude."""
    if not value or not hemi:
        return None
    dot = value.index(".") if "." in value else len(value)
    deg = float(value[:dot - 2])
    minutes = float(value[dot - 2:])
    out = deg + minutes / 60.0
    return -out if hemi in ("S", "W") else out


def parse_nmea(line):
    """
    Parse one NMEA line. Returns a dict or None.
      GGA -> {"type": "GGA", "quality", "satellites", "hdop", "altitude_m", "lat", "lon"}
      RMC -> {"type": "RMC", "valid", "lat", "lon", "speed_mph", "course", "utc"}
    """
    line = line.strip()
    if not checksum_ok(line):
        return None
    fields = line[1:line.index("*")].split(",")
    kind = fields[0][2:]
    try:
        if kind == "GGA" and len(fields) >= 10:
            quality = int(fields[6] or 0)
            return {
                "type": "GGA", "quality": quality,
                "lat": _latlon(fields[2], fields[3]), "lon": _latlon(fields[4], fields[5]),
                "satellites": int(fields[7] or 0),
                "hdop": float(fields[8]) if fields[8] else None,
                "altitude_m": float(fields[9]) if fields[9] else None,
            }
        if kind == "RMC" and len(fields) >= 10:
            return {
                "type": "RMC", "valid": fields[2] == "A",
                "lat": _latlon(fields[3], fields[4]), "lon": _latlon(fields[5], fields[6]),
                "speed_mph": float(fields[7]) * KNOT_MPH if fields[7] else 0.0,
                "course": float(fields[8]) if fields[8] else None,
                "utc": fields[1][:6] + " " + fields[9],     # hhmmss ddmmyy
            }
    except (ValueError, IndexError):
        return None
    return None


# ---------------------------------------------------------------- Wi-Fi fallback

def _split_nmcli(line):
    """nmcli -t escapes ':' inside a field as '\\:'."""
    out, cur, i = [], "", 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return out


def parse_wifi_list(text):
    """`nmcli -t -f SSID,BSSID,SIGNAL dev wifi list` -> [{"macAddress", "signalStrength"}], hotspots and _nomap left out."""
    aps = []
    for line in text.splitlines():
        parts = _split_nmcli(line)
        if len(parts) < 3:
            continue
        ssid, bssid, signal = parts[0], parts[1], parts[2]
        if ssid in HOTSPOT_NAMES or ssid.endswith("_nomap") or len(bssid) != 17:
            continue
        try:
            dbm = int(signal) / 2 - 100          # nmcli's 0-100 % is roughly (dBm + 100) * 2
        except ValueError:
            continue
        aps.append({"macAddress": bssid.upper(), "signalStrength": round(dbm)})
    return aps


def wifi_connected():
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "TYPE,STATE", "dev"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(l.startswith("wifi:connected") for l in out.splitlines())


def scan_wifi():
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL", "dev", "wifi", "list", "--rescan", "auto"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_wifi_list(out)


def geolocate(access_points, url, timeout=8):
    """POST the access points; returns (lat, lon, accuracy_m) or None."""
    if len(access_points) < 2:
        return None
    body = json.dumps({"wifiAccessPoints": access_points, "considerIp": False}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
        loc = data["location"]
        return float(loc["lat"]), float(loc["lng"]), float(data.get("accuracy") or 0)
    except Exception:
        return None


# ---------------------------------------------------------------- odometer

class Odometer:
    """Miles driven: GPS speed x time between fixes, saved to a file now and then."""

    def __init__(self, path=ODOMETER_FILE, save_every_s=30.0):
        self.path = Path(path)
        self.save_every_s = save_every_s
        self.miles = self._load()
        self.last_t = None
        self.saved_t = 0.0
        self.saved_miles = self.miles

    def _load(self):
        try:
            return float(json.loads(self.path.read_text())["miles"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    def add(self, speed_mph, t):
        """Feed one valid fix (speed in mph, time t in seconds)."""
        if self.last_t is not None and speed_mph is not None and speed_mph >= MOVING_MPH:
            dt = t - self.last_t
            if 0 < dt <= MAX_STEP_S:
                self.miles += speed_mph * dt / 3600.0
        self.last_t = t
        self.save(t)

    def gap(self):
        """Call when the fix is lost, so the time without a fix is not counted."""
        self.last_t = None

    def save(self, t, force=False):
        if not force and (t - self.saved_t < self.save_every_s or self.miles == self.saved_miles):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"miles": round(self.miles, 3), "updated": int(time.time())}))
            os.replace(tmp, self.path)
            self.saved_t, self.saved_miles = t, self.miles
        except OSError:
            pass


# ---------------------------------------------------------------- position choice

def best_position(gps, wifi, now):
    """
    gps: {"lat", "lon", ..., "t"} (t = monotonic time of the last good fix) or None
    wifi: {"lat", "lon", "accuracy_m", "t"} or None
    Returns ("gps" | "wifi" | None, position dict).
    """
    if gps and now - gps["t"] <= FIX_STALE_S:
        return "gps", gps
    if wifi and now - wifi["t"] <= WIFI_STALE_S:
        return "wifi", wifi
    return None, {}


# ---------------------------------------------------------------- the receiver

class GPSReceiver:

    def __init__(self, settings=None, clock=time.monotonic):
        self.settings = settings or load_settings()
        self.clock = clock
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.device = None                  # path of the open serial port, None = not found
        self.gps = None                     # last good GPS fix
        self.gga = {}                       # latest GGA details (satellites, hdop, altitude)
        self.wifi = None
        self.last_wifi_try = -1e9
        self.started = clock()
        self.odometer = Odometer()
        self.last_write = 0.0
        self.last_source = None
        self.threads = [threading.Thread(target=self._gps_loop, name="gps", daemon=True),
                        threading.Thread(target=self._wifi_loop, name="gps-wifi", daemon=True)]
        for t in self.threads:
            t.start()

    # -- serial -------------------------------------------------

    def find_device(self):
        if self.settings.get("device"):
            return self.settings["device"] if os.path.exists(self.settings["device"]) else None
        found = sorted(glob.glob("/dev/serial/by-id/*u-blox*") + glob.glob("/dev/serial/by-id/*U-blox*"))
        return found[0] if found else None

    def _gps_loop(self):
        while not self.stopping.is_set():
            path = self.find_device()
            if path is None or serial is None:
                with self.lock:
                    self.device = None
                self.stopping.wait(3.0)
                continue
            try:
                with serial.Serial(path, int(self.settings["baud"]), timeout=1.0) as port:
                    with self.lock:
                        self.device = path
                    while not self.stopping.is_set():
                        raw = port.readline()
                        if raw:
                            self.feed(raw.decode("ascii", "ignore"))
                        self.check_stale()
            except Exception as e:                      # unplugged, USB power cut, permissions
                print("gps:", e, flush=True)
            with self.lock:
                self.device = None
            self.odometer.gap()
            self.stopping.wait(3.0)

    def feed(self, line, now=None):
        """One NMEA line from the dongle."""
        now = self.clock() if now is None else now
        msg = parse_nmea(line)
        if msg is None:
            return
        with self.lock:
            if msg["type"] == "GGA":
                self.gga = {k: msg[k] for k in ("quality", "satellites", "hdop", "altitude_m")}
                return
            if msg["type"] == "RMC" and msg["valid"] and msg["lat"] is not None:
                self.gps = {"lat": msg["lat"], "lon": msg["lon"], "speed_mph": msg["speed_mph"],
                            "heading": msg["course"], "utc": msg["utc"], "t": now}
                speed = msg["speed_mph"]
            else:
                return
        self.odometer.add(speed, now)
        self.write_position(now)

    def check_stale(self):
        with self.lock:
            lost = self.gps is not None and self.clock() - self.gps["t"] > FIX_STALE_S
        if lost:
            self.odometer.gap()

    # -- Wi-Fi fallback ------------------------------------------

    def _wifi_loop(self):
        while not self.stopping.wait(10.0):
            try:
                self.maybe_wifi()
            except Exception as e:
                print("gps wifi:", e, flush=True)

    def wifi_wanted(self, now):
        if not self.settings.get("wifi_fallback", True):
            return False
        with self.lock:
            fresh_gps = self.gps is not None and now - self.gps["t"] <= FIX_STALE_S
            quiet = now - (self.gps["t"] if self.gps else self.started)
        if fresh_gps or quiet < float(self.settings["wifi_after_s"]):
            return False
        return now - self.last_wifi_try >= float(self.settings["wifi_refresh_s"])

    def maybe_wifi(self, now=None):
        now = self.clock() if now is None else now
        if not self.wifi_wanted(now) or not wifi_connected():
            return False
        self.last_wifi_try = now
        result = geolocate(scan_wifi(), self.settings["geolocate_url"])
        if result is None:
            return False
        lat, lon, accuracy = result
        with self.lock:
            self.wifi = {"lat": lat, "lon": lon, "accuracy_m": accuracy, "t": now}
        self.write_position(now)
        return True

    # -- output --------------------------------------------------

    def write_position(self, now):
        with self.lock:
            source, pos = best_position(self.gps, self.wifi, now)
        if source is None:
            return
        # at most every 5 s, but a change of source (Wi-Fi -> GPS) is written at once
        if source == self.last_source and now - self.last_write < 5.0:
            return
        self.last_write = now
        self.last_source = source
        try:
            POSITION_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = POSITION_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"lat": pos["lat"], "lon": pos["lon"], "source": source, "ts": int(time.time())}))
            os.replace(tmp, POSITION_FILE)
        except OSError:
            pass

    def read(self):
        now = self.clock()
        with self.lock:
            source, pos = best_position(self.gps, self.wifi, now)
            device = self.device
            gga = dict(self.gga)
        data = {"source": source, "device": device, "fix": source == "gps",
                "lat": pos.get("lat"), "lon": pos.get("lon"),
                "speed_mph": pos.get("speed_mph") if source == "gps" else None,
                "heading": pos.get("heading") if source == "gps" else None,
                "altitude_ft": None, "satellites": None, "hdop": None,
                "accuracy_m": None, "age_s": None if source is None else round(now - pos["t"], 1),
                "odometer_miles": round(self.odometer.miles, 1)}
        if source == "gps":
            if gga.get("altitude_m") is not None:
                data["altitude_ft"] = round(gga["altitude_m"] * M_FT)
            data["satellites"] = gga.get("satellites")
            data["hdop"] = gga.get("hdop")
            if gga.get("hdop") is not None:
                data["accuracy_m"] = round(gga["hdop"] * 5.0, 1)     # rough: hdop x ~5 m
        elif source == "wifi":
            data["accuracy_m"] = round(pos.get("accuracy_m") or 0)
        return data

    def close(self):
        self.stopping.set()
        self.odometer.save(self.clock(), force=True)
