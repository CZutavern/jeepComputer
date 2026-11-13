from dashboard.widgets.gauge_card import GaugeCard
from dashboard.theme import COLORS, format_value
from dashboard.pages.base import DashboardPage, reading, online


class ElectricalPage(DashboardPage):

    def __init__(self):
        super().__init__("Electrical")

        self.cards = {
            "battery_voltage": GaugeCard("Start Battery", "V", 11.0, 15.0),
            "rpm": GaugeCard("Engine", "rpm"),
            "key": GaugeCard("Ignition Key"),
            "charging": GaugeCard("Charging"),
            "lux": GaugeCard("Ambient Light", "lx"),
            "system": GaugeCard("Inputs"),
            "ups_source": GaugeCard("Pi Power Source"),
            "ups_percent": GaugeCard("UPS Battery", "%", 0, 100),
            "ups_voltage": GaugeCard("UPS Cell", "V", 3.0, 4.2),
        }

        self.layout_.addLayout(
            self.add_card_grid(list(self.cards.values()), columns=3),
            stretch=1
        )

        self.update_values()

    # --------------------------------------------------
    # UPS (Geekworm X1209), called by main.py once a second
    # --------------------------------------------------

    def set_ups(self, ups):
        source = self.cards["ups_source"]
        percent = self.cards["ups_percent"]
        volts = self.cards["ups_voltage"]

        if ups is None:
            source.set_value("NO UPS")
            source.set_status("warning")
            percent.set_value(None)
            volts.set_value(None)
            return

        if ups.get("vehicle_power"):
            full = ups.get("hours_to_full")
            source.set_value("VEHICLE" if full is None else f"CHARGING · {full:.1f} h")
            source.set_status("good")
        elif ups.get("shutdown_in") is not None:
            action = ups.get("grace_action")
            station = action in ("station", "sleep")
            label = {"station": "STATION", "sleep": "SLEEP"}.get(action, "OFF")
            source.set_value(f"BATTERY · {label} IN {ups['shutdown_in']:.0f}s")
            source.set_status("warning" if station else "critical")
        elif ups.get("mode") == "station" and ups.get("station_max_hours"):
            # Parked on always-on power: the time limit is the safeguard (ups_monitor.py)
            hours, limit = ups.get("station_hours") or 0, ups["station_max_hours"]
            if ups.get("halt_warning"):
                source.set_value("SHUTDOWN SOON · OFF UNTIL BUTTON")
                source.set_status("critical")
            else:
                source.set_value(f"STATION MODE · {hours:.0f}h of {limit:g}h")
                source.set_status("warning")
        else:
            source.set_value("BATTERY")
            source.set_status("warning")

        pct = ups.get("battery_percent")
        percent.set_value(format_value(pct, 0))
        if pct is not None:
            low = "warning" if ups.get("vehicle_power") else "critical"     # charging: not an emergency
            percent.set_status(low if pct < 10 else "warning" if pct < 25 else "good")

        volts.set_value(format_value(ups.get("battery_voltage"), 2))

    # --------------------------------------------------

    def update_values(self):

        data = self.sensor_data

        # Starting battery from ADS1115 A3 (the INA238 power monitor is retired)
        voltage = reading(data, "battery", "voltage")
        rpm = reading(data, "tach", "rpm")
        key_on = reading(data, "acc", "key_on")
        lux = reading(data, "light", "lux")

        self.cards["battery_voltage"].set_value(format_value(voltage, 2))
        self.cards["rpm"].set_value(format_value(rpm, 0))
        self.cards["lux"].set_value(format_value(lux, 0))

        key = self.cards["key"]
        if key_on is None:
            key.set_value(None)
            key.set_status("normal")
        else:
            key.set_value("ON" if key_on else "OFF")
            key.set_status("good" if key_on else "normal")

        # --------------------
        # Charging status
        # --------------------

        if voltage is None:
            charging, status = None, "normal"
        elif voltage >= 13.5:
            charging, status = "CHARGING", "good"
        elif voltage >= 12.2:
            charging, status = "ON BATTERY", "warning"
        else:
            charging, status = "LOW", "critical"

        self.cards["charging"].set_value(charging)
        self.cards["charging"].set_status(status)
        self.cards["battery_voltage"].set_status(status)

        # --------------------
        # Sensor health
        # --------------------

        # ACC, Tach and Battery (A3) are the inputs this page is about. The tach
        # reads as "offline" until the first spark, so it only counts when the
        # engine is meant to be running.
        acc_ok = online(data, "acc")
        tach_ok = online(data, "tach")
        battery_ok = online(data, "battery")
        light_ok = online(data, "light")

        if acc_ok and battery_ok and light_ok:
            self.cards["system"].set_value("OK")
            self.cards["system"].set_status("good")
        elif data:
            self.cards["system"].set_value("CHECK")
            self.cards["system"].set_status("warning")

        if data:
            parts = [f"ACC {'ok' if acc_ok else 'offline'}",
                     f"Tach {'ok' if tach_ok else 'no signal'}",
                     f"Battery {'ok' if battery_ok else 'offline'}"]
            self.header.set_subtitle(
                " · ".join(parts),
                COLORS["good"] if acc_ok and battery_ok else COLORS["warning"],
            )
