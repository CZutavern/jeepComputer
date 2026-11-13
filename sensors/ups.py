import json
import time

STATUS_FILE = "/run/jeep-ups/status.json"

# The UPS monitor writes once a second; older than this means it stopped
STALE_S = 10


def read_ups_status():
    """
    Latest status from the UPS monitor service (ups_monitor.py), or
    None if it isn't running:

    {vehicle_power, battery_voltage, battery_percent, on_battery_s,
     shutdown_in, shutting_down, armed}
    """

    try:
        with open(STATUS_FILE) as f:
            status = json.load(f)
    except (OSError, ValueError):
        return None

    if time.time() - status.get("time", 0) > STALE_S:
        return None

    return status
