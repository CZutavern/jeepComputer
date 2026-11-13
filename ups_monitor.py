"""
Geekworm X1209 UPS monitor (runs as root: jeep-ups.service).

Modes
  vehicle  Jeep running: everything on.
  grace    Vehicle power just went (GPIO 6 low). Wait `grace_s` so engine
           cranking / quick stops don't trigger anything.
  station  Jeep off: weather-station mode on the UPS batteries. Amps off,
           dashboard + CarPlay + CamillaDSP + the desktop stopped, USB power
           off (monitor, touchscreen), CPU throttled, and only the weather
           logger runs
           (jeep-weather-station.service). At the battery reserve it
           switches to sleep cycles (below), or shuts down.
  duty     Sleep cycles, for longer logging on a low battery: the Pi powers
           off with the RTC alarm set, wakes every `duty_interval_min`,
           records ~`duty_sample_s` of weather and powers off again, down
           to `duty_floor_*`. On each wake tools/duty_check.sh (early boot)
           cuts USB power and skips the desktop and CamillaDSP.
           Whether the X1209 keeps the Pi powered while halted (needed for
           the alarm to wake it) is recorded as "verified" in
           /var/lib/jeep-ups/duty.json after the first real wake. If it
           doesn't, a sleep behaves like the old shutdown at the reserve.
Vehicle power coming back returns to `vehicle` without a reboot.

Shutdown
  - battery critical at any time, reserve reached in station mode, or
    grace expired with station mode off / battery already low
  - the UPS cuts power after the Pi halts and restores it when vehicle
    power returns (POWER_OFF_ON_HALT=1 in the Pi 5 bootloader)
  - if vehicle power returns *during* shutdown, the system-shutdown hook
    (jeep-ups-power-restored) reboots instead of staying off

Testing without cutting power
  echo '{"percent": 80, "voltage": 3.9}' > /run/jeep-ups/simulate-vehicle-off
  (the battery values are optional; rm the file to "restore" power)
  While simulating, nothing ever really shuts down; it's only logged.

Vehicle-on signal (ups.json "vehicle_signal")
  "ups"  (default) the X1209 power-loss line, GPIO 6, as always.
  "acc"  the ignition-key state from the ACC-sense input (GPIO 10, filtered
         by sensors/acc.py: 2 s of key-off before it counts, so cranking
         dips are ignored). If the ACC input can't be read, GPIO 6 is used.
         The monitor then owns the GPIO 10 line and publishes the state to
         /run/jeep-ups/acc.json, which the dashboard's ACC sensor reads.
         Restart jeep-ups.service after changing it (the line is opened once).
station_mode and duty_cycle work the same either way.

Always-on power (UPS input fed from the house bank, ACC says when the Jeep is on)
  "Sleep" is station mode: the Pi keeps running (amps, USB, display, desktop and
  CarPlay off, weather logging on). Key back on returns to vehicle mode without a
  reboot (the display and dashboard are restarted by exit_station()). The UPS cells
  just float-charge from the external power, so their state must not decide
  anything. Recommended ups.json:
      {"vehicle_signal": "acc", "station_mode": true, "duty_cycle": false,
       "station_max_hours": 48, "grace_s": 30,
       "critical_voltage": -1, "critical_percent": -1,
       "station_reserve_percent": -1, "station_reserve_voltage": -1}
  (the -1 limits can never trigger; "duty_cycle": false because a halted Pi cannot be
  woken by ACC.) Ways the Pi can halt, in this setup:
    1. station_max_hours reached (the only safeguard for the house bank now that the
       INA238 is gone; 48 h at ~2 W). A halt_countdown_s (60 s) countdown runs first;
       the key coming on in that time cancels it (no halt command has been issued yet). AFTER THIS SHUTDOWN THE PI STAYS OFF:
       nothing wakes it while power is always on except pressing the X1209 button
       (the RTC alarm only helps with POWER_OFF_ON_HALT=0). The status file carries
       halt_warning for the last hour so the phone page can say so.
    2. critical battery / station reserve / grace expiring with station_mode off, if
       those settings are left at their defaults (the cells read low or missing).
    3. duty_cycle true (sleep cycles power the Pi off).
    4. A manual shutdown (Settings page, ssh, systemd).
  ACC unreadable falls back to GPIO 6, which is high while external power is present,
  so a broken ACC input keeps the Jeep in vehicle mode rather than halting.

Status for the dashboard: /run/jeep-ups/status.json
Settings: ~/.config/jeep_computer/ups.json
"""

