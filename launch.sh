#!/bin/bash
#
# One-click launcher (desktop icon, taskbar button, app menu): starts the
# Jeep Computer if it isn't already running. It runs as the same
# jeep-dashboard service the car starts at boot, so crash restarts and
# weather-station mode keep working.

# Already running (as the service, or started by hand)? Nothing to do.
if pgrep -u "$(id -u)" -f "jeep_computer/run_dashboard.sh" > /dev/null; then
    exit 0
fi

systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE
systemctl --user reset-failed jeep-dashboard.service 2>/dev/null
systemctl --user start jeep-dashboard.service || exec "$(dirname "$0")/run_dashboard.sh"
