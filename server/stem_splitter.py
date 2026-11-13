#!/usr/bin/env python3
"""
Jeep stem splitter: runs on the Linux server (GTX 1660 Super), not the Pi.

Watches an inbox for songs, splits each with Demucs on the GPU, and files
the four stems where the Jeep picks them up:

    ~/jeep-stems/inbox/      songs arrive here (Windows "Send to > Jeep Stems")
    ~/jeep-stems/done/<song>/drums.flac bass.flac vocals.flac other.flac
    ~/jeep-stems/processed/  originals after a successful split
    ~/jeep-stems/failed/     originals Demucs couldn't split (+ .log)

A song folder appears in done/ only once it's complete (built in a temp
folder, then renamed), so the Pi never pulls half a song.

Settings (environment, e.g. in the systemd unit):
    JEEP_STEMS_DIR    base folder          (default ~/jeep-stems)
    DEMUCS_MODEL      htdemucs_ft | htdemucs | ...   (default htdemucs_ft)
    DEMUCS_DEVICE     cuda | cpu            (default cuda)
    DEMUCS_SEGMENT    seconds per chunk; lower = less VRAM (default: model's)
    DEMUCS_PYTHON     python with demucs installed (default: this one)
    DEMUCS_DRY_RUN=1  don't run Demucs; write silent stems (for testing)

Run by hand:  python3 stem_splitter.py        (see docs/stem-server.md)
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(os.environ.get("JEEP_STEMS_DIR", Path.home() / "jeep-stems"))
INBOX, DONE, PROCESSED, FAILED, WORK = (BASE / d for d in ("inbox", "done", "processed", "failed", ".work"))
MODEL = os.environ.get("DEMUCS_MODEL", "htdemucs_ft")
DEVICE = os.environ.get("DEMUCS_DEVICE", "cuda")
SEGMENT = os.environ.get("DEMUCS_SEGMENT")
PYTHON = os.environ.get("DEMUCS_PYTHON", sys.executable)
DRY_RUN = os.environ.get("DEMUCS_DRY_RUN") == "1"

AUDIO = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aif", ".aiff"}
POLL_S = 5
SETTLE_S = 5          # file unchanged this long = finished uploading
STEMS = ("drums", "bass", "vocals", "other")


def log(msg):
    print(time.strftime("%F %T"), msg, flush=True)


def settled(path):
    """True once a file has stopped growing (an upload may still be running)."""
    try:
        st = path.stat()
    except OSError:
        return False
    return time.time() - st.st_mtime >= SETTLE_S and st.st_size > 0


def unique(folder, name):
    target = folder / name
    n = 2
    while target.exists():
        target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return target


def split(song):
    title = song.stem
    work = WORK / title
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    started = time.monotonic()

    if DRY_RUN:
        track = work / MODEL / title
        track.mkdir(parents=True)
        for stem in STEMS:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            "anullsrc=r=44100:cl=stereo", "-t", "2", str(track / f"{stem}.flac")], check=True)
        result = subprocess.CompletedProcess([], 0, "", "")
    else:
        cmd = [PYTHON, "-m", "demucs", "-n", MODEL, "-d", DEVICE, "--flac", "-o", str(work), str(song)]
        if SEGMENT:
            cmd[3:3] = ["--segment", SEGMENT]
        result = subprocess.run(cmd, capture_output=True, text=True)

    track = work / MODEL / title
    missing = [s for s in STEMS if not (track / f"{s}.flac").exists()]
    if result.returncode != 0 or missing:
        (FAILED / f"{song.name}.log").write_text(
            f"exit {result.returncode}, missing {missing}\n\n{result.stdout}\n{result.stderr}")
        shutil.move(str(song), str(unique(FAILED, song.name)))
        shutil.rmtree(work, ignore_errors=True)
        log(f"FAILED {song.name} (see failed/{song.name}.log)")
        return

    (track / "source.txt").write_text(f"{song.name}\nmodel {MODEL}\n")
    final = DONE / title
    if final.exists():
        shutil.rmtree(final)
    track.rename(final)                      # atomic: the Pi sees it complete or not at all
    shutil.rmtree(work, ignore_errors=True)
    shutil.move(str(song), str(unique(PROCESSED, song.name)))
    log(f"split {song.name} in {time.monotonic() - started:.0f} s -> done/{title}/")


def main():
    for d in (INBOX, DONE, PROCESSED, FAILED, WORK):
        d.mkdir(parents=True, exist_ok=True)
    log(f"watching {INBOX} (model {MODEL} on {DEVICE}{', DRY RUN' if DRY_RUN else ''})")
    once = "--once" in sys.argv
    while True:
        for song in sorted(INBOX.iterdir()):
            if song.is_file() and song.suffix.lower() in AUDIO and settled(song):
                split(song)
        if once:
            break
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