import json
import os
import signal
import subprocess
import time
from collections import deque
from pathlib import Path

RUN_DIR = Path("/run/jeep-ups")
RUN_DIR.mkdir(exist_ok=True)
os.environ.setdefault("LG_WD", str(RUN_DIR))

import lgpio
from smbus2 import SMBus


STATUS_FILE = RUN_DIR / "status.json"
DUTY_FLAG = RUN_DIR / "duty-wake"              # set by tools/duty_check.sh at boot
DUTY_STATE = Path("/var/lib/jeep-ups/duty.json")
RTC_WAKEALARM = Path("/sys/class/rtc/rtc0/wakealarm")
SHUTDOWN_MARKER = RUN_DIR / "shutdown-requested"
SIMULATE_FILE = RUN_DIR / "simulate-vehicle-off"
SETTINGS_FILE = Path(f"/home/{USER}/.config/jeep_computer/ups.json")

USER = os.environ.get("JEEP_USER", "pi")
USER_RUNTIME = f"/run/user/{os.environ.get('JEEP_UID', '1000')}"
AMP_SOCKET = f"{USER_RUNTIME}/jeep-amp.sock"

# Pi 5 USB root hubs (uhubctl). The monitor is powered from a USB port,
# so cutting USB power is the biggest saving in station mode.
USB_HUBS = ("1", "2", "3", "4")

DEFAULT_SETTINGS = {
    # Seconds on battery before acting (covers engine cranking, quick
    # stops). Vehicle power coming back cancels.
    "grace_s": 30,
    # Shut down immediately below either of these while on battery
    "critical_voltage": 3.3,
    "critical_percent": 5,
    # Keep logging weather on the UPS batteries while the Jeep is off
    "station_mode": True,
    # ...until the battery gets down to this (keeps a reserve for the
    # cells' health and for the next start)
    "station_reserve_percent": 25,
    "station_reserve_voltage": 3.45,
    # Optional cap on station time in hours (null = until the reserve)
    "station_max_hours": None,
    # Sleep cycles instead of a shutdown at the reserve (see module doc)
    "duty_cycle": True,
    "duty_interval_min": 10,
    "shutdown_wake_min": 30,         # every shutdown sets an RTC alarm: with POWER_OFF_ON_HALT=0 the
                                     # X1209 keeps 5 V on, so a halted Pi only comes back by alarm (or its button)
    "duty_sample_s": 45,
    # Start sleep cycles above the reserve (e.g. 50) once a wake has been
    # verified; null = only at the reserve
    "duty_start_percent": None,
    # Stop for good (no alarm) at or below these
    "duty_floor_percent": 10,
    "duty_floor_voltage": 3.38,
    # What says "the Jeep is on": "ups" = X1209 power-loss line (GPIO 6), "acc" = key state
    # from the ACC-sense input on GPIO 10 (falls back to GPIO 6 if it can't be read)
    "vehicle_signal": "ups",
    # Station time limit reached: wait this long (key on cancels) before the halt command
    "halt_countdown_s": 60,
}
ACC_SETTINGS_FILE = Path(f"/home/{USER}/.config/jeep_computer/acc.json")
ACC_SHARED_FILE = RUN_DIR / "acc.json"
ACC_RETRY_S = 30

PLD_GPIO = 6            # high = vehicle power present, low = on battery
FUEL_GAUGE_ADDR = 0x36  # MAX17043
LOOP_S = 1.0

