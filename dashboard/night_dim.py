"""
Automatic night dimming from the TSL2591 light sensor.

The shell lays a black layer over the whole screen (CarPlay included) whose
opacity follows the cabin light level: 0 in daylight, up to `night_dim` in
the dark, blended on a log scale in between (the eye works in ratios, so
4 -> 60 lux is a big change and 1000 -> 1060 isn't). The reading is
smoothed over ~12 s so streetlights and overpasses don't make it flicker.

Settings (~/.config/jeep_computer/display.json, also on the Settings page):
    brightness  manual brightness 0.3..1.0 (the screen is never brighter
                than this; auto-dim can only darken it further)
    auto_dim    true / false
    night_dim   dimming in the dark, 0..0.8 (default 0.45)
    day_lux     at or above this: no dimming (default 60)
    night_lux   at or below this: full night_dim (default 4)
"""

import json
import math
from pathlib import Path

SETTINGS = Path.home() / ".config/jeep_computer/display.json"
DEFAULTS = {"brightness": 1.0, "auto_dim": True, "night_dim": 0.45, "day_lux": 60.0, "night_lux": 4.0}
MIN_BRIGHTNESS = 0.3

SMOOTHING = 0.08        # per reading (1 Hz): ~12 s time constant
MIN_STEP = 0.02         # only tell the shell about changes bigger than this


def load_settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(settings):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")


def dim_for(lux, s):
    """Dimming level for a (smoothed) light level."""
    day, night = math.log10(s["day_lux"] + 1), math.log10(s["night_lux"] + 1)
    x = (day - math.log10(max(lux, 0) + 1)) / max(day - night, 1e-6)
    return s["night_dim"] * max(0.0, min(1.0, x))


class NightDimmer:

    def __init__(self, send):
        self.send = send                # set_dim(level) -> reply or None
        self.settings = load_settings()
        self.smoothed = None            # log10(lux + 1)
        self.sent = None
        self.lux = None
        self.level = 0.0

    # --- Settings page -------------------------------------

    def set_brightness(self, brightness):
        self.settings["brightness"] = max(MIN_BRIGHTNESS, min(1.0, float(brightness)))
        save_settings(self.settings)
        self.apply(force=True)

    def set_auto(self, on):
        self.settings["auto_dim"] = bool(on)
        save_settings(self.settings)
        self.apply(force=True)

    # --- Light sensor --------------------------------------

    def update(self, lux):
        self.lux = lux
        if lux is not None:
            value = math.log10(max(lux, 0) + 1)
            self.smoothed = value if self.smoothed is None else (
                self.smoothed + SMOOTHING * (value - self.smoothed))
        self.apply()

    def target(self):
        s = self.settings
        manual = 1.0 - max(MIN_BRIGHTNESS, min(1.0, s["brightness"]))
        auto = 0.0
        if s["auto_dim"] and self.smoothed is not None:
            auto = dim_for(10 ** self.smoothed - 1, s)
        # Darkest of the two: manual brightness is a ceiling
        return max(manual, auto)

    def apply(self, force=False):
        target = self.target()
        self.level = target
        if (force or self.sent is None or abs(target - self.sent) >= MIN_STEP
                or (target == 0 and self.sent != 0)):
            if self.send(target) is not None:
                self.sent = target
