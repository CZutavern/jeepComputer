"""
Stem spatial mixer: place drums / bass / vocals / other anywhere in the
cabin and render them to the Jeep's speakers.

Signal path
    stem sources -> jeep_stems (8 ch PipeWire sink: drums L/R, bass L/R,
    vocals L/R, other L/R) -> CamillaDSP inputs 3-10 -> the mixer (gains set
    here) -> the usual per-speaker crossovers, trims, time alignment and
    limiter. So placement sits in front of the existing tuning, not around it.

Panning: DBAP (distance-based amplitude panning, Lossius et al. 2009), the
method used for installations with irregular speaker layouts and no single
sweet spot, which is what a car is. Each speaker's gain falls off with its
distance from the source (6 dB per doubling), with a "blur" radius so a
source never collapses onto one speaker, normalised for constant power.

It pans *within each frequency band's speakers*: in the 3-way layout only
the four PRO-X6BMs (front doors, cargo) play mids and highs, which is where
you hear direction, and the 2nd-row 8" mid-bass play the low-mids. Panning
each set separately keeps every band at a steady level and the low-mids
with the image. The subs are bass management: every stem is sent to them,
wherever it's placed; low bass has no direction.

Tuned against the CarPlay routing and a model of the driver's seat
(tools/stems_check.py):
  - Highs: constant power, compensated for the listening seat's distance
    to each DS18 (`seat`). The DSP time-aligns every speaker to the driver
    but has no level trims, so front L (0.66 m from the driver) otherwise
    drowns out rear R (2.02 m): stems placed beside or behind the driver
    were heard 90-180 deg off, usually in front. Full compensation modelled
    49 -> 14 deg average error but boosts the far side a lot; half
    (SEAT_COMPENSATION) is the compromise.
  - Per-output trims (`outputs`, the Audio page's Mixer tab): gain, polarity
    and mute for everything the stems send to one output, on top of the
    placement.
  - 8in mid-bass (80-300 Hz): the pair sums in phase at these wavelengths,
    so gains add to one (amplitude, not power); power panning played the
    low-mids 3 dB louder than CarPlay.
  - Every output at CarPlay's level for it (carplay_sends): the subs'
    +6 dB and whatever the Mixer tab sets per speaker (the DS18s at
    -12.5 dB made stems 12.5 dB hot there before this).

Stereo stems: the L and R channels sit either side of the stem's position,
`width` apart. `spread` enlarges the source (more blur = more diffuse).

Coordinates (metres, top view): x right, y forward, origin mid-cabin of a
1975 Cherokee SJ. Positions are approximate; the time alignment in the DSP
config (tuned for the driver's seat) is unchanged.
"""

import functools
import json
import math
import threading
import time
from pathlib import Path

STATE_FILE = Path.home() / ".config/jeep_computer/stems.json"

# name -> (x, y, CamillaDSP output, band)
SPEAKERS = {
    "front_l": (-0.72, 1.05, 0, "high"),
    "front_r": (0.72, 1.05, 1, "high"),
    "rear_l": (-0.65, -1.25, 2, "high"),
    "rear_r": (0.65, -1.25, 3, "high"),
    "mid_l": (-0.74, -0.15, 4, "mid"),
    "mid_r": (0.74, -0.15, 5, "mid"),
}
SUB_OUTPUTS = (6, 7)
SUB_POSITION = (0.0, -1.42)

STEMS = ["drums", "bass", "vocals", "other"]
STEM_LABELS = {"drums": "Drums", "bass": "Bass", "vocals": "Vocals", "other": "Other"}
STEM_COLORS = {"drums": "#ffb42e", "bass": "#a98bff", "vocals": "#2fd49a", "other": "#6cb4ff"}
CARPLAY_INPUTS = (0, 1)         # CamillaDSP inputs 1-2
FIRST_STEM_INPUT = 2            # CamillaDSP input index of drums L (inputs 3-10)
STEM_INPUTS = 8

# Cabin area the UI lets you place sources in
AREA = {"x": (-0.95, 0.95), "y": (-1.6, 1.45)}

