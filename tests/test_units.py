"""
Unit checks for the pure logic added around the dashboard: stem panning,
tachometer maths, night dimming, sunrise/sunset, stale readings, trail
geometry. No hardware, no Qt, no CamillaDSP needed.

Run:  .venv/bin/python -m tests.test_units
"""

import math
import sys
import threading
import collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


# ---------------------------------------------------------------- stems
from audio import stems as S  # noqa: E402

highs = S.band_sets()["high"]
for label, (x, y) in {"front centre": (0, 1.05), "cabin centre": (0, 0), "rear right": (0.65, -1.25),
                      "far corner": (-0.95, 1.45)}.items():
    g = S.dbap(x, y, highs, 0.18)
    power = sum(v * v for v in g.values())
    check(f"DBAP constant power ({label})", abs(power - 1) < 1e-9, f"{power:.6f}")

g = S.dbap(-0.72, 1.05, highs, 0.18)
check("DBAP: source on front L is loudest there", max(g, key=g.get) == "front_l")

state = S.default_state()
table = S.mixer_sources(state)
inputs = {ch for gains in table.values() for ch in gains}
check("mixer covers exactly stem inputs 3-10",
      inputs == set(range(S.FIRST_STEM_INPUT, S.FIRST_STEM_INPUT + S.STEM_INPUTS)), str(sorted(inputs)))

state["stems"]["vocals"]["mute"] = True
t = S.mixer_sources(state)
check("muted stem sends nothing", all(t[o][6] == 0 and t[o][7] == 0 for o in t))
state["stems"]["vocals"]["mute"] = False
state["stems"]["drums"]["solo"] = True
t = S.mixer_sources(state)
check("solo leaves only that stem", all(t[o][ch] == 0 for o in t for ch in range(4, 10))
      and any(t[o][2] > 0 for o in t))
state["stems"]["drums"]["solo"] = False
state["stems_mute"] = True
check("stems mute silences every stem", all(v == 0 for g_ in S.mixer_sources(state).values() for v in g_.values()))

config = {"mixers": {"route_to_speakers": {"channels": {"in": 10, "out": 8}, "mapping": [
    {"dest": d, "sources": [{"channel": 0, "gain": 0, "inverted": False, "mute": False},
                            {"channel": 1, "gain": 0, "inverted": False, "mute": False}]} for d in range(8)]}}}
state = S.default_state()
state["carplay_mute"] = True
S.apply_to_config(config, state)
srcs = config["mixers"]["route_to_speakers"]["mapping"][0]["sources"]
check("CarPlay mute flags only inputs 1-2",
      all(s["mute"] for s in srcs if s["channel"] < 2) and not any(s["mute"] for s in srcs if s["channel"] >= 2))
mids = S.band_sets()["mid"]
g = S.dbap(0.3, 0.2, mids, 0.18, coherent=True)
check("8in pair gains sum to one (in-phase band)", abs(sum(g.values()) - 1) < 1e-9, f"{sum(g.values()):.6f}")
plain = S.dbap(0, 0, highs, 0.18)
seated = S.dbap(0, 0, highs, 0.18, seat=S.SEATS["driver"])
check("driver seat: near front L turned down, far rear R up",
      seated["front_l"] < plain["front_l"] and seated["rear_r"] > plain["rear_r"])
config["mixers"]["route_to_speakers"]["mapping"][6]["sources"][0]["gain"] = 6.0
S.apply_to_config(config, S.default_state())
sub = {s_["channel"]: s_["gain"] for s_ in config["mixers"]["route_to_speakers"]["mapping"][6]["sources"]}
check("stems' sub send follows CarPlay's (+6 dB)", abs(sub[S.FIRST_STEM_INPUT] - 10 ** (6 / 20)) < 1e-3,
      f"{sub[S.FIRST_STEM_INPUT]:.3f}")
state = S.default_state()
base = S.mixer_sources(state)
state["outputs"] = {"1": {"gain_db": -6.0}, "0": {"mute": True}}
trimmed = S.mixer_sources(state)
check("stems trim: -6 dB on output 2 only",
      all(abs(trimmed[1][ch] - base[1][ch] * 10 ** (-6 / 20)) < 1e-9 for ch in base[1])
      and trimmed[2] == base[2])
check("stems trim: muted output gets nothing", all(v == 0 for v in trimmed[0].values()))
state["outputs"] = {"3": {"inverted": True}}
S.apply_to_config(config, state)
m3 = config["mixers"]["route_to_speakers"]["mapping"][3]["sources"]
check("stems trim: polarity flips stem sources, not CarPlay's",
      all(s_["inverted"] for s_ in m3 if s_["channel"] >= 2) and not any(s_["inverted"] for s_ in m3 if s_["channel"] < 2))
state = S.default_state()
state["carplay_source_mute"] = {"2:0": True}
S.apply_to_config(config, state)
m2 = {s_["channel"]: s_["mute"] for s_ in config["mixers"]["route_to_speakers"]["mapping"][2]["sources"]}
check("Mixer tab's own CarPlay mute survives a stems push", m2[0] and not m2[1])
for src in config["mixers"]["route_to_speakers"]["mapping"][0]["sources"][:2]:
    src["gain"] = -12.5                 # CarPlay L and R into front L
state = S.default_state()
plain = S.mixer_sources(state)
S.apply_to_config(config, state)
front_l = {s_["channel"]: s_["gain"] for s_ in config["mixers"]["route_to_speakers"]["mapping"][0]["sources"]}
check("stems follow CarPlay's -12.5 dB on an output",
      abs(front_l[6] - plain[0][6] * 10 ** (-12.5 / 20)) < 1e-4, f"{front_l[6]:.4f}")
check("apply_to_config refuses an 8-input config",
      not S.apply_to_config({"mixers": {"route_to_speakers": {"channels": {"in": 8}, "mapping": []}}}, state))

