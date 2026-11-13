#!/bin/sh
#
# jeep-duty-check.service (root, early boot, before the desktop).
#
# Was this boot an RTC wake in the middle of sleep cycles, with the Jeep
# still off? Then keep it cheap: cut USB power (monitor, touchscreen) and
# leave a flag that makes lightdm and CamillaDSP skip starting (their
# ExecCondition drop-ins). ups_monitor.py takes it from there: a short
# weather reading, then back to sleep. Otherwise do nothing.
#
# Test without changing anything:  sh tools/duty_check.sh --dry-run

STATE=/var/lib/jeep-ups/duty.json
FLAG=/run/jeep-ups/duty-wake
DRY=0
[ "$1" = "--dry-run" ] && DRY=1

say() { echo "duty-check: $*"; }

if [ ! -f "$STATE" ] || ! grep -q '"active": true' "$STATE"; then
    say "no sleep cycles active: normal boot"
    exit 0
fi

# X1209 power-loss detect: GPIO 6 high = vehicle power present. The X1209
# only charges on vehicle power, so this also covers "plugged in, charging".
# This early in boot the pin has no function yet and `pinctrl lev` prints
# "-", so make it an input first (as ups_monitor.py does), and only a clear
# 0 counts as "Jeep off": anything unreadable boots normally.
# (DUTY_CHECK_ASSUME_OFF=1 pretends it's off, for testing with --dry-run)
if [ "${DUTY_CHECK_ASSUME_OFF:-0}" = 1 ]; then
    level=0
else
    pinctrl set 6 ip > /dev/null 2>&1
    level=$(pinctrl lev 6 2>/dev/null)
fi
if [ "$level" != "0" ]; then
    say "vehicle power present (GPIO 6 = ${level:-?}): normal boot (the UPS monitor ends the cycles)"
    exit 0
fi

if [ "$DRY" = 1 ]; then
    say "would set $FLAG and switch USB power off"
    exit 0
fi

mkdir -p /run/jeep-ups
touch "$FLAG"
for hub in 1 2 3 4; do
    uhubctl -l "$hub" -a off > /dev/null 2>&1
done
# With no desktop nothing quits the boot splash, and plymouth-quit-wait
# would hold multi-user.target, and with it jeep-ups.service, forever.
systemctl --no-block start plymouth-quit.service
say "sleep-cycle wake: USB off, desktop and CamillaDSP skipped"
