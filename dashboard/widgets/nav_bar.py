from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QPushButton,
)
from PySide6.QtCore import Signal, QSize, QRectF, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter

from dashboard.theme import COLORS, FONTS
from dashboard.widgets.icons import draw_icon


ICON_SIZE = 26


def nav_icon(name):
    """Line icon: muted when idle, white on the active page."""

    icon = QIcon()

    for state, color in ((QIcon.Off, COLORS["text_secondary"]), (QIcon.On, COLORS["text"])):
        pixmap = QPixmap(ICON_SIZE * 2, ICON_SIZE * 2)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        draw_icon(painter, name, QRectF(0, 0, ICON_SIZE, ICON_SIZE), color, width=2.2)
        painter.end()

        icon.addPixmap(pixmap, QIcon.Normal, state)

    return icon


class NavBar(QWidget):

    BAR_HEIGHT = 90

    page_changed = Signal(str)
    exit_requested = Signal()

    def __init__(self):
        super().__init__()

        self.setFixedHeight(self.BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self.buttons = {}

        pages = [
            ("carplay", "CARPLAY"),
            ("home", "HOME"),
            ("weather", "WEATHER"),
            ("offroad", "OFF ROAD"),
            ("electrical", "ELECTRICAL"),
            ("diagnostics", "DIAGNOSTICS"),
            ("music","MUSIC"),
            ("audio", "AUDIO"),
            ("settings", "SETTINGS"),
        ]

        for page_id, text in pages:

            button = QPushButton("  " + text)
            button.setIcon(nav_icon(page_id))
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            button.setCheckable(page_id != "exit")
            button.setProperty("page", page_id)

            if page_id == "exit":
                button.clicked.connect(
                    self.exit_requested.emit
                )
            else:
                button.clicked.connect(
                    lambda checked=False, p=page_id:
                    self.page_changed.emit(p)
                )

            self.buttons[page_id] = button
            layout.addWidget(button)

        self.set_active("carplay")
        self.update_style()

    def height(self):
        return self.BAR_HEIGHT

    def set_active(self, page):

        for page_id, button in self.buttons.items():

            if page_id == "exit":
                button.setChecked(False)
            else:
                button.setChecked(page_id == page)

    def update_style(self):

        self.setStyleSheet(f"""
            QWidget {{
                background: {COLORS["nav_bar"]};
                border-top: 2px solid {COLORS["border"]};
            }}

            QPushButton {{
                background: {COLORS["card"]};
                color: {COLORS["text_secondary"]};
                border: 2px solid {COLORS["border"]};
                border-radius: 10px;
                font-size: {FONTS["small"]}px;
                font-weight: bold;
                padding: 8px;
            }}

            QPushButton:checked {{
                background: {COLORS["accent"]};
                color: {COLORS["text"]};
                border: 2px solid {COLORS["accent"]};
            }}

            QPushButton:pressed {{
                background: {COLORS["accent"]};
                color: {COLORS["text"]};
            }}
        """)
