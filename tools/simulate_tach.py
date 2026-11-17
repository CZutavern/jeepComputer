"""Fake tach signal for bench tests: pulses GPIO 13 low at the spark rate of a chosen RPM.
Wire GPIO 13 (header pin 33) to GPIO 12 (pin 32), grounds are already common. Run it with the
dashboard running; the RPM dial should follow.   usage: simulate_tach.py 800 [seconds]
Spark rate = rpm * cylinders / 120 (8 cylinders: 800 rpm = 53 Hz, 4500 = 300 Hz, 6000 = 400 Hz)."""
import sys, time
from datetime import timedelta
import gpiod
from gpiod.line import Direction, Value

rpm = float(sys.argv[1]) if len(sys.argv) > 1 else 800
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 30
hz = rpm * 8 / 120
period = 1.0 / hz
req = gpiod.request_lines("/dev/gpiochip0", consumer="tach-sim", config={
    13: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)})
end = time.perf_counter() + secs
nxt = time.perf_counter()
print(f"{rpm:.0f} rpm = {hz:.1f} pulses/s on GPIO 13 for {secs:.0f} s")
try:
    while time.perf_counter() < end:
        req.set_value(13, Value.INACTIVE)               # pulse low (a "spark")
        t = time.perf_counter() + min(0.0015, period / 3)
        while time.perf_counter() < t: pass
        req.set_value(13, Value.ACTIVE)
        nxt += period
        while time.perf_counter() < nxt: pass
finally:
    req.set_value(13, Value.ACTIVE); req.release()
