import adafruit_ina23x

# Below this the bus input isn't connected to the vehicle battery (a real
# 12 V system never reads this low while the Pi is running); report no
# voltage rather than a "0.00 V" low-battery alarm.
NOT_CONNECTED_V = 1.0


class INA238:
    def __init__(self, i2c):
        self.sensor = adafruit_ina23x.INA23X(i2c)

    def read(self):
        voltage = self.sensor.bus_voltage
        connected = voltage >= NOT_CONNECTED_V
        return {
            "voltage": voltage if connected else None,
            "current": self.sensor.current if connected else None,
            "power": self.sensor.power if connected else None,
            "shunt_voltage": self.sensor.shunt_voltage,
            "connected": connected,
        }
