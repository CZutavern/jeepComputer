"""
Remote control for LIVI's own UI over Chromium's DevTools protocol.

LIVI is started with --remote-debugging-port (main.py LIVI_DEBUG_PORT,
127.0.0.1 only). While CarPlay streams, LIVI hides its navigation rail and
sends every key to the phone, so its settings can't be reached from the
screen; navigating its router from here brings them up.

    open_settings()    LIVI's settings page (clicks its own Settings tab)
    close_settings()   back to the projection (CarPlay) tab
    reset_dongle()     USB-reset the Carlinkit dongle (LIVI's own force reset)

Clicking LIVI's own navigation tabs (aria-labels Projection / Telemetry /
Media / Settings) goes through its router; LIVI keeps routes in memory,
so location/pushState don't reflect or change them. A scripted click also works while
the rail is hidden during streaming.
"""

import json
import urllib.request

import websocket

PORT = 9223
TIMEOUT_S = 2.0


def _page_socket(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=TIMEOUT_S) as r:
        targets = json.load(r)
    page = next(t for t in targets if t.get("type") == "page")
    # suppress_origin: Chromium refuses DevTools websockets that send an Origin
    return websocket.create_connection(page["webSocketDebuggerUrl"], timeout=TIMEOUT_S,
                                       suppress_origin=True)


def evaluate(expression, port=PORT):
    """Run JavaScript in LIVI's page; returns its value, or None if unreachable."""
    try:
        ws = _page_socket(port)
    except Exception:
        return None
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expression, "returnByValue": True}}))
        while True:
            reply = json.loads(ws.recv())
            if reply.get("id") == 1:
                return ((reply.get("result") or {}).get("result") or {}).get("value")
    except Exception:
        return None
    finally:
        ws.close()


def click_tab(label):
    """Click one of LIVI's navigation tabs by its aria-label."""
    return evaluate(
        f"(() => {{ const t = document.querySelector('#nav-root [role=tab][aria-label={json.dumps(label)}]');"
        " if (!t) return false; t.click(); return true; })()")


def open_settings():
    # Projection first: MUI ignores a click on the tab it already thinks is
    # selected, which would leave the tabs and LIVI's (in-memory) router
    # out of step
    click_tab("Projection")
    return click_tab("Settings")


def close_settings():
    return click_tab("Projection")


def reset_dongle():
    """
    LIVI's own "force reset" (window.projection.usb.forceReset): stops the
    projection and USB-resets the dongle, which re-enumerates in ~3 s, gets
    LIVI's settings (1920x928) again, and the phone reconnects. USB power
    stays on: the Pi 5 can only switch all its ports together, monitor
    included. Returns True if the reset was started.
    """
    return evaluate("(window.projection.usb.forceReset(), true)") is True