# Listening seats (driver's = where the DSP's time alignment points).
# "everyone": no seat compensation.
SEATS = {"driver": (-0.32, 0.52), "passenger": (0.32, 0.52), "everyone": None}
# How much of each speaker's distance to the seat the gains make up:
# 1 = all of it in free field (6 dB per doubling), which boosted front R by
# 5 dB and rear R by 10 dB for the driver. In a car the reflections even out
# levels, so only the direct sound falls off that fast. 0.5 = 3 dB per
# doubling. (Front R sounded too loud; the main cause was the stems ignoring
# the Mixer tab's -12.5 dB on the DS18s, see carplay_sends.)
SEAT_COMPENSATION = 0.5

ROLLOFF_DB = 6.0                # per doubling of distance
BASE_BLUR_M = 0.18
MAX_EXTRA_BLUR_M = 1.1
MAX_HALF_WIDTH_M = 0.8


def default_stem(x, y, width=0.5, spread=0.3, level_db=0.0, sub=1.0):
    return {"x": x, "y": y, "width": width, "spread": spread, "level_db": level_db,
            "mute": False, "solo": False, "sub": sub}


PRESETS = {
    "stage": {
        "label": "Stage",
        "stems": {
            "drums": default_stem(0.0, 0.3, 0.6, 0.35),
            "bass": default_stem(0.38, 0.55, 0.2, 0.3),
            "vocals": default_stem(0.0, 0.98, 0.0, 0.15),
            "other": default_stem(-0.38, 0.62, 0.9, 0.35),
        },
    },
    "surround": {
        "label": "Surround",
        "stems": {
            "drums": default_stem(0.0, -0.9, 0.9, 0.45),
            "bass": default_stem(0.0, -0.1, 0.3, 0.6),
            "vocals": default_stem(0.0, 1.0, 0.0, 0.15),
            "other": default_stem(0.0, 0.1, 1.0, 0.6),
        },
    },
    "band": {
        "label": "In the band",
        "stems": {
            "drums": default_stem(-0.45, -0.95, 0.5, 0.3),
            "bass": default_stem(0.5, -0.9, 0.2, 0.3),
            "vocals": default_stem(-0.3, 0.95, 0.0, 0.15),
            "other": default_stem(0.5, 0.8, 0.5, 0.3),
        },
    },
    "wide": {
        "label": "Wide",
        "stems": {
            "drums": default_stem(0.0, 0.0, 1.0, 0.8),
            "bass": default_stem(0.0, 0.0, 0.6, 0.9),
            "vocals": default_stem(0.0, 0.6, 0.2, 0.4),
            "other": default_stem(0.0, 0.0, 1.0, 0.9),
        },
    },
}


def default_state():
    return {"preset": "stage", "master_db": 0.0, "carplay_mute": False, "stems_mute": False, "seat": "driver",
            "stems": json.loads(json.dumps(PRESETS["stage"]["stems"])),
            "outputs": {}}     # "dest" -> {"gain_db", "inverted", "mute"} (Audio page Mixer tab)


def output_trim(state, dest):
    trim = (state.get("outputs") or {}).get(str(dest)) or {}
    return {"gain_db": float(trim.get("gain_db", 0.0)), "inverted": bool(trim.get("inverted")),
            "mute": bool(trim.get("mute"))}


_live = {}


def live_state():
    """The dashboard's one copy of the stem state: the Music page (placement)
    and the Audio page (per-output trims) change the same dict, so neither
    pushes a stale copy over the other's changes."""
    if "state" not in _live:
        _live["state"] = load_state()
    return _live["state"]


def live_renderer(client_factory):
    """The dashboard's one StemRenderer (pushes are coalesced in one place)."""
    if "renderer" not in _live:
        _live["renderer"] = StemRenderer(client_factory)
    return _live["renderer"]


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text())
        base = default_state()
        base.update({k: v for k, v in state.items() if k not in ("stems", "outputs")})
        base["outputs"].update(state.get("outputs") or {})
        for name in STEMS:
            base["stems"][name].update((state.get("stems") or {}).get(name) or {})
        return base
    except (OSError, ValueError):
        return default_state()


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


# -------------------------------------------------------------
# DBAP
# -------------------------------------------------------------

