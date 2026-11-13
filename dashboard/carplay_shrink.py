"""
Spot CarPlay coming up at the dongle's fallback size.

Sometimes the phone streams at 1280x720 instead of LIVI's 1920x928 (the
dongle falls back to its defaults), and LIVI draws that unscaled in the
top-left of its 1920x930 window, leaving pure black (0, 0, 0) to the right
of it and below it. A real CarPlay screen, dark mode and night dimming
included, never has those regions all exactly black (the 2026-09-25
screenshots: exactly 0 when shrunk, median 40-60 when not).
"""

from PySide6.QtGui import QImage

# Fractions of the CarPlay area (x0, x1, y0, y1). The picture ends at
# 1280/1920 = 0.67 across and 720/930 = 0.77 down. The top 12 % is left out
# for the alert banner the shell draws over CarPlay, and the middle of the
# bottom for the volume pop-up.
EMPTY_REGIONS = [
    (0.72, 0.98, 0.12, 0.95),       # right of the picture
    (0.02, 0.30, 0.82, 0.97),       # below it, left of the volume pop-up
]
PICTURE_REGION = (0.02, 0.60, 0.12, 0.70)
BLACK = 2           # letterbox pixels are exactly 0
STEP = 4            # sample every 4th pixel each way


def _brightness(image, region):
    x0, x1, y0, y1 = region
    w, h = image.width(), image.height()
    values = []
    for y in range(int(y0 * h), int(y1 * h), STEP):
        for x in range(int(x0 * w), int(x1 * w), STEP):
            c = image.pixelColor(x, y)
            values.append(max(c.red(), c.green(), c.blue()))
    return values


def picture_shrunk(path):
    """True if the screenshot shows the fallback-size picture, False if not,
    None if it can't be read (grim not finished, empty file)."""
    image = QImage(str(path))
    if image.isNull():
        return None
    for region in EMPTY_REGIONS:
        values = _brightness(image, region)
        if not values or sum(v > BLACK for v in values) > len(values) * 0.01:
            return False
    # ...and not an all-black screen (LIVI still starting): the corner the
    # picture shrinks into has something in it
    picture = sorted(_brightness(image, PICTURE_REGION))
    return bool(picture) and picture[len(picture) // 2] > 10
