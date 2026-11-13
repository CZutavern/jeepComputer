"""
Pi 5 power draw from the PMIC's rail monitors (vcgencmd pmic_read_adc).

Sums voltage x current over the SoC-side rails. It doesn't see what the
5 V input feeds directly (USB devices, the HAT, regulator losses), so the
real draw from the UPS is higher; use it to compare modes.

Usage: python3 tools/pi_power.py [samples] [interval_s]
"""

import re
import statistics
import subprocess
import sys
import time


def read_rails():
    out = subprocess.run(["vcgencmd", "pmic_read_adc"], capture_output=True, text=True).stdout
    amps, volts = {}, {}
    for name, kind, value in re.findall(r"(\S+)_([AV]) \w+\(\d+\)=([\d.]+)", out):
        (amps if kind == "A" else volts)[name] = float(value)
    return amps, volts


def power_w():
    amps, volts = read_rails()
    return sum(a * volts[name] for name, a in amps.items() if name in volts)


def main():
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    readings = []
    for _ in range(samples):
        readings.append(power_w())
        time.sleep(interval)

    print(f"SoC rails: {statistics.fmean(readings):.2f} W average "
          f"(min {min(readings):.2f}, max {max(readings):.2f}, {samples} samples)")


if __name__ == "__main__":
    main()
