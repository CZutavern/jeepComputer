#!/usr/bin/env python3
"""
Start the fuel / engine-use learning over.

    .venv/bin/python tools/reset_fuel_learning.py            # dry run: shows what would happen
    .venv/bin/python tools/reset_fuel_learning.py --apply    # does it

What --apply does, in one SQLite transaction (safe while the dashboard runs):
  1. backs up engine.db first to ~/backups/engine-before-fuel-reset-<date>.db
     (SQLite's online backup, so the copy is consistent)
  2. copies every row of `trips` (the open one too) into `trips_archive_<date>`
     and deletes them from `trips`: engine hours, gal/h and the fill-up
     detection start from zero. The logger opens a new trip at the next ignition.
  3. deletes the meta keys mpg_odometer_start, mpg_start_ts, mpg_fuel_start_pct
     and sets reset_ts to now.
`samples` and `anchors` are kept: they calibrate the gauge supply, not fuel.
A running dashboard keeps its in-memory trip until the ignition has been off
for 2 minutes; its updates to the deleted row do nothing.
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path.home() / ".local/share/jeep_computer/engine.db"
BACKUP_DIR = Path.home() / "backups"
META_KEYS = ("mpg_odometer_start", "mpg_start_ts", "mpg_fuel_start_pct")


def backup(db_path, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise SystemExit(f"{dest} already exists: refusing to overwrite a backup")
    src = sqlite3.connect(str(db_path), timeout=30)
    out = sqlite3.connect(str(dest))
    with out:
        src.backup(out)
    out.close()
    src.close()


def reset(db_path, archive, now=None, apply=True):
    """Archive and clear the trips. Returns (trips moved, meta keys removed)."""
    now = int(time.time() if now is None else now)
    db = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        trips = db.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        keys = [k for (k,) in db.execute(
            f"SELECT key FROM meta WHERE key IN ({','.join('?' * len(META_KEYS))})", META_KEYS)]
        if apply:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (archive,)).fetchone()
            if exists:
                raise SystemExit(f"{archive} already exists: the reset was done already")
            db.execute(f"CREATE TABLE {archive} AS SELECT * FROM trips WHERE 0")
            db.execute(f"INSERT INTO {archive} SELECT * FROM trips")
            copied = db.execute(f"SELECT COUNT(*) FROM {archive}").fetchone()[0]
            if copied != trips:
                raise RuntimeError(f"archived {copied} of {trips} trips")
            db.execute("DELETE FROM trips")
            db.execute(f"DELETE FROM meta WHERE key IN ({','.join('?' * len(META_KEYS))})", META_KEYS)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('reset_ts', ?)", (str(now),))
            db.execute("COMMIT")
        else:
            db.execute("ROLLBACK")
        return trips, keys
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="do it (default: dry run)")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d")
    archive = f"trips_archive_{stamp}"
    dest = BACKUP_DIR / f"engine-before-fuel-reset-{time.strftime('%Y-%m-%d')}.db"
    if not DB_PATH.exists():
        print("No engine.db at", DB_PATH)
        return 1

    if args.apply:
        backup(DB_PATH, dest)
        print("Backup:", dest, f"({dest.stat().st_size} bytes)")
    trips, keys = reset(DB_PATH, archive, apply=args.apply)
    verb = "Archived" if args.apply else "Would archive"
    print(f"{verb} {trips} trips into {archive} and clear them from trips")
    print(f"{'Removed' if args.apply else 'Would remove'} meta keys: {', '.join(keys) or '(none)'}; reset_ts = now")
    if not args.apply:
        print("Dry run only. Add --apply to do it (the backup is made first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
