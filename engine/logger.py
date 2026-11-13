"""
Engine log, fuel use and gauge self-calibration. Runs on the sensor thread
(sensors/sensor_worker.py): add(data) once a second with the SensorManager
output; returns a summary for the dashboard (data["engine"]).

Trips: a trip is one ignition-on period (gauge senders powered: at least
IGNITION_MIN_SENDERS of fuel/coolant/oil have voltage, so one stray wire
can't start a trip; in Accessory the oil input alone reads ~0.8 V). It ends
after TRIP_END_S with the ignition off, so a quick stop doesn't split it.
The engine counts as running from the tach (RUNNING_RPM). With no tach
signal yet and a tach that has never seen the engine run (tach_proven),
it is the alternator charging the starting battery (v_start from
ADS1115 A3, RUNNING_START_V) or oil pressure (RUNNING_OIL_PSI). The ACC key
state (sensors/acc.py) is kept as "accessory" info: the key is on (Accessory
or On) even when the gauges are off. Each 10 s row also stores the starting
battery voltage (in the v_system column, which held the retired INA238's
reading) and IMU jitter, so running can be refitted from vibration later.

Fuel use: no distance yet (no GPS), so gallons per engine hour: the fuel
level dropped since the last fill-up (a jump of REFUEL_MIN_PCT between
trips), times the TANK_GAL tank, over the engine hours since. Needs a bit
of running (GPH_MIN_*) before it's shown. Also gallons left and the engine
hours they'd last.

Self-calibration: key on, engine off (and not just stopped) means 0 psi of
oil, so the oil sender is at its 0 psi resistance and its voltage gives the
gauge supply (sensors/senders.py). Those moments are stored as anchors;
after each trip the supply is re-learned as the median over the last
LEARN_TRIPS trips and written to senders.CAL_FILE, which the conversion
then uses. Anchors only count ("trusted") once the tach has seen the engine
run, or with no tach, when oil pressure says the engine has been off for
ANCHOR_AFTER_ENGINE_S; and only within ANCHOR_MAX_JUMP of the current supply.
Everything raw is stored too (engine/store.py) for refitting the curves.
"""

import json
import statistics
import time

from engine.store import EngineStore
from sensors import senders

CHANNELS = ("A0", "A1", "A2", "A3")


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _jitter(rows):
    """How much pitch and roll wobble over a 10 s row (degrees, standard deviation summed): engine
    vibration with the Jeep parked. None without enough IMU readings."""
    total = 0.0
    for key in ("pitch", "roll"):
        values = [r[key] for r in rows if r.get(key) is not None]
        if len(values) < 3:
            return None
        total += statistics.pstdev(values)
    return round(total, 4)


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


