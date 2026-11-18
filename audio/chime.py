"""
Key-on / engine-off chime (dashboard/pages/home.py -> audio/chime.py).

The chime goes into CamillaDSP like CarPlay audio does (PipeWire target "camilladsp-capture"), and is
played at a FIXED level: the CamillaDSP start-up volume (-40 dB, the "-g-40" in camilladsp.service) plus
10 dB = -30 dB, whatever the music volume is. To do that the main volume is set to -30 dB for the length of
the chime and put back afterwards (and un-muted if it was muted). Anything else playing at that moment is
heard at that level for the ~2 s too.
"""

import math
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path

from audio.camilla_client import CamillaClient

DEFAULT_VOLUME_DB = -40.0     # camilladsp -g-40 (systemd drop-in), the volume it starts at
BOOST_DB = 10.0               # chime at the default volume + 10 dB
CHIME_DB = DEFAULT_VOLUME_DB + BOOST_DB
TARGET = "camilladsp-capture"
WAV = Path.home() / ".cache/jeep_computer/chime.wav"
RATE = 48000

_lock = threading.Lock()


def make_wav(path=WAV):
    """Two falling bell-like notes (880 Hz, 659 Hz), about 1.8 s, stereo 16-bit, peak 0.8 of full scale."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for start, freq, length in ((0.0, 880.0, 0.9), (0.55, 659.25, 1.25)):
        n = int(length * RATE)
        for i in range(n):
            t = i / RATE
            env = min(1.0, t / 0.01) * math.exp(-3.2 * t)
            v = env * (math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(2 * math.pi * 2 * freq * t))
            idx = int(start * RATE) + i
            while len(frames) <= idx:
                frames.append(0.0)
            frames[idx] += v
    peak = max(abs(x) for x in frames) or 1.0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        data = bytearray()
        for x in frames:
            s = int(0.8 * x / peak * 32767)
            data += struct.pack("<hh", s, s)
        w.writeframes(bytes(data))
    return path


def play(wav=WAV):
    """Play the chime once at CHIME_DB. Blocks about 2 s; call it from a thread."""
    if not _lock.acquire(blocking=False):
        return False                      # one at a time
    camilla = CamillaClient(timeout=1.0, log_errors=False)
    old_volume = old_mute = None
    try:
        if not Path(wav).exists():
            make_wav(Path(wav))
        if camilla.connect():
            old_volume = camilla.get_volume()
            old_mute = camilla.get_mute()
            if old_mute:
                camilla.call("SetMute", False)
            camilla.set_volume(CHIME_DB)
        subprocess.run(["pw-play", "--target", TARGET, str(wav)], timeout=8, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.3)
        return True
    except Exception as e:
        print("chime failed:", e, flush=True)
        return False
    finally:
        try:
            if old_volume is not None:
                camilla.set_volume(old_volume)
            if old_mute:
                camilla.call("SetMute", True)
        except Exception as e:
            print("chime: could not restore the volume:", e, flush=True)
        camilla.close()
        _lock.release()


def play_async():
    threading.Thread(target=play, name="chime", daemon=True).start()
