"""
Jeep gauge senders (fuel, coolant temperature, oil pressure) read
through the ADS1115.

Each sender is a variable resistor (Rs) to ground, fed through its
gauge's coil (Rp) from the gauge supply (Vs). The ADS1115 reads the
sender side, so:

    V_out = Vs * Rs / (Rs + Rp)   ->   Rs = Rp * V_out / (Vs - V_out)

and each sender's curve turns Rs into a value.

Vs and Rp, measured in the Jeep (2026-09-25, gauges on, engine off):
oil at 0 psi (73 ohm) read 1.04 V and fuel near full (~12.5 ohm)
0.49 V, which gives Rp ~ 22 ohm (the same as the bench fit below) and
Vs ~ 1.35 V as the ADS1115 sees the gauge supply. Coolant then reads
~32 ohm, about 180 F: mid-gauge, as the gauge showed. The first
calibration assumed the bench's 3.56 V supply, which put every
reading at the full / hot / 80 psi end ("max the whole time").

With the gauges off the sender wires sit at 0 V, which reads as a
short (below SHORT_CIRCUIT_OHMS) and so as no value.

Vs is also learned as the Jeep is used (engine/logger.py): at every
key-on with the engine off, oil pressure is 0, so the oil sender is at
OIL_ZERO_OHMS and its voltage gives Vs. The median over recent trips is
kept in CAL_FILE and used here instead of SUPPLY_V.

If the gauge supply turns out to vary (e.g. engine running vs not),
wire it to the ADS1115's spare A3 and make this ratiometric: Vs = A3.

Fuel override: ~/.config/jeep_computer/fuel_cal.json {"points": [[ohms, percent], ...]}
(written by tools/capture_fuel_point.py). Its points replace the default fuel
points with the same percent and are added otherwise; see merge_fuel_points().
Read when the dashboard starts.

Curves:   "thermistor" - Beta model, 1/T = 1/T0 + ln(Rs/R0) / B.
                         Physically correct, so it extrapolates well
                         (coolant fits B ~ 3560 K).
          "interp"     - straight lines between the calibration
                         points, by resistance, clamped to their range
                         (fuel can't go below empty, etc.).
"""

import json
import math
from pathlib import Path


# -------------------------------------------------------------
# Calibration: (sender resistance ohm, value, bench V_out)
# The bench V_out column is from the first calibration (3.56 V supply);
# only resistance -> value is used now.
# -------------------------------------------------------------

CALIBRATION = {
    "fuel": [
        (73, 0.0, 2.71),     # Empty
        (23, 50.0, 1.82),    # Half
        (10, 100.0, 1.10),   # Full
    ],
    "coolant": [
        (73, 130.0, 2.71),
        (36, 171.0, 2.20),
        (13, 242.0, 1.32),
        (9, 270.0, 1.02),
    ],
    "oil_pressure": [
        (73, 0.0, 2.71),
        (23, 40.0, 1.82),
        (10, 80.0, 1.10),
    ],
}

# A coolant reading checked in the Jeep: (sender ohms, °F). The curve keeps its
# shape (B) but is shifted to pass through it. 2026-10-05, idling warm after the
# frame ground was added: the bench curve said 206 °F where the user judged
# 180-185 °F from the factory gauge (needle a third to just under half).
COOLANT_ANCHOR = (21.2, 182.0)

# Which ADS1115 input each sender is wired to
CHANNELS = {
    "fuel": "A0",
    "coolant": "A1",
    "oil_pressure": "A2",
}

# The gauge circuit as the ADS1115 sees it (measured in the Jeep, see above)
SUPPLY_V = 1.35
PULLUP_OHMS = 22.0
OIL_ZERO_OHMS = 73.0        # oil sender at 0 psi (CALIBRATION)

# Learned calibration (engine/logger.py): {"supply_v": ..., ...}
CAL_FILE = Path.home() / ".config/jeep_computer/senders_cal.json"
SUPPLY_RANGE_V = (0.6, 5.0)  # anything learned outside this is ignored

# Readings outside these resistances mean a wiring fault, not a value
OPEN_CIRCUIT_OHMS = 250.0    # sender unplugged / broken wire
SHORT_CIRCUIT_OHMS = 2.0     # wire shorted to ground (or gauges off: 0 V)


FUEL_CAL_FILE = Path.home() / ".config/jeep_computer/fuel_cal.json"


def merge_fuel_points(defaults, custom):
    """Fuel curve points: custom [(ohms, percent)] replace the default points with
    the same percent and are added otherwise. defaults are (ohms, percent, bench_v)
    tuples, the result (ohms, percent) pairs sorted by resistance. Raises ValueError
    for a bad point, or if the merged curve isn't monotonic (more ohms = less fuel)."""
    by_percent = {pct: ohms for ohms, pct, _v in defaults}
    for point in custom:
        ohms, pct = float(point[0]), float(point[1])
        if not (0 < ohms < OPEN_CIRCUIT_OHMS and 0 <= pct <= 100):
            raise ValueError(f"bad fuel point {point}")
        by_percent[pct] = ohms
    merged = sorted((ohms, pct) for pct, ohms in by_percent.items())
    for (r0, p0), (r1, p1) in zip(merged, merged[1:]):
        if not (r1 > r0 and p1 < p0):
            raise ValueError(f"fuel points out of order: {r0} ohm {p0} % / {r1} ohm {p1} %")
    return merged


