#!/usr/bin/env python3
"""
Record one point of the fuel-sender curve from the live reading.

    .venv/bin/python tools/capture_fuel_point.py 0          # tank truly empty, 20 s average
    .venv/bin/python tools/capture_fuel_point.py 25 30      # 25 % full, 30 s
    .venv/bin/python tools/capture_fuel_point.py --show     # print the curve and live reading, change nothing

Averages the fuel input (ADS1115 A0) for the given seconds, turns it into
sender ohms with the same gauge supply the dashboard uses (the learned supply
in senders_cal.json), and stores [ohms, percent] in
~/.config/jeep_computer/fuel_cal.json (the old file is kept as fuel_cal.json.bak).
A point replaces the default point with the same percent; others are added.
The dashboard reads the file at start, so restart it to use the new curve.

Refuses, and writes nothing, when the gauges are off or the reading looks like
an open or shorted circuit (e.g. all four ADS1115 inputs sitting at ~3.5 V).
Needs the key On (gauges powered), not just Accessory.
"""

import argparse
import json
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sensors import senders  # noqa: E402

MIN_SECONDS = 8            # the gauge supply pulses on a ~1.1 s cycle: average over several


class Refused(Exception):
    pass


def ohms_from_voltage(voltage):
    """Sender ohms for the averaged A0 voltage, or Refused with the reason."""
    if voltage is None:
        raise Refused("no reading from the fuel input (A0)")
    supply = senders.supply_v()
    ohms = senders.resistance(voltage)
    if voltage >= supply:
        raise Refused(f"A0 reads {voltage:.2f} V, at or above the gauge supply ({supply:.2f} V): "
                      f"open circuit (sender unplugged, floating input or disconnected harness)")
    if ohms > senders.OPEN_CIRCUIT_OHMS:
        raise Refused(f"A0 reads {voltage:.3f} V = {ohms:.0f} ohm: open circuit "
                      f"(limit {senders.OPEN_CIRCUIT_OHMS:.0f} ohm)")
    if ohms < senders.SHORT_CIRCUIT_OHMS:
        raise Refused(f"A0 reads {voltage:.3f} V = {ohms:.1f} ohm: gauges off (key not in On) "
                      f"or the sender wire is shorted")
    return ohms


def update_file(path, ohms, percent):
    """Add or replace the point (same percent) in fuel_cal.json, keeping a .bak. Returns the points."""
    path = Path(path)
    points = []
    if path.exists():
        points = json.loads(path.read_text()).get("points", [])
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    points = [p for p in points if abs(float(p[1]) - percent) > 1e-9]
    points.append([round(ohms, 2), percent])
    points.sort(key=lambda p: p[0])
    senders.merge_fuel_points(senders.CALIBRATION["fuel"], points)       # must make a sane curve
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"points": points}, indent=2) + "\n")
    tmp.replace(path)
    return points


def sample_a0(seconds):
    import board
    from sensors.ads1115 import ADS1115
    adc = ADS1115(board.I2C())
    values = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            values.append(adc.channels["A0"].voltage)
        except OSError:                 # a NAK while the dashboard uses the bus: skip the sample
            pass
        time.sleep(0.05)
    if len(values) < 10:
        raise Refused(f"only {len(values)} good readings from the ADS1115 (I2C errors): try again")
    return statistics.mean(values), statistics.pstdev(values), len(values)


def show():
    print("Fuel curve in use (ohms -> percent):")
    for ohms, pct in senders.load_fuel_points():
        print(f"  {ohms:6.1f} ohm  {pct:5.1f} %")
    print(f"Gauge supply {senders.supply_v():.4f} V, pull-up {senders.PULLUP_OHMS} ohm; "
          f"file {senders.FUEL_CAL_FILE} {'present' if senders.FUEL_CAL_FILE.exists() else 'absent'}")
    try:
        mean, sd, n = sample_a0(3)
    except Refused as e:
        print("Live A0:", e)
        return
    print(f"Live A0: {mean:.3f} V (sd {sd:.3f}, {n} samples)")
    try:
        ohms = ohms_from_voltage(mean)
        print(f"  = {ohms:.1f} ohm = {senders.CURVES['fuel'].value(ohms):.1f} % on the curve in use")
    except Refused as e:
        print("  not a usable reading:", e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("percent", nargs="?", type=float, help="true fuel level in percent (0 = empty)")
    ap.add_argument("seconds", nargs="?", type=float, default=20, help="averaging time (default 20)")
    ap.add_argument("--show", action="store_true", help="print the curve and live reading, change nothing")
    args = ap.parse_args()

    if args.show:
        show()
        return 0
    if args.percent is None:
        ap.print_usage()
        return 2
    if not 0 <= args.percent <= 100:
        print("Percent must be 0-100")
        return 2
    if args.seconds < MIN_SECONDS:
        print(f"Average over at least {MIN_SECONDS} s (the gauge supply pulses)")
        return 2

    try:
        mean, sd, n = sample_a0(args.seconds)
        ohms = ohms_from_voltage(mean)
    except Refused as e:
        print("Refusing, nothing written:", e)
        return 1
    print(f"A0 = {mean:.4f} V (sd {sd:.3f}, {n} samples) -> {ohms:.2f} ohm at {args.percent:g} %")
    try:
        points = update_file(senders.FUEL_CAL_FILE, ohms, args.percent)
    except ValueError as e:
        print("Refusing, nothing written:", e)
        return 1
    print("Saved", senders.FUEL_CAL_FILE, "points:", points)
    return 0


if __name__ == "__main__":
    sys.exit(main())
