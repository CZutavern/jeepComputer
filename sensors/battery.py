"""
Starting-battery voltage from ADS1115 input A3.

A3 sits on a divider (R10 100k / R11 22k): A3 volts = battery volts * 0.18033.
The ADS1115 runs at +/-4.096 V, so about 22 V is the top of the range
(12.6 V reads 2.27 V, 14.7 V reads 2.65 V).

Settings (~/.config/jeep_computer/battery.json):
    divider   A3 volts per battery volt (0.18033 for 100k / 22k)
    scale     multiplier from the multimeter calibration
              (tools/calibrate_start_battery.py)
    offset_v  fixed offset in volts, added last
"""

import json
from pathlib import Path

SETTINGS = Path.home() / ".config/jeep_computer/battery.json"
DEFAULTS = {"divider": 0.18033, "scale": 1.0, "offset_v": 0.0}

# Below this the divider is not connected to a battery (a floating input
# reads ~0), or above MAX_V; report no voltage rather than a bogus alarm.
NOT_CONNECTED_V = 1.0
MAX_V = 18.0                 # no 12 V system charges above ~16 V: higher means a floating or
                             # miswired A3 (seen 2026-10-11: 3.5 V on A3 = 19.5 V), not a battery


def load_settings(path=SETTINGS):
    try:
        return {**DEFAULTS, **json.loads(Path(path).read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def convert(a3_volts, settings=None):
    """Battery volts from the A3 pin voltage, or None (no reading / not connected)."""
    if a3_volts is None:
        return None
    s = settings or load_settings()
    try:
        volts = float(a3_volts) / float(s["divider"]) * float(s["scale"]) + float(s["offset_v"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if volts < NOT_CONNECTED_V or volts > MAX_V:
        return None
    return volts


def reading(a3_volts, settings=None):
    """The 'battery' sensor data: {"voltage", "a3_volts"}; voltage is None if unusable."""
    return {"voltage": convert(a3_volts, settings),
            "a3_volts": None if a3_volts is None else round(a3_volts, 4)}


def scale_for(meter_volts, a3_volts, settings=None):
    """The scale that makes convert() equal the meter reading (keeping the offset)."""
    s = settings or load_settings()
    return (meter_volts - float(s["offset_v"])) / (a3_volts / float(s["divider"]))
