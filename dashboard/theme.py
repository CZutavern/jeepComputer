from PySide6.QtGui import QFont


COLORS = {
    # Deep navy base
    "background": "#050a14",
    "background_top": "#0a1426",
    "card": "#0c1628",
    "card_alt": "#12203a",
    "card_top": "#13223e",
    "top_bar": "#08101f",
    "nav_bar": "#08101f",
    "border": "#1d2d4a",
    "border_bright": "#2c4570",

    "text": "#eef4ff",
    "text_secondary": "#8ea4c8",
    "muted": "#50648a",

    # Blue accent family
    "accent": "#2f8cff",
    "accent_bright": "#6cb4ff",
    "accent_dark": "#1a4fa8",

    "good": "#2fd49a",
    "warning": "#ffb42e",
    "critical": "#ff4d61",
}


STATUS_COLORS = {
    "normal": COLORS["accent"],
    "good": COLORS["good"],
    "warning": COLORS["warning"],
    "critical": COLORS["critical"],
    "offline": COLORS["muted"],
}


def format_value(value, decimals=1):
    """
    Format a sensor reading, showing "--" when it is missing.
    """

    if value is None:
        return "--"

    if isinstance(value, str):
        return value

    return f"{value:.{decimals}f}"


FONT_FAMILY = "Nunito Sans"


FONTS = {
    "title": 26,
    "header": 22,
    "large": 44,
    "value": 44,
    "normal": 18,
    "small": 20,
    "caption": 13,
    "nav": 15,
}


def make_font(size, weight=QFont.Bold, spacing=0):
    """
    Build a QFont in the dashboard font.

    spacing is extra letter spacing in pixels, used for the
    small uppercase captions.
    """

    font = QFont(FONT_FAMILY)
    font.setPixelSize(size)
    font.setWeight(weight)

    if spacing:
        font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)

    return font
