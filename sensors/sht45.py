import adafruit_sht4x

class SHT45:
    def __init__(self, i2c):
        self.sensor = adafruit_sht4x.SHT4x(i2c)

    def read(self):
        temp, humidity = self.sensor.measurements

        return {
            "temperature_c": temp,
            "temperature_f": temp * 9 / 5 + 32,
            "humidity": humidity
        }