"""
ADS1115: the gauge senders (A0 fuel, A1 coolant, A2 oil; see senders.py).

The Jeep's gauge supply is a constant-voltage regulator that pulses: in the
Jeep the sender voltages swing about +-15 % over a ~1.1 s cycle, so a single
reading lands anywhere on that wave (oil at 0 psi read 0-24 psi). This
samples every input in its own thread, ~20 times a second, and reports the
average over the last WINDOW_S (a few regulator cycles; the real gauges are
slower still).
"""

import collections
import threading
import time

import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

try:
    from adafruit_ads1x15.ads1x15 import Pin        # library 3.x
    PINS = (Pin.A0, Pin.A1, Pin.A2, Pin.A3)
except ImportError:                                 # 2.x
    PINS = (ADS.P0, ADS.P1, ADS.P2, ADS.P3)


class ADS1115:
    WINDOW_S = 8.0           # ~7 regulator cycles; leftover ripple was about +-3 % at 4 s
    ROUND_S = 0.05          # one pass over the four inputs
    STALE_S = 9.0           # no good sample for this long: report an error

    def __init__(self, i2c):
        self.ads = ADS.ADS1115(i2c)         # raises if the chip isn't there
        self.ads.data_rate = 860

        self.channels = {
            f"A{i}": AnalogIn(self.ads, pin) for i, pin in enumerate(PINS)
        }
        self.samples = {name: collections.deque() for name in self.channels}
        self.lock = threading.Lock()
        self.last_ok = None
        self.error = None

        self._sample_once()                 # data straight away
        threading.Thread(target=self._run, name="ads1115", daemon=True).start()

    def _sample_once(self):
        now = time.monotonic()
        values = {name: (channel.voltage, channel.value) for name, channel in self.channels.items()}
        with self.lock:
            for name, sample in values.items():
                q = self.samples[name]
                q.append((now, *sample))
                while q and now - q[0][0] > self.WINDOW_S:
                    q.popleft()
            self.last_ok = now
            self.error = None

    def _run(self):
        while True:
            start = time.monotonic()
            try:
                self._sample_once()
            except Exception as e:          # loose connector: keep trying
                with self.lock:
                    self.error = str(e)
                time.sleep(1.0)
            time.sleep(max(0.0, self.ROUND_S - (time.monotonic() - start)))

    def read(self):
        with self.lock:
            if self.last_ok is None or time.monotonic() - self.last_ok > self.STALE_S:
                raise OSError(self.error or "ADS1115 not sampling")
            return {
                name: {
                    "voltage": sum(s[1] for s in q) / len(q),
                    "raw": q[-1][2],
                    "samples": len(q),
                }
                for name, q in self.samples.items() if q
            }
