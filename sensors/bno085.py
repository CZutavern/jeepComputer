import time
import math
import board
import digitalio

from adafruit_bno08x.i2c import BNO08X_I2C
from adafruit_bno08x import BNO_REPORT_ROTATION_VECTOR


RESET_PIN = board.D4        # header pin 7, to the BNO085's RST (active low)
_reset_pin = None


def reset_pin():
    """The RST line, claimed once and kept as an output driven high (not in
    reset). Never released: a floating RST can leave the IMU in reset,
    which holds SDA low and blocks every sensor on the bus."""
    global _reset_pin
    if _reset_pin is None:
        pin = digitalio.DigitalInOut(RESET_PIN)
        pin.direction = digitalio.Direction.OUTPUT
        pin.value = True
        _reset_pin = pin
    return _reset_pin


def pulse_reset():
    """Hardware reset, leaving the IMU running."""
    try:
        pin = reset_pin()
    except Exception:
        return False
    pin.value = False
    time.sleep(0.02)
    pin.value = True
    time.sleep(0.3)             # boot before anyone talks to the bus
    return True


def hold_reset():
    """Keep the IMU in reset: it lets go of the I2C bus (and draws less).
    Used while the other sensors start, in weather-station mode, and when
    it has been caught holding SDA low."""
    try:
        reset_pin().value = False
        return True
    except Exception:
        return False


class BNO085:

    def __init__(self, i2c):

        # One hardware reset (RST on GPIO 4), then let it boot. The pin
        # isn't handed to the library: its own reset talks to the chip
        # too soon after and fails with "Remote I/O error".
        last_error = None
        for attempt in range(3):
            pulse_reset()
            time.sleep(0.5)
            try:
                self.sensor = BNO08X_I2C(i2c, address=0x4A)
                self.sensor.enable_feature(BNO_REPORT_ROTATION_VECTOR)
                break
            except Exception as e:
                last_error = e
        else:
            raise last_error

        print("BNO085 ready")

    def quaternion_to_euler(self, q):

        """
        Convert quaternion to:
        heading (yaw)
        pitch
        roll

        Returns degrees.
        """

        if q is None:
            return None

        x, y, z, w = q

        # Roll
        sin_roll = 2 * (w * x + y * z)
        cos_roll = 1 - 2 * (x * x + y * y)
        roll = math.atan2(sin_roll, cos_roll)


        # Pitch
        sin_pitch = 2 * (w * y - z * x)

        if abs(sin_pitch) >= 1:
            pitch = math.copysign(math.pi / 2, sin_pitch)
        else:
            pitch = math.asin(sin_pitch)


        # Heading / yaw
        sin_yaw = 2 * (w * z + x * y)
        cos_yaw = 1 - 2 * (y * y + z * z)
        yaw = math.atan2(sin_yaw, cos_yaw)


        return {
            "heading": math.degrees(yaw) % 360,
            "pitch": math.degrees(pitch),
            "roll": math.degrees(roll)
        }


    def read(self):

        euler = self.quaternion_to_euler(
            self.sensor.quaternion
        )

        return euler