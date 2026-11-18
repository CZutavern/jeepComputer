"""
"Key on, engine off" watch for the dashboard (dashboard/pages/home.py).

Leaving the ignition switch on with the engine stopped keeps the HEI module and coil powered. If the module
is shorted, or just left on for long, the coil overheats (the 2026-11 coil melted its potting compound).

Key on = the FUEL gauge sender has power (A0 above IGNITION_OFF_V). In Accessory the start battery (A3) still
shows its voltage but the gauges are off, so the fuel gauge is the reliable check (Colton, 2026-11-18).
Any tach reading above CRANK_RPM (cranking or running) restarts the timer, so a start attempt does not trigger
it; it counts from the moment cranking stops. Sensors offline = no verdict (the timer resets).

After CHIME_FIRST_S the chime plays (audio/chime.py) and the banner warns; the chime repeats every
CHIME_REPEAT_S; after CRITICAL_S the banner goes critical.
"""

import time

WARN_S = 30
CHIME_FIRST_S = 30
CHIME_REPEAT_S = 60
CRITICAL_S = 180
CRANK_RPM = 30
IGNITION_OFF_V = 0.2


def fuel_gauge_off(analog_data):
    """True when the fuel sender (A0) has no power (key off or Accessory); None-safe."""
    volts = ((analog_data or {}).get("A0") or {}).get("voltage")
    return volts is None or volts < IGNITION_OFF_V


class KeyOnIdle:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.since = None
        self.last_chime = None

    def update(self, analog_ok, ignition_off, engine_running, rpm):
        """Returns seconds of key-on / engine-off so far (0 when not in that state)."""
        now = self.clock()
        if (not analog_ok) or ignition_off or engine_running or (rpm or 0) > CRANK_RPM:
            self.since = None
            self.last_chime = None
            return 0.0
        if self.since is None:
            self.since = now
        return now - self.since

    def chime_due(self, idle_s):
        """True once at CHIME_FIRST_S, then every CHIME_REPEAT_S while the state lasts."""
        if idle_s < CHIME_FIRST_S:
            return False
        now = self.clock()
        if self.last_chime is None or now - self.last_chime >= CHIME_REPEAT_S:
            self.last_chime = now
            return True
        return False

    @staticmethod
    def level(idle_s):
        """None, "warning" or "critical"."""
        if idle_s >= CRITICAL_S:
            return "critical"
        if idle_s >= WARN_S:
            return "warning"
        return None
