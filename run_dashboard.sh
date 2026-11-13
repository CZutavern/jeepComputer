#!/bin/bash
#
# Keeps the Jeep Computer running.
#
#   Shell mode (default): shell/jeep_shell.py is a full-screen compositor
#   that runs the dashboard and CarPlay (LIVI) inside it, so CarPlay can be
#   placed anywhere in the UI. The shell restarts the dashboard itself if
#   it crashes.
#
#   Classic mode: the dashboard runs directly on the desktop with LIVI as a
#   separate docked window. Used automatically if the shell can't start,
#   or always if ~/.config/jeep_computer/no-shell exists.
#
#   - restarts after a crash
#   - stops when you press EXIT (clean exit code 0)
#   - stops when the desktop session ends
#
# Output goes to ~/.cache/jeep_computer/dashboard.log

cd "$(dirname "$0")" || exit 1

LOG_DIR="$HOME/.cache/jeep_computer"
LOG="$LOG_DIR/dashboard.log"
mkdir -p "$LOG_DIR"

# The desktop session is alive while *our* labwc runs. (A stale socket
# file can outlive a crash, and the login screen's labwc runs as another
# user, so neither counts.)
session_alive() {
    pgrep -u "$(id -u)" -x labwc > /dev/null
}

USE_SHELL=1
[ -f "$HOME/.config/jeep_computer/no-shell" ] && USE_SHELL=0

# Shell failures in a row that happened within seconds of starting.
# Three of those and we fall back to the classic layout for this session.
QUICK_SHELL_FAILURES=0

while true; do
    # Keep the log from growing forever
    if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1048576 ]; then
        mv -f "$LOG" "$LOG.1"
    fi

    started=$(date +%s)

    if [ "$USE_SHELL" = 1 ]; then
        echo "=== $(date '+%F %T') starting shell" >> "$LOG"
        LD_LIBRARY_PATH="$PWD/vendor/qtwayland/lib" \
            .venv/bin/python -u shell/jeep_shell.py >> "$LOG" 2>&1
        code=$?

        if [ "$code" -ne 0 ] && [ $(( $(date +%s) - started )) -lt 10 ]; then
            QUICK_SHELL_FAILURES=$((QUICK_SHELL_FAILURES + 1))
        else
            QUICK_SHELL_FAILURES=0
        fi

        if [ "$code" -eq 3 ] || [ "$QUICK_SHELL_FAILURES" -ge 3 ]; then
            echo "=== $(date '+%F %T') shell could not start (exit $code), using classic layout" >> "$LOG"
            USE_SHELL=0
            continue
        fi
    else
        echo "=== $(date '+%F %T') starting dashboard (classic)" >> "$LOG"
        .venv/bin/python -u main.py >> "$LOG" 2>&1
        code=$?
    fi

    if [ "$code" -eq 0 ]; then
        echo "=== $(date '+%F %T') exited normally" >> "$LOG"
        break
    fi

    sleep 1
    if ! session_alive; then
        echo "=== $(date '+%F %T') desktop session gone, not restarting" >> "$LOG"
        break
    fi

    echo "=== $(date '+%F %T') crashed (exit $code), restarting" >> "$LOG"

    # Back off if it is crashing straight away
    if [ $(( $(date +%s) - started )) -lt 10 ]; then
        sleep 10
    else
        sleep 2
    fi
done
