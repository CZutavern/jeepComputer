# Backups

`tools/backup.py` runs daily (`jeep-backup.timer`, catches up after the Jeep
has been off) and whenever the Pi reconnects to Wi-Fi
(`/etc/NetworkManager/dispatcher.d/90-jeep-home-sync`).

| What | Where |
|---|---|
| Weather history snapshot (newest 8 kept) | `~/.local/share/jeep_computer/backups/weather-DATE.db.gz` |
| Config files outside the repo | committed under `system/` ("Automatic config snapshot") |
| USB drive, if one is plugged in | `<drive>/jeep-computer-backup/`: weather snapshot + `jeep_computer.bundle` (whole repo) |
| GitHub, if a remote is set up | `git push origin HEAD` |

## Off the Pi (what survives a dead SD card)

| Copy | How | Status |
|---|---|---|
| `~/jeep-backup-latest.tar` (whole repo + newest weather + RESTORE.txt) | refreshed every backup run; copy it off from the PC: `scp pi@jeep.local:jeep-backup-latest.tar .` | works now |
| server `~/backups/jeep/jeep-backup-DATE.tar` | daily, write-only key (can't read or delete) | after `tools/stem_server.sh git` |
| server `~/backups/jeep_computer.git` | git push, git-only key | after `tools/stem_server.sh git` |
| USB drive | daily, when one is plugged in | plug one in |

## Set up GitHub (once)

```bash
sudo apt install gh
gh auth login                 # GitHub.com, HTTPS, log in with a browser/code
cd ~/jeep_computer
gh repo create jeep_computer --private --source . --push
```

After that every backup run pushes automatically.

## Restore

- Code: `git clone jeep_computer.bundle jeep_computer` (from the USB drive) or clone from GitHub.
- Weather: `gunzip -c weather-DATE.db.gz > ~/.local/share/jeep_computer/weather.db` with the dashboard stopped.
- Configs: copy the files in `system/` back to the paths listed in `system/collect.sh`.
