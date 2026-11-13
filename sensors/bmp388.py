import adafruit_bmp3xx

class BMP388:
    def __init__(self, i2c):
        self.sensor = adafruit_bmp3xx.BMP3XX_I2C(i2c)

    def read(self):
        return {
            "temperature_c": self.sensor.temperature,
            "pressure": self.sensor.pressure,
            "altitude": self.sensor.altitude
        }