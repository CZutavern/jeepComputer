"""
Speaker test: which physical speaker is each CamillaDSP output?

For output N (1-8): N short beeps, then 1.5 s of pink noise, on that output
only. Listen for where it comes from:
    1 front L   2 front R   3 rear L   4 rear R   5 8" L   6 8" R   7-8 subs
If they come out of the wrong speakers, the DAC's channels are out of step:
the amp controller's DAC guard reopens the DAC after underruns; you can
force it now with --resync.

It temporarily routes the test signal (stems input 3, "drums L") straight
to one output via CamillaDSP's mixer, then restores the mixer. Crossovers,
time alignment and the limiter stay in (so the subs only get the low end),
and it plays at your current volume.

Run:  .venv/bin/python tools/speaker_test.py [--resync] [--no-beeps] [outputs, e.g. 1 2 7]
(Also on the dashboard: Settings > Speaker check.)
"""

import json
import math
import random
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from audio.camilla_client import CamillaClient  # noqa: E402

RATE = 48000
TEST_INPUT = 2          # CamillaDSP input 3 = stems channel AUX0
NAMES = {1: "front L", 2: "front R", 3: "rear L", 4: "rear R", 5: "8in L", 6: "8in R", 7: "sub 1", 8: "sub 2"}


def signal(n_beeps):
    """8-channel float32 frames: beeps then pink noise, on channel 0 only."""
    frames = []
    for _ in range(n_beeps):
        frames += [0.3 * math.sin(2 * math.pi * 1000 * i / RATE) for i in range(int(0.12 * RATE))]
        frames += [0.0] * int(0.18 * RATE)
    frames += [0.0] * int(0.3 * RATE)
    b = [0.0] * 3                                   # Paul Kellet's economy pink filter
    for _ in range(int(1.5 * RATE)):
        w = random.uniform(-1, 1)
        b[0] = 0.99765 * b[0] + w * 0.0990460
        b[1] = 0.96300 * b[1] + w * 0.2965164
        b[2] = 0.57000 * b[2] + w * 1.0526913
        frames.append(0.12 * (b[0] + b[1] + b[2] + w * 0.1848))
    frames += [0.0] * int(0.4 * RATE)
    out = bytearray()
    for v in frames:
        out += struct.pack("<8f", v, 0, 0, 0, 0, 0, 0, 0)
    return bytes(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    outputs = [int(a) for a in args] or list(range(1, 9))
    c = CamillaClient()
    c.connect()

    if "--resync" in sys.argv:
        config = json.loads(c.call("GetConfigJson"))
        level = config["devices"]["target_level"]
        config["devices"]["target_level"] = level + 1 if level % 2 == 0 else level - 1
        c.call("SetConfigJson", json.dumps(config))
        print("DAC reopened")
        time.sleep(2.5)

    original = c.call("GetConfigJson")
    try:
        for out in outputs:
            config = json.loads(original)
            for mapping in config["mixers"]["route_to_speakers"]["mapping"]:
                for src in mapping["sources"]:
                    src["mute"] = True
                if mapping["dest"] == out - 1:
                    test = next((src for src in mapping["sources"] if src["channel"] == TEST_INPUT), None)
                    if test is None:
                        test = {"channel": TEST_INPUT, "inverted": False}
                        mapping["sources"].append(test)
                    test.update(gain=0.0, scale="dB", mute=False)
            c.call("SetConfigJson", json.dumps(config))
            beeps = 0 if "--no-beeps" in sys.argv else out
            print(f"Output {out} ({NAMES[out]}): {beeps} beeps, then noise", flush=True)
            subprocess.run(["pw-cat", "--playback", "--raw", "--target", "jeep_stems", "--rate", str(RATE),
                            "--channels", "8", "--format", "f32",
                            "--channel-map", ",".join(f"AUX{i}" for i in range(8)), "-"],
                           input=signal(beeps), check=False)
    finally:
        c.call("SetConfigJson", original)
        print("Mixer restored")


if __name__ == "__main__":
    main()
