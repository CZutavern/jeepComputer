import time

import board

from .mcp9808 import MCP9808
from .sht45 import SHT45
from .bmp388 import BMP388
from .tsl2591 import TSL2591
from .ads1115 import ADS1115
from .bno085 import BNO085, hold_reset
from .tach import Tach
from .acc import open_acc
from . import battery


class SensorManager:

    ALL_SENSORS = [
        ("imu", BNO085),
        ("amp_box", MCP9808),       # amplifier enclosure temperature
        ("outside", SHT45),
        ("pressure", BMP388),       # also the cabin temperature
        ("light", TSL2591),
        # The INA238 power monitor ("power") is retired (burned out 2026-10); sensors/ina238.py is kept, unused.
        ("analog", ADS1115),
    ]

    # The BNO085 can hold SDA low and block every other sensor (seen when its
    # INT or PS pins are miswired). It is started last, and if the bus locks
    # up it is held in reset (which frees the bus) and retried later.
    BUS_STUCK_READS = 3             # every I2C sensor failing this many reads in a row
    IMU_RETRY_S = 30 * 60
    IMU_BLOCKING = "holding the I2C bus low (check its SDA/INT/P0/P1 wiring)"

    def __init__(self, only=None):
        """only: sensor names to use (e.g. just the weather sensors)."""

        self.i2c = board.I2C()

        self.sensors = {}
        self.bus_failures = 0
        self.imu_blocked_until = 0.0

        # IMU in reset (off the bus) while the others start. In weather-
        # station mode it isn't used and simply stays there.
        hold_reset()
        wanted = [(n, c) for n, c in self.ALL_SENSORS if only is None or n in only]

        for name, sensor_class in wanted:
            if name != "imu":
                self._add_sensor(name, sensor_class)
        if any(name == "imu" for name, _ in wanted):
            self._start_imu()

        # Tachometer: GPIO edge counter, not an I2C device
        self.tach = None
        if only is None or "tach" in only:
            try:
                self.tach = Tach()
            except Exception as e:
                print("tach: FAILED -", e, flush=True)

        # Ignition-key state: GPIO input, not an I2C device
        self.acc = None
        self.acc_error = None
        if only is None or "acc" in only:
            try:
                self.acc = open_acc()
            except Exception as e:
                self.acc_error = str(e)
                print("acc: FAILED -", e, flush=True)


    def _add_sensor(self, name, sensor_class, quiet=False):
        try:
            sensor = sensor_class(self.i2c)

            self.sensors[name] = {
                "sensor": sensor,
                "class": sensor_class,
                "status": "OK",
                "error": None
            }

            print(f"{name}: OK")

        except Exception as e:
            self.sensors[name] = {
                "sensor": None,
                "class": sensor_class,
                "status": "ERROR",
                "error": str(e)
            }

            if not quiet:
                print(f"{name}: FAILED - {e}")


    def retry_failed(self):
        """
        Try again to start any sensor that failed, so a loose
        connector or slow sensor at boot doesn't need a restart.
        """

        for name, info in list(self.sensors.items()):
            if info["sensor"] is None and name != "imu":
                self._add_sensor(name, info["class"], quiet=True)

        imu = self.sensors.get("imu")
        if imu and imu["sensor"] is None and time.monotonic() >= self.imu_blocked_until:
            self._start_imu(quiet=True)

    def _other_sensor_answers(self):
        """True if a running non-IMU sensor can still be read (bus is free)."""
        others = [info["sensor"] for n, info in self.sensors.items()
                  if n != "imu" and info["sensor"] is not None]
        if not others:
            return None             # nothing to judge by
        for sensor in others:
            try:
                sensor.read()
                return True
            except Exception:
                pass
        return False

    def _start_imu(self, quiet=False):
        self._add_sensor("imu", BNO085, quiet=quiet)
        if self._other_sensor_answers() is False:
            self._block_imu()

    def _block_imu(self):
        hold_reset()
        self.imu_blocked_until = time.monotonic() + self.IMU_RETRY_S
        self.sensors["imu"].update(sensor=None, status="ERROR", error=self.IMU_BLOCKING)
        print(f"imu: {self.IMU_BLOCKING}; held in reset, retry in "
              f"{self.IMU_RETRY_S // 60} min", flush=True)


    def read_all(self):
        """
        Full diagnostic output.
        Includes status and errors.
        Useful for troubleshooting.
        """

        data = {}

        for name, info in self.sensors.items():

            if info["sensor"] is None:
                data[name] = {
                    "status": "ERROR",
                    "error": info["error"],
                    "data": None
                }
                continue

            try:
                reading = info["sensor"].read()

                data[name] = {
                    "status": "OK",
                    "error": None,
                    "data": reading
                }

            except Exception as e:
                data[name] = {
                    "status": "ERROR",
                    "error": str(e),
                    "data": None
                }

        self._check_bus(data)
        return data

    def _check_bus(self, data):
        """If every running sensor fails at once, the bus is stuck: take the
        IMU (the usual culprit) off it by holding it in reset."""
        live = [n for n, info in self.sensors.items() if info["sensor"] is not None]
        if not live or any(data[n]["status"] == "OK" for n in live):
            self.bus_failures = 0
            return

        self.bus_failures += 1
        if self.bus_failures < self.BUS_STUCK_READS:
            return

        self.bus_failures = 0
        print("I2C bus stuck", flush=True)
        if "imu" in self.sensors:
            self._block_imu()
        else:
            hold_reset()


    def get_data(self):
        """
        Clean sensor data for the dashboard.

        Always returns a dictionary for each sensor so the dashboard
        continues running even when sensors are disconnected.
        """

        raw = self.read_all()

        clean = {}

        for name, sensor in raw.items():

            if sensor["status"] == "OK":
                clean[name] = {
                    "status": "OK",
                    "error": None,
                    "data": sensor["data"]
                }

            else:
                clean[name] = {
                    "status": "ERROR",
                    "error": sensor["error"],
                    "data": {}
                }

        # No spark seen yet = not wired (or engine never started)
        if self.tach is not None:
            reading = self.tach.read()
            wired = reading["rpm"] is not None
            clean["tach"] = {
                "status": "OK" if wired else "ERROR",
                "error": None if wired else "no ignition signal",
                "data": reading if wired else {},
            }

        # Starting battery: ADS1115 input A3 through the 100k/22k divider
        if "analog" in clean:
            analog = clean["analog"]
            a3 = ((analog.get("data") or {}).get("A3") or {}).get("voltage") if analog["status"] == "OK" else None
            volts = battery.reading(a3) if a3 is not None else None
            good = volts is not None and volts["voltage"] is not None
            clean["battery"] = {
                "status": "OK" if good else "ERROR",
                "error": None if good else ("no A3 reading" if volts is None else "battery not connected to A3 (or out of range)"),
                "data": volts or {},
            }

        # Ignition key (ACC sense)
        if self.acc is not None or self.acc_error is not None:
            clean["acc"] = self._read_acc()

        return clean

    def _read_acc(self):
        if self.acc is None:
            return {"status": "ERROR", "error": self.acc_error, "data": {}}
        try:
            state = self.acc.read()
        except Exception as e:
            return {"status": "ERROR", "error": str(e), "data": {}}
        return {"status": "OK", "error": None, "data": state}


    def get_status(self):
        """
        Returns sensor health information.
        Useful for diagnostics page.
        """

        status = {}

        for name, info in self.sensors.items():
            status[name] = {
                "status": info["status"],
                "error": info["error"]
            }

        return status