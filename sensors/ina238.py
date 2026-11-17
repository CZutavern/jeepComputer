import adafruit_ina23x

# Below this the bus input isn't connected to the vehicle battery (a real
# 12 V system never reads this low while the Pi is running); report no
# voltage rather than a "0.00 V" low-battery alarm.
NOT_CONNECTED_V = 1.0

# The shunt is damaged/removed (2026-10-15), so only the bus voltage on VIN+ is
# used: current and power would be meaningless. Set False once a shunt is fitted.
VOLTAGE_ONLY = True


class INA238:
    def __init__(self, i2c):
        self.sensor = adafruit_ina23x.INA23X(i2c)

    def read(self):
        voltage = self.sensor.bus_voltage
        connected = voltage >= NOT_CONNECTED_V
        return {
            "voltage": voltage if connected else None,
            "current": None if VOLTAGE_ONLY or not connected else self.sensor.current,
            "power": None if VOLTAGE_ONLY or not connected else self.sensor.power,
            "shunt_voltage": None if VOLTAGE_ONLY else self.sensor.shunt_voltage,
            "connected": connected,
        }