# ---------------------------------------------------------------- library
from audio import stem_player as P  # noqa: E402

check("track numbers hidden", P.clean_name("08. Peachy") == "Peachy"
      and P.clean_name("01. GD Vibes (2014)") == "GD Vibes (2014)" and P.clean_name("888") == "888")
check("natural sort: 2 before 10", sorted(["10. B", "2. A"], key=P.natural_key) == ["2. A", "10. B"])
fake = {"vocals": Path("/x/vocals.flac")}
song = P.Song("08. Peachy", "folder", fake, ("Cavetown", "Albums", "01. GD Vibes (2014)", "08. Peachy"))
check("artist/album from Artist/Albums/Album/Song", (song.artist, song.album) == ("Cavetown", "01. GD Vibes (2014)"))
check("Artist/Song -> Singles", P.Song("x", "folder", fake, ("A", "x")).album == "Singles")
check("song at the top -> Unsorted", P.Song("x", "folder", fake, ("x",)).artist == "Unsorted")

# ---------------------------------------------------------------- gauge senders
from sensors import senders as G  # noqa: E402
import tempfile as _tempfile  # noqa: E402

# Use the default supply (1.35 V), not whatever the Jeep has learned
G.CAL_FILE = Path(_tempfile.mkdtemp()) / "none.json"

# Measured in the Jeep 2026-09-25, gauges on, engine off: oil 0, fuel near full
check("oil at 1.04 V reads ~0 psi", G.convert("oil_pressure", 1.04) < 3, f"{G.convert('oil_pressure', 1.04):.1f}")
check("fuel at 0.49 V reads near full", 85 < G.convert("fuel", 0.49) < 97, f"{G.convert('fuel', 0.49):.0f} %")
# Checked 2026-10-05 idling warm: 21.2 ohm judged 180-185 F from the factory gauge
check("coolant curve passes through the checked point", 180 < G.CURVES["coolant"].value(21.2) < 185,
      f"{G.CURVES['coolant'].value(21.2):.0f} F")
check("coolant hotter as resistance falls", G.CURVES["coolant"].value(13) > G.CURVES["coolant"].value(36))
check("gauges off (0 V) reads as no value", all(G.convert(n, 0.0) is None for n in G.CHANNELS))
check("fuel falls as resistance rises", G.convert("fuel", 0.6) > G.convert("fuel", 0.9) > G.convert("fuel", 1.1))

# ---------------------------------------------------------------- tach
from sensors import tach as T  # noqa: E402


class FakeTach(T.Tach):
    def __init__(self, cylinders):
        self.cylinders, self.pin = cylinders, 12
        self.lock = threading.Lock()
        self.periods = collections.deque(maxlen=T.PERIODS)
        self.last_tick = self.last_spark = None
        self.sparks = 0


for cyl, rpm in ((8, 650), (8, 6000), (6, 800)):
    t = FakeTach(cyl)
    period = int(120e9 / (cyl * rpm))
    for i in range(40):
        t._edge(i * period)
        t._edge(i * period + 300_000)          # coil ringing: must be ignored
    check(f"tach {cyl} cyl {rpm} rpm", abs(t.rpm() - rpm) < 1, f"{t.rpm():.1f}")
check("tach never wired -> None", FakeTach(8).rpm() is None)
try:
    T.Tach({"pin": 16, "cylinders": 8})
    check("tach refuses GPIO 16 (UPS charge control)", False)
except RuntimeError:
    check("tach refuses GPIO 16 (UPS charge control)", True)

# ---------------------------------------------------------------- night dimming
from dashboard import night_dim as N  # noqa: E402

s = dict(N.DEFAULTS)
check("no dimming in daylight", N.dim_for(30000, s) == 0)
check("full night dimming in the dark", abs(N.dim_for(0, s) - s["night_dim"]) < 1e-9)
levels = [N.dim_for(l, s) for l in (0, 2, 5, 10, 20, 40, 80)]
check("dimming falls as light rises", all(a >= b for a, b in zip(levels, levels[1:])))

sent = []
d = N.NightDimmer(lambda level: sent.append(level) or True)
d.settings = dict(N.DEFAULTS, brightness=0.6, auto_dim=False)
d.apply(force=True)
check("manual brightness 60% -> 40% dim", abs(sent[-1] - 0.4) < 1e-9, f"{sent[-1]}")

# ---------------------------------------------------------------- sun
from weather.sun import sun_times  # noqa: E402

st = sun_times(1790265600, 30.58222, -96.36167)       # 2026-09-24, College Station
day_h = (st["sunset"] - st["sunrise"]) / 3600
check("equinox-ish day length ~12 h", 11.7 < day_h < 12.4, f"{day_h:.2f} h")
check("sunrise before sunset", st["sunrise"] < st["sunset"])

# ---------------------------------------------------------------- stale readings
from weather.analysis import metric_summary  # noqa: E402

now = 1_790_000_000
old = metric_summary([(now - 7200 + i * 60, 20.0) for i in range(60)], now, "°F", 1)
fresh = metric_summary([(now - 1800 + i * 60, 20.0) for i in range(30)], now, "°F", 1)
check("stale metric has no 'now'", old["now"] is None and old["last"] == 20.0)
check("fresh metric has 'now'", fresh["now"] == 20.0)

# ---------------------------------------------------------------- trail geometry
from dashboard.pages.offroad import tip_angles  # noqa: E402

roll_tip, pitch_tip = tip_angles({"track_in": 58.6, "cg_height_in": 30.0, "wheelbase_in": 108.7})
check("roll tip angle from SSF", abs(roll_tip - math.degrees(math.atan(29.3 / 30))) < 0.01, f"{roll_tip:.1f}°")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