def load_fuel_points(path=None):
    """The effective fuel points: defaults plus fuel_cal.json when it is present and sane."""
    defaults = CALIBRATION["fuel"]
    path = FUEL_CAL_FILE if path is None else Path(path)
    try:
        custom = json.loads(path.read_text())["points"]
        return merge_fuel_points(defaults, custom)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, IndexError) as e:
        print("fuel_cal.json ignored:", e, flush=True)
    return sorted((r, v) for r, v, _b in defaults)


def _linear_fit(xs, ys):
    """Return (intercept, slope) of the best-fit line."""

    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n

    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))

    slope = sxy / sxx
    return my - slope * mx, slope


def _f_to_k(f):
    return (f - 32) * 5 / 9 + 273.15


def _k_to_f(k):
    return (k - 273.15) * 9 / 5 + 32


# -------------------------------------------------------------
# Divider: V_out <-> sender resistance
# -------------------------------------------------------------

class Divider:

    def __init__(self, supply, pullup):
        self.supply = supply
        self.pullup = pullup

    def resistance(self, voltage):
        if voltage >= self.supply:
            return math.inf
        return self.pullup * voltage / (self.supply - voltage)

    def voltage(self, resistance):
        return self.supply * resistance / (resistance + self.pullup)


# -------------------------------------------------------------
# Sender curves
# -------------------------------------------------------------

class ThermistorCurve:
    """Resistance -> temperature (°F) using the Beta equation."""

    def __init__(self, points):
        """points: (resistance, temperature_f) pairs."""

        # ln(R) = ln(R_inf) + B / T  -> linear in 1/T
        intercept, self.beta = _linear_fit(
            [1 / _f_to_k(t) for r, t in points],
            [math.log(r) for r, t in points],
        )
        self.r_inf = math.exp(intercept)

    def value(self, resistance):
        return _k_to_f(self.beta / math.log(resistance / self.r_inf))


class InterpCurve:
    """Resistance -> value, straight lines between calibration points,
    clamped to the calibrated range."""

    def __init__(self, points):
        """points: (resistance, value) pairs."""
        self.points = sorted(points)

    def value(self, resistance):
        pts = self.points
        if resistance <= pts[0][0]:
            return pts[0][1]
        if resistance >= pts[-1][0]:
            return pts[-1][1]
        for (r0, v0), (r1, v1) in zip(pts, pts[1:]):
            if r0 <= resistance <= r1:
                return v0 + (v1 - v0) * (resistance - r0) / (r1 - r0)


CURVE_TYPES = {
    "fuel": InterpCurve,
    "coolant": ThermistorCurve,
    "oil_pressure": InterpCurve,
}

_cal = {"mtime": None, "supply_v": None}


def supply_v():
    """The gauge supply to use: learned if there is a sane value, else SUPPLY_V."""
    try:
        mtime = CAL_FILE.stat().st_mtime
    except OSError:
        return SUPPLY_V
    if mtime != _cal["mtime"]:
        _cal["mtime"] = mtime
        try:
            learned = float(json.loads(CAL_FILE.read_text()).get("supply_v"))
            _cal["supply_v"] = learned if SUPPLY_RANGE_V[0] <= learned <= SUPPLY_RANGE_V[1] else None
        except (OSError, ValueError, TypeError):
            _cal["supply_v"] = None
    return _cal["supply_v"] or SUPPLY_V


def supply_from_oil_zero(voltage):
    """Vs implied by the oil sender's voltage at 0 psi (engine off)."""
    return voltage * (OIL_ZERO_OHMS + PULLUP_OHMS) / OIL_ZERO_OHMS


def resistance(voltage):
    return Divider(supply_v(), PULLUP_OHMS).resistance(voltage)


DIVIDER = Divider(SUPPLY_V, PULLUP_OHMS)       # the default, for reference
CURVES = {name: CURVE_TYPES[name]([(r, value) for r, value, v in points])
          for name, points in CALIBRATION.items()}
CURVES["fuel"] = InterpCurve(load_fuel_points())
if COOLANT_ANCHOR:
    _ohms, _f = COOLANT_ANCHOR
    CURVES["coolant"].r_inf = _ohms / math.exp(CURVES["coolant"].beta / _f_to_k(_f))


def convert(name, voltage):
    """
    Convert one sender's V_out to its value.

    Returns None when the reading is missing or shows a wiring
    fault (open or shorted sender) or the gauges are off.
    """

    if voltage is None:
        return None

    ohms = resistance(voltage)

    if not SHORT_CIRCUIT_OHMS <= ohms <= OPEN_CIRCUIT_OHMS:
        return None

    return CURVES[name].value(ohms)


def read_senders(sensor_data):
    """
    All sender values from SensorManager output:
    {"fuel": %, "coolant": °F, "oil_pressure": psi}, None if unavailable.
    """

    analog = (sensor_data.get("analog") or {}).get("data") or {}

    result = {}

    for name, channel in CHANNELS.items():
        voltage = (analog.get(channel) or {}).get("voltage")
        result[name] = convert(name, voltage)

    return result
