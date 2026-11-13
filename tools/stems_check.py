"""
Does stem placement land where you put it? Checks audio/stems.py two ways.

1. Direction (no audio needed): for a grid of puck positions, the direction
   the DS18s' sound energy comes from at a seat (Gerzon energy vector:
   received energy = gain^2 / distance^2; the DSP time-aligns every speaker
   to the driver, so levels decide direction) vs the direction of the puck.
   Prints the error map (degrees, + = heard right of the puck) and the mean.

2. Balance (needs numpy, and songs in ~/Music/Stems): renders songs through
   the stems mixer and through the CarPlay routing (L -> front L, rear L,
   8in L; R likewise; subs from the config) and compares the level per band
   at the driver's seat, using the DSP's LR4 80 / 300 Hz crossovers. The
   8in pair and the subs sum in phase at their frequencies, the DS18s in
   power. Near 0 dB = placing stems doesn't change the car's tuning.

Run:  python3 tools/stems_check.py [--balance]
(/usr/bin/python3 has numpy; the dashboard's venv doesn't.)
"""

import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from audio import stems as S  # noqa: E402

HIGHS = S.band_sets()["high"]
BY_OUTPUT = {v[2]: k for k, v in S.SPEAKERS.items()}
CARPLAY_SUB_DB = 6.0         # route_to_speakers: CarPlay L/R -> subs


def high_gains(x, y, seat):
    """{speaker: gain} the mixer gives the DS18s for a narrow source."""
    stem = S.default_stem(x, y, width=0.0, spread=0.0)
    return {BY_OUTPUT[o]: sum(g for side, g in pairs if side == 0)
            for o, pairs in S.stem_gains(stem, seat).items() if BY_OUTPUT.get(o) in HIGHS}


def heard_direction(gains, listener):
    ex = ey = 0.0
    for name, g in gains.items():
        sx, sy = S.SPEAKERS[name][:2]
        r = math.dist(listener, (sx, sy))
        e = g * g / (r * r)
        ex += e * (sx - listener[0]) / r
        ey += e * (sy - listener[1]) / r
    return math.atan2(ex, ey)


def direction_errors(seat, listener, show=False):
    errors, rows = [], []
    for yi in range(145, -161, -15):
        row = f"y={yi / 100:5.2f} "
        for xi in range(-95, 96, 19):
            x, y = xi / 100, yi / 100
            if math.dist((x, y), listener) < 0.45:        # on the listener's head
                row += "   ·"
                continue
            heard = heard_direction(high_gains(x, y, seat), listener)
            target = math.atan2(x - listener[0], y - listener[1])
            err = math.degrees((heard - target + math.pi) % (2 * math.pi) - math.pi)
            errors.append(abs(err))
            row += f"{err:4.0f}"
        rows.append(row)
    if show:
        print("\n".join(rows))
    errors.sort()
    return sum(errors) / len(errors), errors[int(len(errors) * 0.9)]


def check_directions():
    driver, passenger = S.SEATS["driver"], S.SEATS["passenger"]
    print("Heard direction vs puck, driver's seat, tuned for the driver "
          "(x from -0.95 to 0.95 m, left to right; front at the top):")
    mean, p90 = direction_errors("driver", driver, show=True)
    print(f"  mean error {mean:.0f} deg, 90% under {p90:.0f} deg")
    for seat, listener, label in (("everyone", driver, "no seat compensation, driver"),
                                  ("driver", passenger, "tuned for the driver, passenger"),
                                  ("passenger", passenger, "tuned for the passenger, passenger")):
        mean, p90 = direction_errors(seat, listener)
        print(f"  {label:36s} mean {mean:3.0f} deg, 90% under {p90:3.0f} deg")


# ------------------------------------------------------------- balance

def check_balance(songs=3, seconds=8):
    import numpy as np

    rate = 48000
    driver = S.SEATS["driver"]
    found = sorted({p.parent for p in (Path.home() / "Music/Stems").rglob("vocals.*")
                    if all(list(p.parent.glob(f"{n}.*")) for n in S.STEMS)})[:songs]
    if not found:
        print("No stem songs in ~/Music/Stems")
        return

    def load(folder, name, start):
        path = next(folder.glob(f"{name}.*"))
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(start), "-t", str(seconds), "-i", str(path),
                              "-f", "f32le", "-ac", "2", "-ar", str(rate), "-"], capture_output=True).stdout
        return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).T.astype(np.float64)

    def lr4(f, fc, highpass):
        k = (f / fc) ** 4
        return k / (1 + k) if highpass else 1 / (1 + k)

    def at_seat(inputs, table, f):
        dist = {S.SPEAKERS[n][2]: math.dist(driver, S.SPEAKERS[n][:2]) for n in S.SPEAKERS}
        dist.update({o: math.dist(driver, S.SUB_POSITION) for o in S.SUB_OUTPUTS})
        spec = {}
        for out, gains in table.items():
            sig = sum(g * inputs[i] for i, g in gains.items() if g)
            spec[out] = np.fft.rfft(sig) / dist[out] if not np.isscalar(sig) else np.zeros(len(f))
        energy = lambda s, h: float(np.sum(np.abs(s * h) ** 2))  # noqa: E731
        return {"highs": sum(energy(spec[o], lr4(f, 300, True)) for o in (0, 1, 2, 3)),
                "8in": energy(spec[4] + spec[5], lr4(f, 80, True) * lr4(f, 300, False)),
                "subs": energy(spec[6] + spec[7], lr4(f, 80, False))}

    sub = 10 ** (CARPLAY_SUB_DB / 20)
    carplay = {o: {} for o in range(8)}
    for out, ch in ((0, 0), (1, 1), (2, 0), (3, 1), (4, 0), (5, 1)):
        carplay[out][ch] = 1.0
    for out in S.SUB_OUTPUTS:
        carplay[out] = {0: sub, 1: sub}

    print(f"\nStems vs CarPlay, level per band at the driver's seat ({len(found)} songs, {seconds} s each):")
    for preset in S.PRESETS:
        state = S.default_state()
        state["stems"] = json.loads(json.dumps(S.PRESETS[preset]["stems"]))
        totals = {}
        for folder in found:
            stems = [load(folder, name, 30) for name in S.STEMS]
            n = min(s.shape[1] for s in stems)
            stems = [s[:, :n] for s in stems]
            mix = sum(stems)
            inputs = [mix[0], mix[1]] + [ch for s in stems for ch in s]
            f = np.fft.rfftfreq(n, 1 / rate)
            a, b = at_seat(inputs, S.mixer_sources(state, {out: sub for out in S.SUB_OUTPUTS}), f), \
                at_seat(inputs, carplay, f)
            for band in a:
                totals.setdefault(band, []).append(10 * math.log10(a[band] / b[band]))
        print(f"  {S.PRESETS[preset]['label']:12s}" +
              "  ".join(f"{band} {sum(v) / len(v):+5.1f} dB" for band, v in totals.items()))


if __name__ == "__main__":
    check_directions()
    if "--balance" in sys.argv:
        check_balance()