# Ignore power-loss for this long after boot, so a flaky start can't
# put the Pi into a shutdown loop
BOOT_HOLDOFF_S = 60


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return settings


def load_duty():
    try:
        return json.loads(DUTY_STATE.read_text())
    except (OSError, ValueError):
        return {"active": False, "wakes": 0, "verified": False}


def save_duty(state):
    DUTY_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DUTY_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    with open(tmp) as f:
        os.fsync(f.fileno())
    tmp.replace(DUTY_STATE)


def set_wakealarm(seconds):
    """RTC alarm `seconds` from now (0 = clear). Survives the power-off."""
    try:
        RTC_WAKEALARM.write_text("0")
        if seconds:
            RTC_WAKEALARM.write_text(f"+{int(seconds)}")
        return True
    except OSError as e:
        print("RTC wake alarm failed:", e, flush=True)
        return False


def uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def run(args, timeout=30):
    try:
        return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def user_systemctl(*args):
    """systemctl --user for the desktop user's services."""
    return run(["systemctl", "--user", "-M", f"{USER}@", *args])


def as_user(*args):
    """Run a command in the desktop user's Wayland session."""
    return run(["runuser", "-u", USER, "--", "env",
                f"XDG_RUNTIME_DIR={USER_RUNTIME}", "WAYLAND_DISPLAY=wayland-0", *args])


class FuelGauge:

    def __init__(self):
        self.bus = SMBus(1)

    def _word(self, register):
        w = self.bus.read_word_data(FUEL_GAUGE_ADDR, register)
        return ((w & 0xFF) << 8) | (w >> 8)

    def read(self):
        try:
            voltage = self._word(0x02) * 1.25 / 1000 / 16
            percent = min(100.0, self._word(0x04) / 256)
            return round(voltage, 3), round(percent, 1)
        except OSError:
            return None, None


