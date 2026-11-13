from PySide6.QtWidgets import (
QWidget,
QHBoxLayout,
QLabel,
)
from PySide6.QtCore import Qt, QTimer, QTime

from dashboard.theme import COLORS, FONTS

class TopBar(QWidget):


    BAR_HEIGHT = 60

    def __init__(self):
        super().__init__()

        self.setFixedHeight(self.BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 5, 15, 5)
        layout.setSpacing(15)

        # Outside temperature
        self.temperature_label = QLabel("-- °F")
        self.temperature_label.setAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )

        # Time
        self.time_label = QLabel("--:--")
        self.time_label.setAlignment(
            Qt.AlignCenter | Qt.AlignVCenter
        )

        # Voltage
        self.voltage_label = QLabel("-- V")
        self.voltage_label.setAlignment(
            Qt.AlignRight | Qt.AlignVCenter
        )

        layout.addWidget(self.temperature_label, 1)
        layout.addWidget(self.time_label, 1)
        layout.addWidget(self.voltage_label, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_time)
        self.timer.start(1000)

        self.update_time()
        self.update_style()

    def height(self):
        return self.BAR_HEIGHT

    def update_time(self):
        self.time_label.setText(
            QTime.currentTime().toString("h:mm AP")
        )

    def set_temperature(self, temperature):
        if temperature is None:
            self.temperature_label.setText("-- °F")
        else:
            self.temperature_label.setText(
                f"{float(temperature):.1f} °F"
            )

    def set_voltage(self, voltage):
        if voltage is None:
            self.voltage_label.setText("-- V")
        else:
            self.voltage_label.setText(
                f"{float(voltage):.2f} V"
            )

    def update_style(self):

        self.setStyleSheet(f"""
            QWidget {{
                background: {COLORS["top_bar"]};
                border-bottom: 2px solid {COLORS["border"]};
            }}

            QLabel {{
                background: transparent;
                border: none;
                color: {COLORS["text"]};
                font-size: {FONTS["title"]}px;
                font-weight: bold;
            }}
        """)
