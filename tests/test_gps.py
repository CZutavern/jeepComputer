import json
import tempfile
from pathlib import Path

import sensors.gps as g


def nmea(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"${body}*{c:02X}"


GGA = nmea("GPGGA,123519,3034.933,N,09621.700,W,1,08,0.9,98.4,M,-24.0,M,,")
RMC = nmea("GPRMC,123519,A,3034.933,N,09621.700,W,022.4,084.4,230394,,,A")
RMC_VOID = nmea("GPRMC,123519,V,,,,,,,230394,,,N")


def test_checksum_and_parse():
    assert g.checksum_ok(GGA) and g.checksum_ok(RMC)
    assert not g.checksum_ok(GGA[:-2] + "00")
    assert g.parse_nmea(GGA.replace("*", "x")) is None
    a = g.parse_nmea(GGA)
    assert a["quality"] == 1 and a["satellites"] == 8 and a["hdop"] == 0.9 and a["altitude_m"] == 98.4
    assert abs(a["lat"] - 30.58222) < 0.001 and abs(a["lon"] + 96.36167) < 0.001     # south/west are negative
    r = g.parse_nmea(RMC)
    assert r["valid"] and abs(r["speed_mph"] - 22.4 * g.KNOT_MPH) < 1e-6 and r["course"] == 84.4
    assert not g.parse_nmea(RMC_VOID)["valid"]


def test_other_talkers_and_garbage():
    assert g.parse_nmea(nmea("GNRMC,123519,A,3034.933,N,09621.700,W,000.0,,230394,,,A"))["valid"]
    assert g.parse_nmea("") is None and g.parse_nmea("$GPGSV,1,1*00") is None


def test_odometer_moving_only_and_gaps(tmp_path):
    o = g.Odometer(path=tmp_path / "o.json", save_every_s=0)
    o.add(60.0, 0.0)
    o.add(60.0, 1.0)                 # 1 s at 60 mph = 1/60 mile
    assert abs(o.miles - 1 / 60) < 1e-9
    o.add(1.0, 2.0)                  # stopped (noise): not counted
    assert abs(o.miles - 1 / 60) < 1e-9
    o.add(60.0, 20.0)                # 18 s gap: not integrated
    assert abs(o.miles - 1 / 60) < 1e-9
    o.gap(); o.add(60.0, 30.0)
    assert abs(o.miles - 1 / 60) < 1e-9
    assert json.loads((tmp_path / "o.json").read_text())["miles"] == round(1 / 60, 3)
    assert g.Odometer(path=tmp_path / "o.json").miles == round(1 / 60, 3)     # survives a restart


def test_nmcli_parse_skips_hotspot_nomap_and_escapes():
    text = "\n".join([
        r"HomeNet:AA\:BB\:CC\:DD\:EE\:01:80",
        r"jeep-hotspot:AA\:BB\:CC\:DD\:EE\:02:90",
        r"Neighbor_nomap:AA\:BB\:CC\:DD\:EE\:03:70",
        r"Cafe:AA\:BB\:CC\:DD\:EE\:04:50",
        r"bad:short:50",
    ])
    aps = g.parse_wifi_list(text)
    assert [a["macAddress"] for a in aps] == ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:04"]
    assert aps[0]["signalStrength"] == -60 and aps[1]["signalStrength"] == -75


def test_geolocate_with_fake_service(monkeypatch):
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"location": {"lat": 30.6, "lng": -96.3}, "accuracy": 55.0}).encode()
    seen = {}
    def fake(request, timeout=0):
        seen["body"] = json.loads(request.data.decode()); seen["ua"] = request.headers.get("User-agent")
        return Resp()
    monkeypatch.setattr(g.urllib.request, "urlopen", fake)
    aps = [{"macAddress": "AA:BB:CC:DD:EE:01", "signalStrength": -60}, {"macAddress": "AA:BB:CC:DD:EE:04", "signalStrength": -75}]
    assert g.geolocate(aps, "http://x") == (30.6, -96.3, 55.0)
    assert seen["ua"] == g.USER_AGENT and len(seen["body"]["wifiAccessPoints"]) == 2
    assert g.geolocate(aps[:1], "http://x") is None                 # one access point is not enough
    monkeypatch.setattr(g.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert g.geolocate(aps, "http://x") is None


def test_best_position_prefers_a_fresh_gps_fix():
    gps = {"lat": 1.0, "lon": 2.0, "t": 100.0}
    wifi = {"lat": 3.0, "lon": 4.0, "accuracy_m": 40, "t": 50.0}
    assert g.best_position(gps, wifi, 102.0)[0] == "gps"
    assert g.best_position(gps, wifi, 100.0 + g.FIX_STALE_S + 1)[0] == "wifi"      # GPS lost: Wi-Fi again
    assert g.best_position(None, wifi, 60.0)[0] == "wifi"
    assert g.best_position(None, wifi, 50.0 + g.WIFI_STALE_S + 1)[0] is None
    assert g.best_position(None, None, 0.0) == (None, {})


def make_receiver(tmp_path, monkeypatch, **settings):
    monkeypatch.setattr(g, "POSITION_FILE", tmp_path / "pos.json")
    monkeypatch.setattr(g.GPSReceiver, "_gps_loop", lambda self: None)       # no serial port, no real threads of work
    monkeypatch.setattr(g.GPSReceiver, "_wifi_loop", lambda self: None)
    t = [1000.0]
    r = g.GPSReceiver(settings={**g.DEFAULTS, **settings}, clock=lambda: t[0])
    r.odometer = g.Odometer(path=tmp_path / "o.json", save_every_s=0)
    return r, t


def test_receiver_uses_wifi_until_gps_has_a_fix(tmp_path, monkeypatch):
    r, t = make_receiver(tmp_path, monkeypatch)
    monkeypatch.setattr(g, "wifi_connected", lambda: True)
    monkeypatch.setattr(g, "scan_wifi", lambda: [{"macAddress": "A", "signalStrength": -50}, {"macAddress": "B", "signalStrength": -60}])
    monkeypatch.setattr(g, "geolocate", lambda aps, url, timeout=8: (30.6, -96.3, 40.0))
    t[0] += 10
    assert not r.maybe_wifi(t[0])                         # too soon: waits wifi_after_s (30 s) first
    t[0] += 25
    assert r.maybe_wifi(t[0])
    d = r.read()
    assert d["source"] == "wifi" and d["lat"] == 30.6 and d["accuracy_m"] == 40 and d["speed_mph"] is None and not d["fix"]
    assert json.loads((tmp_path / "pos.json").read_text())["source"] == "wifi"
    # the GPS gets a fix: it takes over
    r.feed(GGA, now=t[0]); r.feed(RMC, now=t[0])
    d = r.read()
    assert d["source"] == "gps" and d["fix"] and d["satellites"] == 8 and d["altitude_ft"] == round(98.4 * g.M_FT)
    assert abs(d["lat"] - 30.58222) < 0.001
    assert json.loads((tmp_path / "pos.json").read_text())["source"] == "gps"
    assert not r.wifi_wanted(t[0] + 1)                    # no more Wi-Fi lookups while the GPS is good


def test_wifi_fallback_can_be_turned_off_and_needs_a_network(tmp_path, monkeypatch):
    r, t = make_receiver(tmp_path, monkeypatch, wifi_fallback=False)
    t[0] += 100
    assert not r.wifi_wanted(t[0])
    r2, t2 = make_receiver(tmp_path, monkeypatch)
    t2[0] += 100
    monkeypatch.setattr(g, "wifi_connected", lambda: False)
    assert not r2.maybe_wifi(t2[0])                       # not on Wi-Fi: nothing sent anywhere