class UpsMonitor:

    def __init__(self):
        self.settings = load_settings()
        self.settings_mtime = self.settings_file_mtime()
        self.duty_verified = bool(load_duty().get("verified"))

        self.gpio = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_input(self.gpio, PLD_GPIO)

        self.halt_at = None              # monotonic time of the pending station time-limit shutdown
        self.acc = None                  # sensors/acc.py reader, only for vehicle_signal "acc"
        self.acc_retry_at = 0.0

        self.gauge = FuelGauge()
        self.mode = "vehicle"
        self.on_battery_since = None
        self.station_since = None
        self.battery_history = deque(maxlen=3600)
        self.last_powered = None
        self.shutting_down = False
        self.simulated_shutdown = None
        self.saved_governor = None

    # ---------------------------------------------------------
    # Inputs
    # ---------------------------------------------------------

    @staticmethod
    def settings_file_mtime():
        try:
            return SETTINGS_FILE.stat().st_mtime
        except OSError:
            return None

    def reload_settings_if_changed(self):
        """The Settings page edits ups.json; pick changes up without a restart."""
        mtime = self.settings_file_mtime()
        if mtime != self.settings_mtime:
            self.settings_mtime = mtime
            self.settings = load_settings()
            print("Settings reloaded:", {k: self.settings[k] for k in ("station_mode", "duty_cycle")},
                  flush=True)

    def simulating(self):
        return SIMULATE_FILE.exists()

    def read_battery(self):
        """Fuel gauge, or a pretend reading while simulating:
        echo '{"percent": 80, "voltage": 3.9}' > /run/jeep-ups/simulate-vehicle-off"""
        if self.simulating():
            try:
                fake = json.loads(SIMULATE_FILE.read_text() or "{}")
                if "percent" in fake or "voltage" in fake:
                    return fake.get("voltage", 3.9), fake.get("percent", 80.0)
            except (OSError, ValueError):
                pass
        return self.gauge.read()

    def acc_key_on(self):
        """Filtered ACC key state, or None if the input can't be read."""
        try:
            if self.acc is None:
                if time.monotonic() < self.acc_retry_at:
                    return None
                self.acc_retry_at = time.monotonic() + ACC_RETRY_S
                from sensors import acc as acc_module
                try:
                    acc_settings = {**acc_module.DEFAULTS, **json.loads(ACC_SETTINGS_FILE.read_text())}
                except (OSError, ValueError):
                    acc_settings = None
                self.acc = acc_module.AccReader(acc_settings)
                print("ACC input open on GPIO", self.acc.pin, flush=True)
            state = self.acc.read()
        except Exception as e:
            if self.acc is not None:
                self.acc.close()
                self.acc = None
            print("ACC input unavailable, using GPIO 6:", e, flush=True)
            return None
        try:
            tmp = ACC_SHARED_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({**state, "time": time.time()}))
            tmp.chmod(0o644)
            tmp.replace(ACC_SHARED_FILE)
        except OSError:
            pass
        return state["key_on"]

    def vehicle_power(self):
        if self.simulating():
            return False
        from sensors.acc import pick_vehicle_power
        signal = self.settings.get("vehicle_signal", "ups")
        acc = self.acc_key_on() if signal == "acc" else None
        return pick_vehicle_power(signal, acc, lgpio.gpio_read(self.gpio, PLD_GPIO) == 1)

    # ---------------------------------------------------------
    # Actions
    # ---------------------------------------------------------

    def amps_off(self):
        """Switch the amps off before anything else so there's no pop."""

        import socket

        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(1)
                s.connect(AMP_SOCKET)
                s.sendall(b'{"cmd": "set_mode", "mode": "off"}\n')
                s.recv(4096)
        except OSError:
            pass

    def amps_auto(self):
        import socket

        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(1)
                s.connect(AMP_SOCKET)
                s.sendall(b'{"cmd": "set_mode", "mode": "auto"}\n')
                s.recv(4096)
        except OSError:
            pass

    def set_governor(self, governor):
        for path in Path("/sys/devices/system/cpu/cpufreq").glob("policy*/scaling_governor"):
            try:
                if self.saved_governor is None:
                    self.saved_governor = path.read_text().strip()
                path.write_text(governor)
            except OSError:
                pass

    def stop_dashboard(self):
        user_systemctl("stop", "jeep-dashboard.service")

        # In case it was started outside the service (e.g. by hand)
        for pattern in ("jeep_computer/run_dashboard[.]sh", "shell/jeep_shell[.]py",
                        "jeep_computer/main[.]py", "LIVI[.]AppImage", "LIVI-lowlatency/livi( |$)"):
            run(["pkill", "-u", USER, "-f", pattern])

    def usb_power(self, on):
        for hub in USB_HUBS:
            run(["uhubctl", "-l", hub, "-a", "on" if on else "off"], timeout=15)

    def enter_station(self):
        print("Entering weather-station mode (vehicle off)", flush=True)

        self.amps_off()
        self.stop_dashboard()
        run(["systemctl", "stop", "camilladsp"])

        # Stop the whole desktop before the monitor loses power: re-adding
        # a display to a running desktop crashes it on this Pi, so the
        # desktop is started fresh when the Jeep comes back on instead.
        run(["systemctl", "stop", "lightdm"])
        self.usb_power(False)

        self.set_governor("powersave")
        user_systemctl("start", "jeep-weather-station.service")

        self.mode = "station"
        self.station_since = time.monotonic()
        self.battery_history.clear()

    def exit_station(self):
        print("Vehicle power back: leaving weather-station mode", flush=True)

        user_systemctl("stop", "jeep-weather-station.service")
        self.set_governor(self.saved_governor or "ondemand")
        self.saved_governor = None

        self.usb_power(True)
        time.sleep(2)            # let the monitor and touchscreen come up
        run(["systemctl", "start", "camilladsp"])

        # Fresh desktop session (auto-login); its autostart starts the
        # dashboard service.
        run(["systemctl", "start", "lightdm"])
        self.amps_auto()

        self.mode = "vehicle"
        self.station_since = None

    def shutdown(self, reason):
        if self.simulating():
            if self.simulated_shutdown != reason:
                print(f"SIMULATION: would shut down now ({reason})", flush=True)
                self.simulated_shutdown = reason
            return

        if self.shutting_down:
            return

        self.shutting_down = True
        self.mode = "shutting_down"
        print(f"Shutting down: {reason}", flush=True)

        self.amps_off()
        user_systemctl("stop", "jeep-weather-station.service")   # flushes the last minute
        # The X1209 no longer cuts power on halt (POWER_OFF_ON_HALT=0, so sleep cycles can
        # wake), so without an alarm the Pi would stay halted even after the Jeep starts.
        set_wakealarm(int(self.settings["shutdown_wake_min"] * 60))
        SHUTDOWN_MARKER.write_text(reason)
        subprocess.run(["systemctl", "poweroff"], check=False)

    # ---------------------------------------------------------
    # Sleep cycles
    # ---------------------------------------------------------

    def at_duty_floor(self, voltage, percent):
        s = self.settings
        return ((percent is not None and percent <= s["duty_floor_percent"])
                or (voltage is not None and voltage <= s["duty_floor_voltage"]))

    def duty_sleep(self, reason):
        """Power off with the RTC alarm set for the next reading."""
        interval = int(self.settings["duty_interval_min"] * 60)

        if self.simulating():
            if self.simulated_shutdown != reason:
                print(f"SIMULATION: would sleep {interval // 60} min ({reason})", flush=True)
                self.simulated_shutdown = reason
            return

        if self.shutting_down:
            return
        self.shutting_down = True
        self.mode = "duty_sleep"
        print(f"Sleeping {interval // 60} min: {reason}", flush=True)

        self.amps_off()
        user_systemctl("stop", "jeep-weather-station.service")   # flushes the last minute

        state = load_duty()
        state.update(active=True, interval_s=interval, reason=reason,
                     last_sleep=time.time(), next_wake=time.time() + interval)
        save_duty(state)

        if not set_wakealarm(interval):
            state["active"] = False
            save_duty(state)
        SHUTDOWN_MARKER.write_text(f"sleep cycle: {reason}")
        subprocess.run(["systemctl", "poweroff"], check=False)

    def end_duty(self):
        state = load_duty()
        if state.get("active"):
            state["active"] = False
            state["ended"] = time.time()
            save_duty(state)
        set_wakealarm(0)
        DUTY_FLAG.unlink(missing_ok=True)

    def duty_wake(self):
        """
        Booted by the RTC alarm with the Jeep off (tools/duty_check.sh set
        DUTY_FLAG and kept the desktop, CamillaDSP and USB off). Record a
        short burst of weather, then sleep again, or carry on normally if
        vehicle power is back.
        """
        s = self.settings
        state = load_duty()
        state["wakes"] = state.get("wakes", 0) + 1
        if not self.simulating():
            state["verified"] = True    # the X1209 kept the Pi powered: the alarm works
        state["last_wake"] = time.time()
        save_duty(state)
        print(f"Sleep-cycle wake #{state['wakes']}", flush=True)

        self.mode = "duty"
        self.set_governor("powersave")
        start = time.monotonic()
        sampling_since = None

        while True:
            now = time.monotonic()
            voltage, percent = self.read_battery()

            if self.vehicle_power():
                print("Vehicle power back during a sleep-cycle wake", flush=True)
                self.end_duty()
                self.mode = "station"          # exit_station() brings everything up
                self.exit_station()
                return

            if sampling_since is None and user_systemctl("start", "jeep-weather-station.service"):
                sampling_since = now
            done = sampling_since is not None and now - sampling_since >= s["duty_sample_s"]
            if done or now - start > s["duty_sample_s"] + 90:
                break

            self.write_status(vehicle_power=False, simulated=self.simulating(), mode="duty",
                              battery_voltage=voltage, battery_percent=percent,
                              on_battery_s=None, grace_action=None, shutdown_in=None,
                              station_hours=None, hours_left=None,
                              shutting_down=False, armed=True, duty=state)
            time.sleep(LOOP_S)

        user_systemctl("stop", "jeep-weather-station.service")    # flush this minute
        voltage, percent = self.read_battery()
        if self.at_duty_floor(voltage, percent):
            self.end_duty()
            self.shutdown(f"sleep cycles: battery floor reached ({voltage} V, {percent} %)")
        else:
            self.duty_sleep(f"sleep cycle ({voltage} V, {percent} %)")

    # ---------------------------------------------------------
    # Station runtime estimate
    # ---------------------------------------------------------

    def battery_trend(self, now):
        """Battery % per hour over the last 30 min (needs 10 min of history,
        which is cleared whenever charging starts or stops), or None."""
        recent = [(t, p) for t, p in self.battery_history if now - t <= 1800]
        if len(recent) < 2 or now - recent[0][0] < 600:
            return None
        return (recent[-1][1] - recent[0][1]) / ((now - recent[0][0]) / 3600)

    def hours_left(self, now, percent):
        """Hours until the reserve, from the recent battery % trend."""
        rate = self.battery_trend(now)
        if percent is None or rate is None or rate >= -0.05:
            return None
        return max(0.0, (percent - self.settings["station_reserve_percent"]) / -rate)

    # ---------------------------------------------------------

    def step(self):
        self.reload_settings_if_changed()
        now = time.monotonic()
        powered = self.vehicle_power()
        voltage, percent = self.read_battery()
        s = self.settings

        if powered != self.last_powered:
            self.battery_history.clear()        # charging <-> discharging
            self.last_powered = powered
        if percent is not None:
            self.battery_history.append((now, percent))

        if powered:
            if self.on_battery_since is not None:
                print("Vehicle power restored", flush=True)
            self.on_battery_since = None
            self.simulated_shutdown = None
            if self.halt_at is not None:
                print("Key on: station time-limit shutdown cancelled", flush=True)
                self.halt_at = None
            if self.mode == "duty":
                self.end_duty()
                self.exit_station()
            elif self.mode == "station":
                self.exit_station()
            elif self.mode == "grace":
                self.mode = "vehicle"
        elif self.on_battery_since is None:
            self.on_battery_since = now
            if self.mode == "vehicle":
                self.mode = "grace"
            print("Vehicle power lost, running on battery", flush=True)

        on_battery_for = 0 if powered else now - self.on_battery_since
        armed = uptime() >= BOOT_HOLDOFF_S
        action_in = None
        grace_action = None
        hours_left = None

        battery_low = (
            (percent is not None and percent <= s["station_reserve_percent"])
            or (voltage is not None and voltage <= s["station_reserve_voltage"])
        )

        if not powered and armed:
            critical = (
                (voltage is not None and voltage < s["critical_voltage"])
                or (percent is not None and percent < s["critical_percent"])
            )

            if critical:
                self.shutdown(f"battery critical ({voltage} V, {percent} %)")

            elif self.mode == "grace":
                if s["station_mode"] and not battery_low:
                    grace_action = "station"
                elif s["duty_cycle"] and not self.at_duty_floor(voltage, percent):
                    grace_action = "sleep"      # already low: straight to sleep cycles
                else:
                    grace_action = "shutdown"
                action_in = max(0, s["grace_s"] - on_battery_for)
                if action_in <= 0:
                    if grace_action == "station":
                        self.enter_station()
                    elif grace_action == "sleep":
                        self.duty_sleep(f"vehicle off, battery already low ({voltage} V, {percent} %)")
                    else:
                        self.shutdown(f"vehicle power off for {s['grace_s']} s")

            elif self.mode == "station":
                hours_left = self.hours_left(now, percent)
                station_hours = (now - self.station_since) / 3600
                max_hours = s.get("station_max_hours")

                duty_start = s["duty_start_percent"]
                duty_now = s["duty_cycle"] and (
                    battery_low or (duty_start is not None and percent is not None
                                    and percent <= duty_start))

                if duty_now and not self.at_duty_floor(voltage, percent):
                    self.duty_sleep(f"station mode: battery {voltage} V, {percent} %")
                elif battery_low:
                    self.shutdown(f"station mode: battery reserve reached ({voltage} V, {percent} %)")
                elif max_hours and station_hours >= max_hours:
                    if self.halt_at is None:
                        self.halt_at = now + s["halt_countdown_s"]
                        print(f"Station time limit ({max_hours} h) reached: shutting down in "
                              f"{s['halt_countdown_s']} s unless the key comes on", flush=True)
                    elif now >= self.halt_at:
                        self.shutdown(f"station mode: {max_hours} h limit reached; the Pi stays OFF "
                                      f"until the X1209 button is pressed (power is always on)")

        station_hours_now = None if self.station_since is None else (now - self.station_since) / 3600
        max_h = s.get("station_max_hours")
        limit_in_h = None
        halt_warning = None
        if self.mode == "station" and max_h and station_hours_now is not None:
            limit_in_h = max(0.0, max_h - station_hours_now)
            if self.halt_at is not None:
                halt_warning = (f"Shutting down in {max(0, self.halt_at - now):.0f} s (station time limit): the Pi "
                                f"stays OFF until the X1209 button is pressed. Turn the key to cancel.")
            elif limit_in_h <= 1.0:
                halt_warning = (f"Station time limit in {limit_in_h * 60:.0f} min: the Pi will shut down and "
                                f"stay OFF until the X1209 button is pressed (power is always on)")

        rate = self.battery_trend(now)
        hours_to_full = None
        if powered and rate is not None and rate > 0.5 and percent is not None and percent < 99:
            hours_to_full = round((100 - percent) / rate, 1)

        self.write_status(
            vehicle_power=powered,
            simulated=self.simulating(),
            mode=self.mode,
            battery_voltage=voltage,
            battery_percent=percent,
            on_battery_s=round(on_battery_for, 1),
            grace_action=grace_action,
            # kept for the dashboard: seconds until the grace action
            shutdown_in=None if action_in is None else round(action_in, 1),
            station_hours=None if self.station_since is None
                          else round((now - self.station_since) / 3600, 2),
            hours_left=None if hours_left is None else round(hours_left, 1),
            # %/h, + while charging; None until 10 min of history
            charge_rate=None if rate is None else round(rate, 1),
            hours_to_full=hours_to_full,
            shutting_down=self.shutting_down,
            armed=armed,
            station_mode=self.settings["station_mode"],
            station_max_hours=max_h,
            station_limit_in_h=None if limit_in_h is None else round(limit_in_h, 2),
            halt_warning=halt_warning,
            vehicle_signal=self.settings.get("vehicle_signal", "ups"),
            duty_cycle=self.settings["duty_cycle"],
            duty_verified=self.duty_verified,
        )

    def write_status(self, **status):
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"time": time.time(), **status}))
        tmp.chmod(0o644)
        tmp.replace(STATUS_FILE)

    def run(self):
        print(f"UPS monitor running (grace {self.settings['grace_s']} s, "
              f"station mode {'on' if self.settings['station_mode'] else 'off'})", flush=True)
        SHUTDOWN_MARKER.unlink(missing_ok=True)

        try:
            if DUTY_FLAG.exists():
                self.duty_wake()
            elif load_duty().get("active"):
                # Normal boot (vehicle power was back at boot): cycles are over
                print("Sleep cycles ended: vehicle power at boot", flush=True)
                self.end_duty()

            while True:
                self.step()
                time.sleep(LOOP_S)
        finally:
            lgpio.gpiochip_close(self.gpio)


def main():
    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    UpsMonitor().run()


if __name__ == "__main__":
    main()
