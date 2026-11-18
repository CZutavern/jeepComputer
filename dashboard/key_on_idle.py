"""
"Key on, engine off" watch for the dashboard (dashboard/pages/home.py).

Leaving the ignition switch on with the engine stopped keeps the HEI module and coil powered. If the module
is shorted, or just left on for long, the coil overheats (the 2026-11 coil melted its potting compound).
After WARN_S of key-on with no engine the dashboard banner warns; after CRITICAL_S it goes critical.

"Key on" = the gauge senders are powered (not the Accessory position). Any tach reading above CRANK_RPM
(cranking or running) restarts the timer, so a start attempt does not trigger it; it counts from the
moment cranking stops. Sensors offline = no verdict (the timer resets).
"""

import time

WARN_S = 60
CRITICAL_S = 180
CRANK_RPM = 30


class KeyOnIdle:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.since = None

    def update(self, analog_ok, ignition_off, engine_running, rpm):
        """Returns seconds of key-on / engine-off so far (0 when not in that state)."""
        now = self.clock()
        if (not analog_ok) or ignition_off or engine_running or (rpm or 0) > CRANK_RPM:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = now
        return now - self.since

    @staticmethod
    def level(idle_s):
        """None, "warning" or "critical"."""
        if idle_s >= CRITICAL_S:
            return "critical"
        if idle_s >= WARN_S:
            return "warning"
        return None
