import adafruit_tsl2591

class TSL2591:
    def __init__(self, i2c):
        self.sensor = adafruit_tsl2591.TSL2591(i2c)

    def read(self):
        return {
            "lux": self.sensor.lux
        }