import board
import adafruit_mcp9808

class MCP9808:
    def __init__(self, i2c):
        self.sensor = adafruit_mcp9808.MCP9808(i2c)

    def read(self):
        return {
            "temperature_c": self.sensor.temperature,
            "temperature_f": self.sensor.temperature * 9 / 5 + 32
        }