"""
Plays a song's four stems in sync into the jeep_stems sink (8 channels:
drums, bass, vocals, other; each L/R), where the stem mixer places them.

Songs (under ~/Music/Stems, searched a few folders deep):
  - a folder holding drums.*, bass.*, vocals.*, other.* (any audio format;
    the layout Demucs and most stem-separation tools write), or
  - a Native Instruments stem file, *.stem.mp4 / *.stem.m4a (streams:
    mix, drums, bass, other, vocals).

Library: artist and album come from the folders, so lay songs out as
Artist/Album/Song/ (folders in between, e.g. Artist/Albums/Album/Song/,
are fine). Artist/Song/ files under "Singles"; songs straight in
~/Music/Stems under "Unsorted". Sorted naturally ("2." before "10."), so
number prefixes set the order; clean_name() hides them for display.
Folders ending in ".part" are still being copied and are skipped.

ffmpeg decodes, resamples to 48 kHz and merges the four stereo stems into
one 8-channel stream (so they stay sample-aligned); pw-cat plays it. Pause
stops pw-cat (ffmpeg then blocks on the full pipe); seek restarts at the
new position.
"""

import os
import re
import signal
import subprocess
import time
from pathlib import Path

STEMS_DIR = Path.home() / "Music/Stems"
SINK = "jeep_stems"
STEM_ORDER = ["drums", "bass", "vocals", "other"]
AUDIO_EXT = {".wav", ".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".aif", ".aiff"}
# NI stem file stream index for each stem (0 is the full mix)
NI_STREAM = {"drums": 1, "bass": 2, "other": 3, "vocals": 4}


TRACK_PREFIX = re.compile(r"^\s*\d{1,3}\s*[.\-_)]\s*")


def clean_name(name):
    """'08. Peachy' -> 'Peachy', '01. GD Vibes (2014)' -> 'GD Vibes (2014)'."""
    return TRACK_PREFIX.sub("", name) or name


def natural_key(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


class Song:
    def __init__(self, title, kind, files, parts=()):
        self.title = title
        self.kind = kind            # "folder" or "ni"
        self.files = files          # folder: {stem: path}; ni: {"file": path}
        self._duration = None
        # parts: folders from the library root down to the song (inclusive)
        self.artist = parts[0] if len(parts) >= 2 else "Unsorted"
        self.album = parts[-2] if len(parts) >= 3 else ("Singles" if len(parts) == 2 else "Songs")
        self.key = str(next(iter(files.values())).parent)

    @property
    def name(self):
        return clean_name(self.title)

    def sort_key(self):
        return natural_key(self.artist), natural_key(self.album), natural_key(self.title)

    @property
    def duration(self):
        if self._duration is None:
            first = next(iter(self.files.values()))
            try:
                out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                      "-of", "default=nw=1:nk=1", str(first)],
                                     capture_output=True, text=True, timeout=10).stdout
                self._duration = float(out.strip())
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self._duration = 0.0
        return self._duration


def find_songs(root=STEMS_DIR, depth=4):
    songs = []
    root.mkdir(parents=True, exist_ok=True)

    def walk(folder, level):
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            return
        stems = {}
        for entry in entries:
            if entry.is_file() and entry.suffix.lower() in AUDIO_EXT:
                stem = entry.stem.lower()
                for name in STEM_ORDER:
                    if stem == name or stem.endswith(("_" + name, "-" + name, " " + name)):
                        stems[name] = entry
            if entry.is_file() and entry.name.lower().endswith((".stem.mp4", ".stem.m4a")):
                title = entry.name.rsplit(".stem.", 1)[0]
                parts = entry.parent.relative_to(root).parts + (title,)
                songs.append(Song(title, "ni", {"file": entry}, parts))
        if len(stems) == 4 and folder != root:
            songs.append(Song(folder.name, "folder", stems, folder.relative_to(root).parts))
        if level < depth:
            for entry in entries:
                if entry.is_dir() and not entry.name.startswith(".") and not entry.name.endswith(".part"):
                    walk(entry, level + 1)

    walk(root, 0)
    songs.sort(key=Song.sort_key)
    return songs