def dbap(x, y, speakers, blur, seat=None, coherent=False):
    """{speaker: linear gain} for a point source over `speakers`. Constant
    power, or gains summing to one when the speakers add in phase
    (`coherent`, low frequencies). `seat`: (x, y) to compensate for the
    listener's distance to each speaker."""
    a = ROLLOFF_DB / (20 * math.log10(2))
    raw = {}
    for name in speakers:
        sx, sy = SPEAKERS[name][:2]
        d = math.sqrt((x - sx) ** 2 + (y - sy) ** 2 + blur ** 2)
        raw[name] = 1.0 / d ** a
        if seat is not None:
            raw[name] *= math.dist(seat, (sx, sy)) ** SEAT_COMPENSATION
    if coherent:
        k = 1.0 / sum(raw.values())
    else:
        k = 1.0 / math.sqrt(sum(g * g for g in raw.values()))
    return {name: g * k for name, g in raw.items()}


@functools.lru_cache(maxsize=None)
def seat_makeup(seat):
    """Gain that keeps the seat-compensated highs as loud at the seat as
    uncompensated ones, on average over the cabin (compensation turns the
    nearest speaker down, and that one dominates what the seat hears)."""
    listener = SEATS[seat]
    highs = band_sets()["high"]
    dist = {n: math.dist(listener, SPEAKERS[n][:2]) for n in highs}
    ratios = []
    for i in range(11):
        for j in range(11):
            x = AREA["x"][0] + (AREA["x"][1] - AREA["x"][0]) * i / 10
            y = AREA["y"][0] + (AREA["y"][1] - AREA["y"][0]) * j / 10
            heard = [sum((g / dist[n]) ** 2 for n, g in dbap(x, y, highs, BASE_BLUR_M, seat=s_).items())
                     for s_ in (None, listener)]
            ratios.append(heard[0] / heard[1])
    return math.sqrt(sum(ratios) / len(ratios))


def band_sets():
    bands = {}
    for name, (_, _, _, band) in SPEAKERS.items():
        bands.setdefault(band, []).append(name)
    return bands


def stem_gains(stem, seat="driver"):
    """
    {output: [(input offset 0/1 for L/R, linear gain)]} for one stem, before
    its level. Each channel is panned within each band's speaker set.
    """
    half = stem["width"] * MAX_HALF_WIDTH_M / 2
    blur = BASE_BLUR_M + stem["spread"] * MAX_EXTRA_BLUR_M
    listener = SEATS.get(seat, SEATS["driver"])
    out = {}
    for side, dx in ((0, -half), (1, half)):
        px = min(max(stem["x"] + dx, AREA["x"][0]), AREA["x"][1])
        for band, names in band_sets().items():
            if band == "high":
                gains = dbap(px, stem["y"], names, blur, seat=listener)
                makeup = seat_makeup(seat) if listener else 1.0
                gains = {name: g * makeup for name, g in gains.items()}
            else:
                gains = dbap(px, stem["y"], names, blur, coherent=True)
            for name, g in gains.items():
                out.setdefault(SPEAKERS[name][2], []).append((side, g))
    return out


def mixer_sources(state, sends=None):
    """
    Every stem input -> output gain, as {output: {input: linear gain}}.
    Includes zeros so the mixer's structure never changes (only values),
    which lets CamillaDSP apply updates without interrupting audio.
    `sends`: {output: CarPlay's linear gain into it} (carplay_sends), so
    the stems sit at the same level CarPlay is tuned to on every speaker.
    """
    stems = state["stems"]
    solo = any(s.get("solo") for s in stems.values())
    master = 0.0 if state.get("stems_mute") else 10 ** (state.get("master_db", 0.0) / 20)
    table = {out: {FIRST_STEM_INPUT + i: 0.0 for i in range(STEM_INPUTS)}
             for out in list(range(6)) + list(SUB_OUTPUTS)}

    for index, name in enumerate(STEMS):
        stem = stems[name]
        audible = not stem.get("mute") and (not solo or stem.get("solo"))
        if not audible:
            continue
        level = master * 10 ** (stem.get("level_db", 0.0) / 20)
        first = FIRST_STEM_INPUT + index * 2
        for out, pairs in stem_gains(stem, state.get("seat", "driver")).items():
            for side, g in pairs:
                table[out][first + side] += level * g
        for out in SUB_OUTPUTS:
            for side in (0, 1):
                table[out][first + side] = level * stem.get("sub", 1.0)

    for out, gains in table.items():
        trim = output_trim(state, out)
        factor = 0.0 if trim["mute"] else 10 ** (trim["gain_db"] / 20) * (sends or {}).get(out, 1.0)
        for ch in gains:
            gains[ch] *= factor
    return table


