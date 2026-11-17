"""
Fuel level filter for the gauge sender (sensors/senders.py convert("fuel")).

What the raw sender does (measured, engine.db):
  - After every key-on it climbs from ~0 % to the real level over about a minute
    (sender and gauge supply warming up).
  - Sloshing moves it +-10 %, with single outliers at 0 and 100 %.
The tank only ever empties, apart from a fill-up, so:
  1. After a gap in the readings (key off) the filter holds the last known level
     for WARMUP_S, then takes the median of the last 30 s of raw readings.
     That value is kept if it is lower than the last known level; a higher one is
     taken only if it is at least REFILL_PCT higher (a fill-up with the key off);
     a smaller rise is slosh and is ignored.
  2. While driving the level can only fall: it follows the median of the last
     MEDIAN_S, falling no faster than MAX_FALL_PCT_PER_MIN.
  3. A rise of REFILL_PCT or more that lasts RISE_CONFIRM_S is a fill-up with the key on.
The last known level is saved in ~/.local/share/jeep_computer/fuel_state.json.
"""

import json
import os
import statistics
import time
from collections import deque
from pathlib import Path

STATE_FILE = Path.home() / ".local/share/jeep_computer/fuel_state.json"

GAP_S = 20             # no valid reading for this long = key was off, warm-up starts again
WARMUP_S = 120         # hold the last known level this long after a gap
SETTLE_S = 30          # at the end of the warm-up use the median of the last SETTLE_S
MEDIAN_S = 120         # steady-state median window
REFILL_PCT = 8         # a rise this big is a fill-up
RISE_CONFIRM_S = 180   # a rise must last this long to count when the key stayed on
MAX_FALL_PCT_PER_MIN = 1.0   # the tank empties ~0.4 %/min at the most
SAVE_EVERY_S = 60
SAVE_MIN_CHANGE = 0.5


class FuelFilter:
    def __init__(self, state_file=STATE_FILE, clock=time.time):
        self.state_file = state_file
        self.clock = clock
        self.window = deque()          # (t, raw %)
        self.last_valid = None
        self.warm_start = None
        self.est = None                # filtered level once the warm-up is over
        self.last_t = None
        self.rise_since = None
        self.saved = None
        self.saved_t = 0.0
        self.last_known = self._load()

    def _load(self):
        try:
            v = float(json.loads(Path(self.state_file).read_text())["fuel_pct"])
            return v if 0 <= v <= 100 else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save(self, now):
        if self.est is None:
            return
        if (self.saved is not None and abs(self.est - self.saved) < SAVE_MIN_CHANGE) or now - self.saved_t < SAVE_EVERY_S:
            return
        try:
            p = Path(self.state_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"fuel_pct": round(self.est, 1), "ts": int(now)}))
            os.replace(tmp, p)
            self.saved, self.saved_t, self.last_known = self.est, now, self.est
        except OSError:
            pass

    def _median(self, since):
        vals = [v for t, v in self.window if t >= since]
        return statistics.median(vals) if vals else None

    def update(self, raw, now=None):
        """Feed one raw reading (percent, or None when the sender is unpowered). Returns the filtered %."""
        now = self.clock() if now is None else now
        if raw is None:
            return None
        if self.last_valid is None or now - self.last_valid > GAP_S:
            self.window.clear()
            self.warm_start = now
            if self.est is not None:
                self.last_known = self.est
            self.est = None
            self.last_t = None
            self.rise_since = None
        self.last_valid = now
        self.window.append((now, raw))
        while self.window and self.window[0][0] < now - MEDIAN_S:
            self.window.popleft()

        if self.est is None:
            if now - self.warm_start < WARMUP_S:
                return None if self.last_known is None else round(self.last_known, 1)
            base = self._median(now - SETTLE_S)
            if self.last_known is None or base < self.last_known or base - self.last_known >= REFILL_PCT:
                self.est = base
            else:
                self.est = self.last_known
            self.last_t = now
            self._save(now)
            return round(self.est, 1)

        dt = max(0.0, now - (self.last_t or now))
        self.last_t = now
        m = self._median(now - MEDIAN_S)
        if m < self.est:
            self.est = max(m, self.est - MAX_FALL_PCT_PER_MIN * dt / 60.0)
            self.rise_since = None
        elif m - self.est >= REFILL_PCT:
            if self.rise_since is None:
                self.rise_since = now
            elif now - self.rise_since >= RISE_CONFIRM_S:
                self.est = m
                self.rise_since = None
        else:
            self.rise_since = None
        self._save(now)
        return round(self.est, 1)
