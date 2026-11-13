"""
Build a lower-latency copy of LIVI.

LIVI asks the Carlinkit dongle to buffer 1000 ms of media audio
(`mediaDelay` in the dongle's box-settings message, hard-coded in the app).
That second dominates CarPlay audio latency. This extracts LIVI.AppImage into
~/LIVI/LIVI-lowlatency and changes that one constant to DELAY_MS. The number
has to stay 3 characters so no offsets in app.asar move.

The dashboard runs this copy when it exists (main.py CARPLAY_*). The original
AppImage is untouched: delete ~/LIVI/LIVI-lowlatency to go back. Re-run this
after LIVI updates itself (the copy doesn't update).

Tested with the Carlinkit dongle here: 500 ms works; at 300 ms the dongle
seems to reject the whole settings message and falls back to its defaults
(CarPlay at 1280x768 instead of the widescreen 1920x928).

It can also shorten LIVI's own playback buffer (--short-sink): GStreamer
pulsesink buffer-time 300 -> 200 ms, latency-time 30 -> 20 ms.

2026-09-24: on the first drive with 500 ms + the short sink, phone audio
dropped out 10-20% of the time while video and touch were fine: the
dongle's buffer is what rides out Wi-Fi jitter between the phone and the
dongle, and a moving car has far more of it than a desk. So the default is
back to the stock 1000 ms and the stock sink; the copy is kept because it
starts faster than the AppImage and the dashboard runs it (remote
debugging for LIVI's settings). Lower it only with a drive to prove it.

Usage:  python3 tools/livi_lowlatency.py [delay_ms] [--short-sink]
        (delay 100-999 or 1000, default 1000 = stock)
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APPIMAGE = Path.home() / "LIVI/LIVI.AppImage"
OUT = Path.home() / "LIVI/LIVI-lowlatency"
DEFAULT_DELAY_MS = 1000     # stock; 500 dropped audio on the road, 300 lost widescreen
AUDIO_SINK_OLD = b"buffer-time=300000`,`latency-time=30000`"
AUDIO_SINK_NEW = b"buffer-time=200000`,`latency-time=20000`"


def patch_audio_sink(asar):
    """LIVI's pulsesink buffer 300 -> 200 ms. Returns True if patched."""
    data = asar.read_bytes()
    if data.count(AUDIO_SINK_OLD) != 1:
        print("audio sink settings not found as expected; left as is")
        return False
    assert len(AUDIO_SINK_OLD) == len(AUDIO_SINK_NEW)
    asar.write_bytes(data.replace(AUDIO_SINK_OLD, AUDIO_SINK_NEW))
    return True


def patch_asar(asar, delay_ms):
    data = bytearray(asar.read_bytes())

    uses = list(re.finditer(rb"mediaDelay:([A-Za-z_$][\w$]*)", data))
    if len(uses) != 1:
        sys.exit(f"expected one mediaDelay use in app.asar, found {len(uses)}; not patching")
    name = uses[0].group(1)
    use_at = uses[0].start()

    # The constant's definition closest before its use: ,Ua=1e3,
    pattern = rb"[,;\s]" + re.escape(name) + rb"=(\d+(?:e\d+)?)[,;]"
    defs = [m for m in re.finditer(pattern, data[:use_at])]
    if not defs:
        sys.exit(f"definition of {name.decode()} not found; not patching")
    match = defs[-1]
    old = match.group(1)
    old_ms = float(old)
    new = b"1e3" if delay_ms == 1000 else str(delay_ms).encode()
    if len(new) != len(old):
        sys.exit(f"{old.decode()} -> {new.decode()} changes length; pick 100-999 or 1000 ms")

    data[match.start(1):match.end(1)] = new
    asar.write_bytes(bytes(data))
    return old_ms


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    delay_ms = int(args[0]) if args else DEFAULT_DELAY_MS
    if not (100 <= delay_ms <= 999 or delay_ms == 1000):
        sys.exit("delay must be 100-999 or 1000 ms")
    if not APPIMAGE.exists():
        sys.exit(f"{APPIMAGE} not found")

    with tempfile.TemporaryDirectory(dir=OUT.parent) as tmp:
        print("Extracting LIVI.AppImage...", flush=True)
        subprocess.run([str(APPIMAGE), "--appimage-extract"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL)
        root = Path(tmp) / "squashfs-root"

        old_ms = patch_asar(root / "resources/app.asar", delay_ms)
        sink = "--short-sink" in sys.argv and patch_audio_sink(root / "resources/app.asar")
        (root / "JEEP-PATCH.txt").write_text(
            f"mediaDelay {old_ms:.0f} ms -> {delay_ms} ms (tools/livi_lowlatency.py)\n"
            + ("pulsesink buffer 300 -> 200 ms\n" if sink else "")
            + f"source: {APPIMAGE}\n")

        if OUT.exists():
            shutil.rmtree(OUT)
        root.rename(OUT)

    print(f"LIVI dongle audio buffer {old_ms:.0f} ms -> {delay_ms} ms: {OUT}")


if __name__ == "__main__":
    main()
