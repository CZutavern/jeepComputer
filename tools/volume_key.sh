#!/bin/bash
#
# Media volume keys -> Jeep Computer (bound in ~/.config/labwc/rc.xml).
#   volume_key.sh up | down | mute
# Sends the key to the dashboard, which drives CamillaDSP (the volume that
# actually reaches the amps). If the dashboard isn't running, falls back to
# the desktop's own volume control.

SOCK="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/jeep-dashboard.sock"

case "$1" in
    up)   request='{"cmd": "volume", "step": 1}';  fallback=volu ;;
    down) request='{"cmd": "volume", "step": -1}'; fallback=vold ;;
    mute) request='{"cmd": "mute"}';                fallback=mute ;;
    *) echo "usage: $0 up|down|mute" >&2; exit 2 ;;
esac

if [ -S "$SOCK" ] && python3 - "$SOCK" "$request" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.settimeout(0.5)
s.connect(sys.argv[1])
s.sendall(sys.argv[2].encode() + b"\n")
s.close()
PY
then
    exit 0
fi

exec wfpanelctl volumepulse "$fallback"