def carplay_sends(mixer):
    """{output: CarPlay's gain into it (linear)} from the mixer, so the stems
    follow however each speaker is tuned for CarPlay on the Audio page's
    Mixer tab (e.g. DS18s -12.5 dB, subs +6 dB). The loudest CarPlay
    source per output; its mute flag is ignored (CarPlay mute uses it)."""
    sends = {}
    for mapping in mixer.get("mapping", []):
        gains = []
        for src in mapping["sources"]:
            if src["channel"] in CARPLAY_INPUTS:
                gain = src.get("gain", 0.0)
                gains.append(gain if src.get("scale") == "linear" else 10 ** (gain / 20))
        if gains:
            sends[mapping["dest"]] = max(gains)
    return sends


def apply_to_config(config, state):
    """Write the stem gains into a CamillaDSP config dict (in place).
    Returns False if the config has no stem inputs."""
    mixer = (config.get("mixers") or {}).get("route_to_speakers")
    if not mixer or (mixer.get("channels") or {}).get("in", 0) < FIRST_STEM_INPUT + STEM_INPUTS:
        return False
    table = mixer_sources(state, carplay_sends(mixer))
    for mapping in mixer["mapping"]:
        # CarPlay mute: its sources in the mixer, so the stems play on. The
        # Mixer tab's own per-source mutes (carplay_source_mute) survive it.
        own_mutes = state.get("carplay_source_mute") or {}
        for src in mapping["sources"]:
            if src["channel"] in CARPLAY_INPUTS:
                own = bool(own_mutes.get(f"{mapping['dest']}:{src['channel']}"))
                src["mute"] = bool(state.get("carplay_mute")) or own
        gains = table.get(mapping["dest"])
        if gains is None:
            continue
        by_channel = {s["channel"]: s for s in mapping["sources"]}
        inverted = output_trim(state, mapping["dest"])["inverted"]
        for ch, g in gains.items():
            src = by_channel.get(ch)
            if src is None:
                src = {"channel": ch, "inverted": False, "mute": False}
                mapping["sources"].append(src)
            src["gain"] = round(g, 6)
            src["scale"] = "linear"
            src["inverted"] = inverted
    return True


# -------------------------------------------------------------
# Live link to CamillaDSP
# -------------------------------------------------------------

class StemRenderer:
    """
    Pushes stem placement to CamillaDSP, coalescing rapid changes (dragging)
    to a few updates a second on a background thread. Each push reads the
    current config first, so edits made on the Audio page aren't overwritten.
    """

    MIN_INTERVAL_S = 0.12

    def __init__(self, client_factory):
        self.client_factory = client_factory
        self.client = None
        self.pending = None
        self.lock = threading.Condition()
        self.status = "idle"         # idle | ok | no-stem-inputs | offline
        self.persist = False
        threading.Thread(target=self._run, name="stem-renderer", daemon=True).start()

    def push(self, state, persist=False):
        """Apply `state` live. persist=True also writes it into the active
        config file (so it survives a CamillaDSP restart); use on release."""
        with self.lock:
            self.pending = json.loads(json.dumps(state))
            self.persist = self.persist or persist
            self.lock.notify()

    def _run(self):
        last = 0.0
        while True:
            with self.lock:
                while self.pending is None:
                    self.lock.wait()
                wait = self.MIN_INTERVAL_S - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            with self.lock:
                state, self.pending = self.pending, None
                persist, self.persist = self.persist, False
            self._send(state, persist)
            last = time.monotonic()

    def _send(self, state, persist=False):
        try:
            if self.client is None:
                self.client = self.client_factory()
                self.client.connect()
            config = json.loads(self.client.call("GetConfigJson"))
            if not apply_to_config(config, state):
                self.status = "no-stem-inputs"
                return
            self.client.call("SetConfigJson", json.dumps(config))
            self.status = "ok"
            if persist:
                self._write_file(state)
        except Exception:
            self.client = None
            self.status = "offline"

    def _write_file(self, state):
        """Stem gains into the active config file (everything else untouched)."""
        import yaml
        path = self.client.call("GetConfigFilePath")
        if not path:
            return
        path = Path(path)
        config = yaml.safe_load(path.read_text())
        if apply_to_config(config, state):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(yaml.safe_dump(config, sort_keys=False, width=100))
            tmp.replace(path)
