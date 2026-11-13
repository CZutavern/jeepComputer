"""
Minute-by-minute weather history in SQLite, so trends survive the Jeep
being switched off. One row per minute; 60 days are kept.
"""

import sqlite3
import time
from pathlib import Path

from weather.analysis import Sample


DB_PATH = Path.home() / ".local/share/jeep_computer/weather.db"
KEEP_DAYS = 60


class WeatherStore:

    def __init__(self, path=DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)

        # Each thread opens its own store (SQLite connections aren't shared)
        self.db = sqlite3.connect(str(path), timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS minute (
                ts        INTEGER PRIMARY KEY,
                pressure  REAL,
                temp_c    REAL,
                humidity  REAL,
                lux       REAL,
                moving    INTEGER,
                source    TEXT
            )
        """)
        # Older databases: 'source' marks imported rows (weather/seed.py);
        # the Jeep's own readings leave it NULL.
        columns = [row[1] for row in self.db.execute("PRAGMA table_info(minute)")]
        if "source" not in columns:
            self.db.execute("ALTER TABLE minute ADD COLUMN source TEXT")
        self.db.commit()

    def add(self, sample):
        self.db.execute(
            "INSERT OR REPLACE INTO minute (ts, pressure, temp_c, humidity, lux, moving) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(sample.ts), sample.pressure, sample.temp_c, sample.humidity, sample.lux,
             None if sample.moving is None else int(sample.moving)),
        )
        self.db.commit()

    def prune(self):
        self.db.execute("DELETE FROM minute WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
        self.db.commit()

    def since(self, seconds):
        rows = self.db.execute(
            "SELECT ts, pressure, temp_c, humidity, lux, moving FROM minute "
            "WHERE ts >= ? ORDER BY ts",
            (time.time() - seconds,),
        ).fetchall()

        return [
            Sample(ts, p, t, h, lux, None if mv is None else bool(mv))
            for ts, p, t, h, lux, mv in rows
        ]