def _command(song, start):
    ss = ["-ss", f"{start:.2f}"] if start > 0 else []
    fmt = "aresample=48000,aformat=sample_fmts=flt:channel_layouts=stereo"
    if song.kind == "folder":
        inputs = []
        for name in STEM_ORDER:
            inputs += ss + ["-i", str(song.files[name])]
        chains = [f"[{i}:a]{fmt}[s{i}]" for i in range(4)]
    else:
        inputs = ss + ["-i", str(song.files["file"])]
        chains = [f"[0:a:{NI_STREAM[name]}]{fmt}[s{i}]" for i, name in enumerate(STEM_ORDER)]
    # join with an explicit map (amerge mislabels four "stereo" inputs):
    # 7.1 order FL FR FC LFE BL BR SL SR = drums L/R, bass L/R, vocals L/R, other L/R
    join = ("[s0][s1][s2][s3]join=inputs=4:channel_layout=7.1:"
            "map=0.FL-FL|0.FR-FR|1.FL-FC|1.FR-LFE|2.FL-BL|2.FR-BR|3.FL-SL|3.FR-SR[m]")
    graph = ";".join(chains) + ";" + join
    ffmpeg = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *inputs,
              "-filter_complex", graph, "-map", "[m]", "-f", "f32le", "-"]
    pwcat = ["pw-cat", "--playback", "--raw", "--target", SINK, "--rate", "48000", "--channels", "8",
             "--format", "f32", "--channel-map", ",".join(f"AUX{i}" for i in range(8)),  # PipeWire names (uppercase)
             "--latency", "60ms", "-"]
    return ffmpeg, pwcat


class StemPlayer:
    """play(song) / pause() / resume() / stop() / seek(seconds); poll() for position."""

    def __init__(self):
        self.stop_leftovers()
        self.song = None
        self.ffmpeg = None
        self.pwcat = None
        self.offset = 0.0          # position at the last (re)start
        self.started = None        # monotonic time of the last (re)start / resume
        self.paused_at = None      # position while paused
        self.state = "stopped"     # stopped | playing | paused

    @staticmethod
    def stop_leftovers():
        """A song still playing from before a dashboard restart (the player's
        processes run in their own session, so they outlive it) would carry
        on with nothing to stop it. Ends it; ffmpeg then exits on the closed
        pipe. Only pw-cat started by this player (its exact latency setting)."""
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                args = (proc / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            if args and args[0].endswith(b"pw-cat") and b"--target" in args and SINK.encode() in args \
                    and b"--latency" in args and b"60ms" in args:
                try:
                    os.kill(int(proc.name), signal.SIGCONT)
                    os.kill(int(proc.name), signal.SIGTERM)
                except OSError:
                    pass

    def play(self, song, start=0.0):
        self.stop()
        self.song = song
        ffmpeg_cmd, pwcat_cmd = _command(song, start)
        # One session per song so stop() can end both processes together
        self.ffmpeg = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
        self.pwcat = subprocess.Popen(pwcat_cmd, stdin=self.ffmpeg.stdout, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
        self.ffmpeg.stdout.close()
        self.offset, self.started, self.paused_at = start, time.monotonic(), None
        self.state = "playing"

    def _signal(self, sig):
        for proc in (self.pwcat, self.ffmpeg):
            if proc is not None and proc.poll() is None:
                try:
                    os.kill(proc.pid, sig)
                except OSError:
                    pass

    def pause(self):
        if self.state == "playing":
            self.paused_at = self.position()
            self._signal(signal.SIGSTOP)
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self._signal(signal.SIGCONT)
            self.offset, self.started, self.paused_at = self.paused_at, time.monotonic(), None
            self.state = "playing"

    def toggle(self):
        self.pause() if self.state == "playing" else self.resume()

    def stop(self):
        self._signal(signal.SIGCONT)
        self._signal(signal.SIGTERM)
        for proc in (self.pwcat, self.ffmpeg):
            if proc is not None:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.ffmpeg = self.pwcat = None
        self.state = "stopped"

    def seek(self, seconds):
        if self.song is not None:
            was_paused = self.state == "paused"
            self.play(self.song, max(0.0, min(seconds, max(0.0, self.song.duration - 1))))
            if was_paused:
                self.pause()

    def position(self):
        if self.state == "paused":
            return self.paused_at
        if self.state == "playing":
            return self.offset + (time.monotonic() - self.started)
        return 0.0

    def poll(self):
        """Call regularly: returns True once the song has ended by itself."""
        if self.state == "playing" and self.pwcat is not None and self.pwcat.poll() is not None:
            self.stop()
            return True
        return False