class EngineLogger:

    SAMPLE_S = 10
    IGNITION_ON_V = 0.01         # a sender above this has power (gauges off: about -0.02 V; running,
                                 # the ground offset pulls coolant down to ~0.04 V)
    IGNITION_MIN_SENDERS = 2     # this many of fuel/coolant/oil powered: gauges on
    RUNNING_RPM = 300
    RUNNING_START_V = 13.2       # starting battery charging (the 2026-10-05 test: 13.1-13.9 V running vs 12.1-12.3 V key on)
    RUNNING_OIL_PSI = 15         # engine off reads up to ~9 psi until the supply is learned; running read 23+
    TRIP_END_S = 120
    FUEL_SETTLE_S = 60           # fuel at the start: median of the first minute
    ANCHOR_AFTER_KEY_ON_S = 15   # let the gauge regulator settle
    ANCHOR_AFTER_ENGINE_S = 90   # oil pressure bleeds off after the engine stops
    REFUEL_MIN_PCT = 8
    TANK_GAL = 21.0
    GPH_MIN_ENGINE_H = 0.5
    GPH_MIN_USED_PCT = 4
    LEARN_TRIPS = 20
    LEARN_MIN_ANCHORS = 3
    ANCHOR_MAX_JUMP = 0.3        # an anchor this far (fraction) from the current supply is ignored
    SUMMARY_S = 30

    def __init__(self, store_factory=EngineStore):
        self.store_factory = store_factory
        self.store = None
        self.buffer = []
        self.bucket = None
        self.trip = None
        self.trip_engine_s = 0.0
        self.ignition_since = None
        self.ignition_off_since = None
        self.fuel_start_done = False
        self.last_running = None
        self.was_running = False
        self.last_tick = None
        self.summary = {}
        self.last_summary = 0.0
        self.tach_proven = False

    # ------------------------------------------------------------ input

    def add(self, data, now=None):
        now = time.time() if now is None else now
        if self.store is None:
            self.store = self.store_factory()
            self.tach_proven = self.store.get("tach_proven") == "1"
            self.resume(now)

        r = self.read(data)
        self.track(r, now)

        bucket = int(now // self.SAMPLE_S)
        if self.bucket is not None and bucket != self.bucket:
            self.flush(self.bucket * self.SAMPLE_S, now)
        self.bucket = bucket
        self.buffer.append(r)

        if now - self.last_summary >= self.SUMMARY_S:
            self.summary = self.compute_summary(now)
            self.last_summary = now
        return {**self.summary, "running": r["running"], "ignition": r["ignition"], "accessory": r["acc"],
                "trip_engine_min": round(self.trip_engine_s / 60, 1) if self.trip else None}

    def read(self, data):
        analog = data.get("analog") or {}
        channels = (analog.get("data") or {}) if analog.get("status") == "OK" else {}
        volts = {c: (channels.get(c) or {}).get("voltage") for c in CHANNELS}
        powered = sum(v is not None and v >= self.IGNITION_ON_V for c, v in volts.items() if c != "A3")
        ignition = powered >= self.IGNITION_MIN_SENDERS

        # Starting battery (ADS1115 A3); the old INA238 "power" entry is gone
        battery = data.get("battery") or {}
        v_start = (battery.get("data") or {}).get("voltage") if battery.get("status") == "OK" else None
        acc = data.get("acc") or {}
        acc_on = (acc.get("data") or {}).get("key_on") if acc.get("status") == "OK" else None
        imu = data.get("imu") or {}
        imu = (imu.get("data") or {}) if imu.get("status") == "OK" else {}

        tach = data.get("tach") or {}
        rpm = (tach.get("data") or {}).get("rpm") if tach.get("status") == "OK" else None

        values = {name: senders.convert(name, volts[ch]) if ignition else None
                  for name, ch in senders.CHANNELS.items()}
        if rpm is not None and rpm >= self.RUNNING_RPM and not self.tach_proven:
            self.store.set("tach_proven", "1")
            self.tach_proven = True
        if rpm is not None or self.tach_proven:
            # A tach that has seen the engine run is believed: no signal = off.
            # (Not oil pressure: until the supply is learned, oil at rest can
            # read a few psi, which would block the very anchors that fix it.)
            running = rpm is not None and rpm >= self.RUNNING_RPM
        else:
            running = ignition and ((v_start or 0) >= self.RUNNING_START_V
                                    or (values["oil_pressure"] or 0) >= self.RUNNING_OIL_PSI)
        return {"ignition": ignition, "running": running, "rpm": rpm, "volts": volts, "v_system": v_start,
                "acc": acc_on,
                "pitch": imu.get("pitch"), "roll": imu.get("roll"), **values}

    # ------------------------------------------------------------ trips

    def resume(self, now):
        """Carry on a trip the dashboard was restarted in the middle of."""
        last = self.store.last_trip()
        if not last or last["end_ts"] is not None:
            return
        last_ts = self.store.last_sample_ts() or last["start_ts"]
        if now - last_ts < self.TRIP_END_S + 60:
            self.trip = last["id"]
            self.trip_engine_s = last["engine_s"] or 0.0
            self.ignition_since = last["start_ts"]
            self.fuel_start_done = last["fuel_start"] is not None
        else:
            self.trip = last["id"]
            self.trip_engine_s = last["engine_s"] or 0.0
            self.close_trip(last_ts)

    def track(self, r, now):
        dt = 0.0 if self.last_tick is None else min(5.0, max(0.0, now - self.last_tick))
        self.last_tick = now

        if r["ignition"]:
            self.ignition_off_since = None
            if self.trip is None:
                self.trip = self.store.open_trip(now)
                self.trip_engine_s = 0.0
                self.ignition_since = now
                self.fuel_start_done = False
                self.last_running = None
            if r["running"]:
                self.trip_engine_s += dt
                self.last_running = now
                if not self.was_running:
                    self.learn()            # the key-on anchors before this start
        elif self.trip is not None:
            if self.ignition_off_since is None:
                self.ignition_off_since = now
            elif now - self.ignition_off_since >= self.TRIP_END_S:
                self.close_trip(self.ignition_off_since)
        self.was_running = r["running"]

    def close_trip(self, end_ts):
        fuel = self.store.trip_samples(self.trip, "fuel_pct")
        self.store.update_trip(self.trip, end_ts=int(end_ts), engine_s=round(self.trip_engine_s, 1),
                               fuel_end=_median(fuel[-6:]))
        self.learn()
        self.store.prune()
        self.trip = None
        self.trip_engine_s = 0.0
        self.last_summary = 0.0             # refresh the summary now

    def settle_fuel_start(self, now):
        """Fuel at the start of the trip, and a fill-up since the last one."""
        if self.fuel_start_done or self.trip is None or now - self.ignition_since < self.FUEL_SETTLE_S:
            return
        start = _median(self.store.trip_samples(self.trip, "fuel_pct")[:6])
        if start is None:
            return
        fields = {"fuel_start": round(start, 1)}
        previous = self.store.previous_trip(self.trip)
        if previous and previous["fuel_end"] is not None and start - previous["fuel_end"] >= self.REFUEL_MIN_PCT:
            fields["refuel_gal"] = round((start - previous["fuel_end"]) / 100 * self.TANK_GAL, 1)
            print(f"Fill-up: fuel {previous['fuel_end']:.0f} -> {start:.0f} % "
                  f"(~{fields['refuel_gal']} gal)", flush=True)
        self.store.update_trip(self.trip, **fields)
        self.fuel_start_done = True

    # ------------------------------------------------------------ 10 s rows

    def flush(self, ts, now):
        rows, self.buffer = self.buffer, []
        if not rows:
            return
        ignition = sum(r["ignition"] for r in rows) * 2 > len(rows)
        if not ignition:
            return
        row = {
            "ts": ts, "trip": self.trip, "ignition": 1,
            "running": int(sum(r["running"] for r in rows) * 2 > len(rows)),
            "rpm": _mean(r["rpm"] for r in rows),
            "v_fuel": _mean(r["volts"]["A0"] for r in rows),
            "v_coolant": _mean(r["volts"]["A1"] for r in rows),
            "v_oil": _mean(r["volts"]["A2"] for r in rows),
            "v_aux": _mean(r["volts"]["A3"] for r in rows),
            "supply_v": senders.supply_v(),
            "fuel_pct": _mean(r["fuel"] for r in rows),
            "coolant_f": _mean(r["coolant"] for r in rows),
            "oil_psi": _mean(r["oil_pressure"] for r in rows),
            "v_system": _mean(r["v_system"] for r in rows),
            "imu_jitter": _jitter(rows),
        }
        self.store.add_sample(row)
        if self.trip is not None:
            self.store.update_trip(self.trip, engine_s=round(self.trip_engine_s, 1))
            self.settle_fuel_start(now)
        self.maybe_anchor(row, now)

    def maybe_anchor(self, row, now):
        if row["running"] or row["v_oil"] is None or self.ignition_since is None:
            return
        if row["rpm"] is not None and row["rpm"] > 50:
            return
        if now - self.ignition_since < self.ANCHOR_AFTER_KEY_ON_S:
            return
        if self.last_running is not None and now - self.last_running < self.ANCHOR_AFTER_ENGINE_S:
            return
        supply = senders.supply_from_oil_zero(row["v_oil"])
        # Far off the current supply: not an engine-off moment after all
        # (e.g. a tach that stopped working while the engine ran)
        plausible = abs(supply - senders.supply_v()) <= self.ANCHOR_MAX_JUMP * senders.supply_v()
        self.store.add_anchor(row["ts"], self.trip, "oil_zero", round(row["v_oil"], 4), round(supply, 4),
                              plausible)

    # ------------------------------------------------------------ learning

    def learn(self):
        """Gauge supply from the trusted engine-off anchors of recent trips."""
        sessions = [s for s in self.store.anchor_sessions("oil_zero", self.LEARN_TRIPS)
                    if len(s) >= self.LEARN_MIN_ANCHORS]
        medians = [statistics.median(s) for s in sessions]
        medians = [m for m in medians if senders.SUPPLY_RANGE_V[0] <= m <= senders.SUPPLY_RANGE_V[1]]
        if not medians:
            return
        learned = statistics.median(medians)
        try:
            current = json.loads(senders.CAL_FILE.read_text()).get("supply_v")
        except (OSError, ValueError):
            current = None
        if current and abs(learned - current) / current < 0.005:
            return
        senders.CAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = senders.CAL_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "supply_v": round(learned, 4), "trips": len(medians), "updated": int(time.time()),
            "how": "median over recent trips of the oil sender at 0 psi (key on, engine off); engine/logger.py",
        }, indent=2))
        tmp.replace(senders.CAL_FILE)
        print(f"Gauge supply learned: {learned:.3f} V from {len(medians)} trip(s)", flush=True)

    # ------------------------------------------------------------ summary

    def compute_summary(self, now):
        s = self.store
        fuel_now = _median(s.recent("fuel_pct", 600, now))
        first = s.last_refuel_trip(1.5) or s.first_trip_id()
        gph = window_h = None
        if first is not None:
            trips = s.trips_since(first)
            start = next((t["fuel_start"] for t in trips if t["fuel_start"] is not None), None)
            window_h = sum((t["engine_s"] or 0) for t in trips) / 3600
            if start is not None and fuel_now is not None:
                used = start - fuel_now
                if window_h >= self.GPH_MIN_ENGINE_H and used >= self.GPH_MIN_USED_PCT:
                    gph = used / 100 * self.TANK_GAL / window_h
        gallons_left = None if fuel_now is None else fuel_now / 100 * self.TANK_GAL
        try:
            learned = json.loads(senders.CAL_FILE.read_text())
        except (OSError, ValueError):
            learned = {}
        return {
            "engine_hours": round(s.total_engine_s() / 3600, 2),
            "gph": None if gph is None else round(gph, 2),
            "gph_engine_hours": None if window_h is None else round(window_h, 2),
            "gallons_left": None if gallons_left is None else round(gallons_left, 1),
            "hours_left": None if gph is None or gallons_left is None else round(gallons_left / gph, 1),
            "supply_v": round(senders.supply_v(), 3),
            "supply_learned_trips": learned.get("trips", 0),
            "tach_proven": s.get("tach_proven") == "1",
        }
