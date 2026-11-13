"""
Daily backup of the Jeep Computer (jeep-backup.timer).

1. Weather history and the engine log: consistent snapshots of weather.db
   and engine.db (SQLite online backup, safe while the loggers write), gzipped into
   ~/.local/share/jeep_computer/backups/, keeping the newest KEEP copies.
2. Config snapshot: runs system/collect.sh and commits any changes under
   system/ (only there; code changes are never auto-committed).
3. USB drive: if a drive is mounted under /media/<user>/, copies the newest
   weather backup and a git bundle of the whole repo (full history in one
   file; restore with `git clone jeep_computer.bundle`) into
   <drive>/jeep-computer-backup/.
4. GitHub: pushes to `origin` if a remote is configured. Never prompts.

Each step is independent; a failure in one doesn't stop the others.

Run by hand:  .venv/bin/python tools/backup.py
"""

import getpass
import json
import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = Path.home() / ".local/share/jeep_computer/weather.db"
ENGINE_DB = Path.home() / ".local/share/jeep_computer/engine.db"     # engine/store.py
BACKUP_DIR = Path.home() / ".local/share/jeep_computer/backups"
USB_ROOT = Path("/media") / getpass.getuser()
USB_FOLDER = "jeep-computer-backup"
KEEP = 8
LATEST = Path.home() / "jeep-backup-latest.tar"       # one file to copy off the Pi
GIT_KEY = Path.home() / ".ssh/jeep_git_ed25519"          # git-only key on the server
BACKUP_KEY = Path.home() / ".ssh/jeep_backup_ed25519"    # write-only key on the server
SERVER_CONFIG = Path.home() / ".config/jeep_computer/stems_sync.json"


def log(msg):
    print(msg, flush=True)


def git(*args, timeout=120):
    key = f"-i {GIT_KEY} -o IdentitiesOnly=yes " if GIT_KEY.exists() else ""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0",
               GIT_SSH_COMMAND=f"ssh {key}-o BatchMode=yes -o ConnectTimeout=15")
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                          timeout=timeout, env=env)


def backup_weather():
    return backup_db(DB, "weather")


def backup_engine():
    """Engine log (trips, fuel use, sender calibration data)."""
    return backup_db(ENGINE_DB, "engine")


def backup_db(db, name):
    if not db.exists():
        log(f"{name}: no database yet")
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    raw = BACKUP_DIR / f"{name}-{stamp}.db"
    out = BACKUP_DIR / f"{name}-{stamp}.db.gz"

    src = sqlite3.connect(str(db), timeout=30)
    dst = sqlite3.connect(str(raw))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    with open(raw, "rb") as f_in, gzip.open(out, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    raw.unlink()

    old = sorted(BACKUP_DIR.glob(f"{name}-*.db.gz"))[:-KEEP]
    for f in old:
        f.unlink()

    log(f"{name}: {out.name} ({out.stat().st_size // 1024} KiB, keeping {KEEP})")
    return out


def snapshot_config():
    collect = REPO / "system/collect.sh"
    subprocess.run([str(collect)], capture_output=True, timeout=60)
    git("add", "system")
    if git("diff", "--cached", "--quiet", "--", "system").returncode == 0:
        log("config: unchanged")
        return
    r = git("commit", "-q", "-m", "Automatic config snapshot", "--", "system")
    log("config: committed snapshot" if r.returncode == 0 else f"config: commit failed: {r.stderr.strip()}")


def usb_drives():
    if not USB_ROOT.is_dir():
        return []
    return [d for d in USB_ROOT.iterdir() if d.is_dir() and os.path.ismount(d) and os.access(d, os.W_OK)]


def copy_to_usb(db_backups):
    drives = usb_drives()
    if not drives:
        log("usb: no drive plugged in")
        return

    for drive in drives:
        target = drive / USB_FOLDER
        target.mkdir(exist_ok=True)
        for db_backup in db_backups:
            shutil.copy2(db_backup, target / db_backup.name)
        bundle = target / "jeep_computer.bundle"
        r = git("bundle", "create", str(bundle) + ".tmp", "--all")
        if r.returncode == 0:
            os.replace(str(bundle) + ".tmp", bundle)
        else:
            log(f"usb: git bundle failed: {r.stderr.strip()}")
        os.sync()
        log(f"usb: backed up to {target}")


def latest_archive(db_backups):
    """~/jeep-backup-latest.tar: the whole repo (git bundle) + the newest
    weather and engine-log snapshots + how to restore. From the PC:
        scp pi@jeep.local:jeep-backup-latest.tar ."""
    import tarfile
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "jeep_computer.bundle"
        r = git("bundle", "create", str(bundle), "--all")
        if r.returncode != 0:
            log(f"latest: git bundle failed: {r.stderr.strip()}")
            return None
        readme = Path(tmp) / "RESTORE.txt"
        readme.write_text(
            "Jeep Computer backup\n\n"
            "Code, configs (system/, camilladsp/) and full history:\n"
            "  git clone jeep_computer.bundle jeep_computer\n\n"
            "Weather history and engine log (dashboard stopped):\n"
            "  gunzip -c weather-*.db.gz > ~/.local/share/jeep_computer/weather.db\n"
            "  gunzip -c engine-*.db.gz > ~/.local/share/jeep_computer/engine.db\n\n"
            "System configs: copy system/ files back per system/collect.sh.\n")
        partial = LATEST.with_suffix(".tar.tmp")
        with tarfile.open(partial, "w") as tar:
            tar.add(bundle, arcname=bundle.name)
            tar.add(readme, arcname=readme.name)
            for db_backup in db_backups:
                tar.add(db_backup, arcname=db_backup.name)
        os.replace(partial, LATEST)
    log(f"latest: {LATEST} ({LATEST.stat().st_size // 1024} KiB)")
    return LATEST


def copy_to_server(archive):
    """The latest archive to the backup server ~/backups/jeep, over a key the
    server restricts to write-only rsync of that folder (tools/stem_server.sh)."""
    if not archive or not BACKUP_KEY.exists():
        return
    try:
        server = json.loads(SERVER_CONFIG.read_text())
    except (OSError, ValueError):
        return
    if not server.get("host") or not server.get("user"):
        return
    dated = f"jeep-backup-{time.strftime('%Y-%m-%d')}.tar"
    ssh = f"ssh -i {BACKUP_KEY} -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10"
    r = subprocess.run(["rsync", "-a", "--timeout=60", "-e", ssh, str(archive),
                        f"{server['user']}@{server['host']}:./{dated}"],
                       capture_output=True, text=True, timeout=300)
    log(f"server: {dated}" if r.returncode == 0 else
        f"server: not copied ({(r.stderr.strip().splitlines() or ['unreachable'])[-1][:120]})")


def push_github():
    if git("remote", "get-url", "origin").returncode != 0:
        log("github: no remote configured (see docs/backups.md)")
        return
    try:
        r = git("push", "-q", "origin", "HEAD", timeout=90)
    except subprocess.TimeoutExpired:
        log("github: push timed out (offline?)")
        return
    log("github: pushed" if r.returncode == 0 else f"github: push failed: {r.stderr.strip()[:200]}")


def main():
    db_backups = []
    archive = None
    steps = (backup_weather, backup_engine, snapshot_config, lambda: copy_to_usb(db_backups),
             lambda: latest_archive(db_backups), lambda: copy_to_server(archive), push_github)
    for i, step in enumerate(steps):
        try:
            result = step()
            if step in (backup_weather, backup_engine) and result:
                db_backups.append(result)
            if i == 4:
                archive = result
        except Exception as e:
            log(f"{getattr(step, '__name__', 'step')}: error: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
