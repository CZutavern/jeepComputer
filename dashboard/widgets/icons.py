import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainterPath, QPen, QColor


def draw_icon(painter, name, rect, color, width=2.4):
    """
    Draw a simple line icon centered in rect.

    Icons are drawn on a 24x24 grid and scaled to fit, so they
    stay crisp at any size.
    """

    painter.save()

    size = min(rect.width(), rect.height())
    scale = size / 24.0

    painter.translate(
        rect.center().x() - size / 2,
        rect.center().y() - size / 2
    )
    painter.scale(scale, scale)

    # Width is given in screen pixels, so undo the scale
    pen = QPen(QColor(color))
    pen.setWidthF(width / scale)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)

    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    drawer = _ICONS.get(name)

    if drawer is not None:
        drawer(painter)

    painter.restore()


def _path(points, close=False):

    path = QPainterPath(QPointF(*points[0]))

    for point in points[1:]:
        path.lineTo(QPointF(*point))

    if close:
        path.closeSubpath()

    return path


def _carplay(p):
    # Screen with a play arrow
    p.drawRoundedRect(QRectF(2.5, 4.5, 19, 15), 3.5, 3.5)
    p.drawPath(_path([(10, 8.5), (15.5, 12), (10, 15.5)], close=True))


def _home(p):
    p.drawPath(_path([(3, 11), (12, 3.5), (21, 11)]))
    p.drawPath(_path([(5.5, 9.5), (5.5, 20.5), (18.5, 20.5), (18.5, 9.5)]))
    p.drawPath(_path([(10, 20.5), (10, 14.5), (14, 14.5), (14, 20.5)]))


def _weather(p):
    # Sun
    p.drawEllipse(QPointF(12, 12), 4.2, 4.2)

    for i in range(8):
        a = math.radians(i * 45)
        p.drawLine(
            QPointF(12 + math.cos(a) * 7.2, 12 + math.sin(a) * 7.2),
            QPointF(12 + math.cos(a) * 9.8, 12 + math.sin(a) * 9.8),
        )


def _offroad(p):
    # Mountains
    p.drawPath(_path([(1.5, 20), (9, 7), (13.5, 14.5)]))
    p.drawPath(_path([(10.5, 20), (16, 10.5), (22.5, 20)]))
    p.drawLine(QPointF(1.5, 20), QPointF(22.5, 20))


def _electrical(p):
    # Lightning bolt
    p.drawPath(
        _path(
            [(13.5, 2.5), (5, 13.5), (11.5, 13.5), (10.5, 21.5), (19, 10.5), (12.5, 10.5)],
            close=True,
        )
    )


def _diagnostics(p):
    # Pulse line
    p.drawPath(
        _path([(2, 12.5), (6.5, 12.5), (9, 6), (13, 19), (15.5, 10), (17, 12.5), (22, 12.5)])
    )


def _music(p):
    # Double note
    p.drawEllipse(QPointF(6.5, 18), 3, 2.5)
    p.drawEllipse(QPointF(17, 16), 3, 2.5)
    p.drawPath(_path([(9.5, 18), (9.5, 5.5), (20, 3.5), (20, 16)]))
    p.drawLine(QPointF(9.5, 9), QPointF(20, 7))


def _audio(p):
    # Speaker with sound waves
    p.drawPath(_path([(3, 9.5), (7, 9.5), (12, 5), (12, 19), (7, 14.5), (3, 14.5)], close=True))
    p.drawArc(QRectF(10.5, 8, 8, 8), -50 * 16, 100 * 16)
    p.drawArc(QRectF(8.5, 4.5, 14, 15), -55 * 16, 110 * 16)


def _exit(p):
    # Power symbol
    p.drawArc(QRectF(4, 4.5, 16, 16), 60 * 16, -300 * 16)
    p.drawLine(QPointF(12, 2.5), QPointF(12, 11))


def _settings(p):
    # Gear: eight teeth around a ring, hub in the middle
    points = []
    for i in range(8):
        a = math.radians(i * 45)
        for da, r in ((-13, 7.2), (-8, 10), (8, 10), (13, 7.2)):
            t = a + math.radians(da)
            points.append((12 + r * math.cos(t), 12 + r * math.sin(t)))
    p.drawPath(_path(points, close=True))
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)


_ICONS = {
    "carplay": _carplay,
    "home": _home,
    "weather": _weather,
    "offroad": _offroad,
    "electrical": _electrical,
    "diagnostics": _diagnostics,
    "music": _music,
    "audio": _audio,
    "exit": _exit,
    "settings": _settings,
}
