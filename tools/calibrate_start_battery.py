#!/usr/bin/env python3
"""
Calibrate the starting-battery reading (ADS1115 A3) against a multimeter.

    .venv/bin/python tools/calibrate_start_battery.py 12.62

Measure the battery's terminal voltage with a multimeter, run this with that
number, and it averages ~20 A3 samples and stores the "scale" in
~/.config/jeep_computer/battery.json so v_start equals the meter reading.
Run it again any time: the result depends only on the meter reading and the
current A3 voltage, not on the old scale, so repeating it changes nothing.
The dashboard picks the new scale up on its next restart.

Refuses (and changes nothing) if A3 is below 0.5 V (battery not wired) or the
implied scale is outside 0.8-1.2 (the divider or wiring is off).
"""

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sensors import battery  # noqa: E402

MIN_A3_V = 0.5
SCALE_RANGE = (0.8, 1.2)
SAMPLES = 20


def plan(meter_volts, a3_volts, settings):
    """(new_scale, None) or (None, reason it was refused)."""
    if a3_volts < MIN_A3_V:
        return None, f"A3 reads {a3_volts:.3f} V (< {MIN_A3_V} V): the battery divider isn't connected"
    scale = battery.scale_for(meter_volts, a3_volts, settings)
    if not SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]:
        return None, (f"implied scale {scale:.3f} is outside {SCALE_RANGE[0]}-{SCALE_RANGE[1]}: "
                      f"check the meter reading and the 100k/22k divider")
    return scale, None


def save(scale, path=battery.SETTINGS):
    """Merge the scale into battery.json (other keys kept). True if the file changed."""
    path = Path(path)
    settings = battery.load_settings(path)
    if abs(settings["scale"] - scale) < 1e-6 and path.exists():
        return False
    settings["scale"] = round(scale, 6)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.replace(path)
    return True


def read_a3():
    import board
    from sensors.ads1115 import ADS1115
    adc = ADS1115(board.I2C())
    samples = []
    for _ in range(SAMPLES):
        samples.append(adc.channels["A3"].voltage)
        time.sleep(0.05)
    return statistics.mean(samples)


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        meter = float(argv[1])
    except ValueError:
        print("The multimeter reading must be a number, e.g. 12.62")
        return 2
    if not 6.0 <= meter <= 18.0:
        print(f"{meter} V doesn't look like a 12 V battery reading")
        return 2

    a3 = read_a3()
    settings = battery.load_settings()
    scale, why = plan(meter, a3, settings)
    if scale is None:
        print("Refusing:", why)
        return 1

    changed = save(scale)
    settings["scale"] = scale
    print(f"A3 = {a3:.4f} V (mean of {SAMPLES}); meter {meter:.2f} V -> scale {scale:.4f}"
          f" ({'saved' if changed else 'unchanged'}); v_start now {battery.convert(a3, settings):.2f} V")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
