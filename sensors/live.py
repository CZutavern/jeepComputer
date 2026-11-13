"""
Latest sensor readings as a small JSON file in RAM, for the phone web page
(web/server.py) and anything else that wants them without touching the
I2C bus. Written once a second by whoever owns the sensors: the dashboard's
sensor thread, or weather/station.py while the Jeep is parked.

    $XDG_RUNTIME_DIR/jeep-live.json
    {"time": 1790000000.0, "source": "dashboard", "sensors": {...get_data()...}}
"""

import json
import os
import time
from pathlib import Path

LIVE_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "jeep-live.json"


def write_live(data, source):
    tmp = LIVE_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"time": time.time(), "source": source, "sensors": data},
                                  default=str))
        os.replace(tmp, LIVE_PATH)
    except OSError:
        pass


def read_live():
    try:
        return json.loads(LIVE_PATH.read_text())
    except (OSError, ValueError):
        return None
