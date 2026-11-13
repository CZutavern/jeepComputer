from PySide6.QtWidgets import QHBoxLayout, QWidget

from dashboard.widgets.gauge_card import GaugeCard
from dashboard.widgets.arc_gauge import ArcGauge
from dashboard.theme import COLORS, format_value
from dashboard.pages.base import DashboardPage, reading, online

from sensors.senders import read_senders
from sensors.tach import gauge_scale



def c_to_f(c):
    return None if c is None else c * 9 / 5 + 32


def rpm_ticks(top):
    """Labelled ticks every 1000 rpm (thousands shown as 0..6), minor ones between."""
    step = 1000
    ticks = [(v, str(v // 1000)) for v in range(0, int(top) + 1, step)]
    ticks += [(v + step // 2, "") for v in range(0, int(top), step) if v + step // 2 < top]
    return ticks


class HomePage(DashboardPage):

    # Fuel sloshes on rough ground; average it out. Each 1 s reading
    # moves the display this fraction of the way (~20 s to settle).
    FUEL_SMOOTHING = 0.1

    # Alternator output means the engine is running. Used so low oil
    # pressure only alarms with the engine on, not at key-on.
    ENGINE_RUNNING_VOLTS = 13.2

    COOLANT_WARNING_F = 220
    COOLANT_CRITICAL_F = 235
    OIL_CRITICAL_PSI = 10
    OIL_WARNING_PSI = 15
    OIL_BUILT_PSI = 20
    FUEL_WARNING_PCT = 15
    FUEL_CRITICAL_PCT = 7

    def __init__(self):
        super().__init__("Vehicle Status")

        self.fuel_smoothed = None
        self.oil_built = False

        # (level, message) for the dashboard-wide alert banner, or None
        self.alert = None

        # --------------------
        # Overall status banner
        # --------------------

        self.status = GaugeCard("System Status", value_size=48)
        self.status.setFixedHeight(128)
        self.layout_.addWidget(self.status)

        # --------------------
        # Gauges
        # --------------------

        # Coolant, oil pressure and fuel come from the Jeep senders on
        # the ADS1115 (see sensors/senders.py for wiring and curves).
        rpm_max, rpm_red = gauge_scale()
        self.gauges = {
            # Engine speed from the tach (None / "no ignition signal" = engine off: "—", needle faded).
            # The dial already glides 0.7 s to each new value; the readout rounds to 10 rpm.
            "rpm": ArcGauge(
                "Engine", "RPM", 0, rpm_max,
                zones=[(rpm_red, rpm_max, "warning")],
                ticks=rpm_ticks(rpm_max),
                formatter=lambda v: f"{round(v / 10) * 10:.0f}",
            ),
            "coolant": ArcGauge(
                "Coolant", "°F", 100, 260,
                zones=[(self.COOLANT_WARNING_F, self.COOLANT_CRITICAL_F, "warning"),
                       (self.COOLANT_CRITICAL_F, 260, "critical")],
                ticks=[(100, "100"), (140, "140"), (180, "180"), (220, "220"), (260, "260"),
                       (120, ""), (160, ""), (200, ""), (240, "")],
            ),
            "oil_pressure": ArcGauge(
                "Oil Pressure", "PSI", 0, 80,
                zones=[(0, self.OIL_CRITICAL_PSI, "critical"),
                       (self.OIL_CRITICAL_PSI, self.OIL_WARNING_PSI, "warning")],
                ticks=[(0, "0"), (20, "20"), (40, "40"), (60, "60"), (80, "80"),
                       (10, ""), (30, ""), (50, ""), (70, "")],
            ),
            "fuel": ArcGauge(
                "Fuel", "% tank", 0, 100,
                zones=[(0, self.FUEL_CRITICAL_PCT, "critical"),
                       (self.FUEL_CRITICAL_PCT, self.FUEL_WARNING_PCT, "warning")],
                ticks=[(0, "E"), (25, "¼"), (50, "½"), (75, "¾"), (100, "F"),
                       (12.5, ""), (37.5, ""), (62.5, ""), (87.5, "")],
            ),
        }

        gauge_row = QHBoxLayout()
        gauge_row.setSpacing(self.SPACING)
        for gauge in self.gauges.values():
            gauge_row.addWidget(gauge)
        self.layout_.addLayout(gauge_row, stretch=1)

        self.small_cards = {
            "cabin_temp": GaugeCard("Cabin", "°F", value_size=52),
            "outside_temp": GaugeCard("Outside", "°F", value_size=52),
            "battery_voltage": GaugeCard("Battery", "V", 11.0, 15.0, value_size=52),
            "rpm": GaugeCard("Engine", "RPM", value_size=52),
            # gal per engine hour since the last fill-up (engine/logger.py)
            "fuel_use": GaugeCard("Fuel use", "gal/h", value_size=52),
        }
        small_box = QWidget()
        small_box.setLayout(self.add_card_grid(list(self.small_cards.values()), columns=5))
        small_box.setFixedHeight(170)
        self.layout_.addWidget(small_box)

        # One lookup for status colouring below
        self.cards = {**self.gauges, **self.small_cards}

        self.update_values()

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def smooth_fuel(self, fuel):
        if fuel is None:
            self.fuel_smoothed = None
        elif self.fuel_smoothed is None:
            self.fuel_smoothed = fuel
        else:
            self.fuel_smoothed += (fuel - self.fuel_smoothed) * self.FUEL_SMOOTHING

        return self.fuel_smoothed

    @staticmethod
    def level_status(card, value, warning, critical, low_is_bad=False):
        if value is None:
            return

        if low_is_bad:
            value, warning, critical = -value, -warning, -critical

        if value > critical:
            card.set_status("critical")
        elif value > warning:
            card.set_status("warning")
        else:
            card.set_status("good")

    # --------------------------------------------------
    # Update all displayed values
    # --------------------------------------------------

    # Sender inputs all below this: the gauge senders have no supply,
    # i.e. the ignition is off (not a sensor fault)
    IGNITION_OFF_V = 0.2

    def ignition_off(self, data):
        analog = data.get("analog") or {}
        if analog.get("status") != "OK":
            return False
        channels = analog.get("data") or {}
        volts = [(channels.get(c) or {}).get("voltage") for c in ("A0", "A1", "A2")]
        return all(v is not None and v < self.IGNITION_OFF_V for v in volts)

    def update_values(self):

        data = self.sensor_data
        senders = read_senders(data)

        values = {
            "coolant": senders["coolant"],
            "oil_pressure": senders["oil_pressure"],
            "fuel": self.smooth_fuel(senders["fuel"]),
            # Cabin: the barometer's temperature (the MCP9808 is in the amp box)
            "cabin_temp": c_to_f(reading(data, "pressure", "temperature_c")),
            "outside_temp": reading(data, "outside", "temperature_f"),
            "battery_voltage": reading(data, "battery", "voltage"),
            "rpm": reading(data, "tach", "rpm"),
            "fuel_use": reading(data, "engine", "gph"),
        }

        # Dials animate to the number; small cards show formatted text
        for key, gauge in self.gauges.items():
            gauge.set_value(values[key])

        decimals = {"battery_voltage": 2, "rpm": 0}
        for key, card in self.small_cards.items():
            card.set_value(format_value(values[key], decimals.get(key, 1)))

        engine = (data.get("engine") or {}).get("data") or {}
        hours = engine.get("engine_hours")
        self.small_cards["rpm"].set_title("Engine" if hours is None else f"Engine · {hours:.1f} h")
        left = engine.get("hours_left")
        self.small_cards["fuel_use"].set_title(
            "Fuel use · learning" if values["fuel_use"] is None
            else "Fuel use" if left is None else f"Fuel use · {left:.1f} h left")

        coolant = values["coolant"]
        oil = values["oil_pressure"]
        fuel = values["fuel"]
        voltage = values["battery_voltage"]

        # Running: the alternator's voltage, else a tach that has proven it
        # sees the engine (engine/logger.py). Neither: assume running, so a
        # real low-oil alarm is never hidden.
        # Neither: running once oil pressure has built up since key-on, so
        # key on / engine off (0 psi) doesn't alarm but losing pressure does.
        engine = (data.get("engine") or {}).get("data") or {}
        if self.ignition_off(data):
            self.oil_built = False
        elif oil is not None and oil >= self.OIL_BUILT_PSI:
            self.oil_built = True
        if voltage is not None:
            engine_running = voltage >= self.ENGINE_RUNNING_VOLTS
        elif engine.get("tach_proven"):
            engine_running = bool(engine.get("running"))
        else:
            engine_running = self.oil_built

        # --------------------
        # Card status colours
        # --------------------

        self.level_status(
            self.cards["coolant"], coolant,
            self.COOLANT_WARNING_F, self.COOLANT_CRITICAL_F
        )

        self.level_status(
            self.cards["fuel"], fuel,
            self.FUEL_WARNING_PCT, self.FUEL_CRITICAL_PCT, low_is_bad=True
        )

        if oil is not None:
            if engine_running:
                self.level_status(
                    self.cards["oil_pressure"], oil,
                    self.OIL_WARNING_PSI, self.OIL_CRITICAL_PSI, low_is_bad=True
                )
            else:
                self.cards["oil_pressure"].set_status("normal")

        self.level_status(
            self.cards["battery_voltage"], voltage, 12.2, 11.8, low_is_bad=True
        )

        # --------------------
        # System health (most serious first)
        # --------------------

        # "engine" is the engine log's summary, not a sensor
        sensor_names = [name for name in data if name != "engine"]
        sensors_online = sum(1 for name in sensor_names if online(data, name))

        if coolant is not None and coolant > self.COOLANT_CRITICAL_F:
            self.status.set_value("ENGINE OVERHEATING")
            self.status.set_status("critical")

        elif oil is not None and engine_running and oil < self.OIL_CRITICAL_PSI:
            self.status.set_value("LOW OIL PRESSURE")
            self.status.set_status("critical")

        elif coolant is not None and coolant > self.COOLANT_WARNING_F:
            self.status.set_value("ENGINE HOT")
            self.status.set_status("warning")

        elif voltage is not None and voltage < 12.0:
            self.status.set_value("LOW BATTERY")
            self.status.set_status("warning")

        elif fuel is not None and fuel < self.FUEL_WARNING_PCT:
            self.status.set_value("LOW FUEL")
            self.status.set_status("warning")

        elif self.ignition_off(data):
            self.status.set_value("IGNITION OFF")
            self.status.set_status("normal")

        elif voltage is None:
            self.status.set_value("SENSORS OFFLINE")
            self.status.set_status("warning")

        else:
            self.status.set_value("ALL SYSTEMS OK")
            self.status.set_status("good")

        # Engine / fuel / battery alarms go to the dashboard-wide banner.
        # "Sensors offline" is shown here only, so it doesn't nag.
        state = self.status.status
        message = self.status.value_text

        if state == "critical":
            self.alert = ("critical", message)
        elif state == "warning" and message != "SENSORS OFFLINE":
            self.alert = ("warning", message)
        else:
            self.alert = None

        if data:
            color = COLORS["good"] if sensors_online == len(sensor_names) else COLORS["warning"]
            self.header.set_subtitle(
                f"{sensors_online} of {len(sensor_names)} sensors online", color
            )
        else:
            self.header.set_subtitle("Starting sensors…")
