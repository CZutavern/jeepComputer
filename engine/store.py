"""
Engine and gauge-sender history in SQLite (engine/logger.py writes it).

  samples  one row per 10 s while the ignition is on: raw sender voltages
           (A0 fuel, A1 coolant, A2 oil, A3 spare), RPM, the supply voltage
           used, and the converted values, plus the starting-battery voltage (column v_system; it
           held the INA238's reading before 2026-10)
           and IMU jitter (engine vibration) so running can be told apart
           without a tach. Enough to refit the sender calibration from real
           drives later. Kept KEEP_DAYS.
  trips    one row per ignition-on period: engine running time, fuel at the
           start and end, fuel added before it (a fill-up). Kept for good.
  anchors  moments that calibrate the gauge supply: key on, engine off, so
           oil pressure is 0 and the oil sender is at its 0 psi resistance.
  meta     small key/value facts (e.g. the tach has seen the engine run).
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".local/share/jeep_computer/engine.db"
KEEP_DAYS = 365

SAMPLE_COLUMNS = ("ts", "trip", "ignition", "running", "rpm", "v_fuel", "v_coolant", "v_oil", "v_aux",
                  "supply_v", "fuel_pct", "coolant_f", "oil_psi", "v_system", "imu_jitter")


class EngineStore:

    def __init__(self, path=DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER PRIMARY KEY, trip INTEGER, ignition INTEGER, running INTEGER, rpm REAL,
                v_fuel REAL, v_coolant REAL, v_oil REAL, v_aux REAL, supply_v REAL,
                fuel_pct REAL, coolant_f REAL, oil_psi REAL);
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY, start_ts INTEGER, end_ts INTEGER, engine_s REAL DEFAULT 0,
                fuel_start REAL, fuel_end REAL, refuel_gal REAL);
            CREATE TABLE IF NOT EXISTS anchors (
                ts INTEGER PRIMARY KEY, trip INTEGER, kind TEXT, voltage REAL, supply_v REAL,
                trusted INTEGER);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(samples)")}
        for column in ("v_system", "imu_jitter"):
            if column not in have:
                self.db.execute(f"ALTER TABLE samples ADD COLUMN {column} REAL")
        self.db.commit()

    # samples ---------------------------------------------------------

    def add_sample(self, row):
        self.db.execute(
            f"INSERT OR REPLACE INTO samples ({', '.join(SAMPLE_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(SAMPLE_COLUMNS))})",
            [row.get(c) for c in SAMPLE_COLUMNS])
        self.db.commit()

    def trip_samples(self, trip, column):
        return [r[0] for r in self.db.execute(
            f"SELECT {column} FROM samples WHERE trip = ? AND {column} IS NOT NULL ORDER BY ts", (trip,))]

    def recent(self, column, seconds, now=None):
        now = time.time() if now is None else now
        return [r[0] for r in self.db.execute(
            f"SELECT {column} FROM samples WHERE ts >= ? AND ignition = 1 AND {column} IS NOT NULL",
            (now - seconds,))]

    def last_sample_ts(self):
        row = self.db.execute("SELECT MAX(ts) FROM samples").fetchone()
        return row[0]

    def prune(self):
        self.db.execute("DELETE FROM samples WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
        self.db.commit()

    # trips -------------------------------------------------------------

    def open_trip(self, start_ts):
        cur = self.db.execute("INSERT INTO trips (start_ts) VALUES (?)", (int(start_ts),))
        self.db.commit()
        return cur.lastrowid

    def update_trip(self, trip, **fields):
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE trips SET {sets} WHERE id = ?", [*fields.values(), trip])
            self.db.commit()

    def trip(self, trip):
        row = self.db.execute("SELECT id, start_ts, end_ts, engine_s, fuel_start, fuel_end, refuel_gal "
                              "FROM trips WHERE id = ?", (trip,)).fetchone()
        return None if row is None else dict(zip(
            ("id", "start_ts", "end_ts", "engine_s", "fuel_start", "fuel_end", "refuel_gal"), row))

    def last_trip(self):
        row = self.db.execute("SELECT MAX(id) FROM trips").fetchone()
        return None if row[0] is None else self.trip(row[0])

    def previous_trip(self, trip):
        row = self.db.execute("SELECT MAX(id) FROM trips WHERE id < ?", (trip,)).fetchone()
        return None if row[0] is None else self.trip(row[0])

    def trips_since(self, trip_id):
        return [self.trip(r[0]) for r in self.db.execute(
            "SELECT id FROM trips WHERE id >= ? ORDER BY id", (trip_id,))]

    def last_refuel_trip(self, min_gal):
        row = self.db.execute("SELECT MAX(id) FROM trips WHERE refuel_gal >= ?", (min_gal,)).fetchone()
        return row[0]

    def first_trip_id(self):
        return self.db.execute("SELECT MIN(id) FROM trips").fetchone()[0]

    def total_engine_s(self):
        return self.db.execute("SELECT COALESCE(SUM(engine_s), 0) FROM trips").fetchone()[0]

    # anchors -----------------------------------------------------------

    def add_anchor(self, ts, trip, kind, voltage, supply, trusted):
        self.db.execute("INSERT OR REPLACE INTO anchors VALUES (?, ?, ?, ?, ?, ?)",
                        (int(ts), trip, kind, voltage, supply, int(trusted)))
        self.db.commit()

    def anchor_sessions(self, kind, limit):
        """[[supply, ...] per trip], newest trips first, trusted anchors only."""
        trips = [r[0] for r in self.db.execute(
            "SELECT DISTINCT trip FROM anchors WHERE kind = ? AND trusted = 1 ORDER BY trip DESC LIMIT ?",
            (kind, limit))]
        return [[r[0] for r in self.db.execute(
            "SELECT supply_v FROM anchors WHERE kind = ? AND trusted = 1 AND trip = ?", (kind, t))]
            for t in trips]

    # meta --------------------------------------------------------------

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else row[0]

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))
        self.db.commit()
