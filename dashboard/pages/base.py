from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout

from dashboard.widgets.page_header import PageHeader


def reading(data, sensor, key):
    """
    One value from the SensorManager output, or None if the sensor
    is offline or didn't report that value.
    """

    info = data.get(sensor) or {}
    values = info.get("data") or {}

    return values.get(key)


def online(data, sensor):
    return (data.get(sensor) or {}).get("status") == "OK"


class DashboardPage(QWidget):
    """
    Common page frame: header on top, content below with
    consistent margins and spacing.
    """

    SPACING = 18

    def __init__(self, title, subtitle=""):
        super().__init__()

        self.sensor_data = {}

        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(28, 14, 28, 24)
        self.layout_.setSpacing(self.SPACING)

        self.header = PageHeader(title, subtitle)
        self.layout_.addWidget(self.header)

    def add_card_grid(self, cards, columns):
        """
        Lay out GaugeCards in a grid that fills the page.
        """

        grid = QGridLayout()
        grid.setSpacing(self.SPACING)

        for i, card in enumerate(cards):
            grid.addWidget(card, i // columns, i % columns)

        rows = (len(cards) + columns - 1) // columns

        for r in range(rows):
            grid.setRowStretch(r, 1)
        for c in range(columns):
            grid.setColumnStretch(c, 1)

        return grid

    def update_sensors(self, data):
        self.sensor_data = data
        self.update_values()

    def update_values(self):
        pass
