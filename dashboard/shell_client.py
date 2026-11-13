import json
import os
import socket


# Set by shell/jeep_shell.py when the dashboard runs inside the shell
CONTROL_PATH = os.environ.get("JEEP_SHELL_CONTROL")


def in_shell():
    return bool(CONTROL_PATH)


def shell_request(request, timeout=0.5):
    """
    One request to the shell's control socket. Returns the reply dict,
    or None if the shell isn't reachable.
    """

    if not CONTROL_PATH:
        return None

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(CONTROL_PATH)
            sock.sendall(json.dumps(request).encode() + b"\n")

            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk

        return json.loads(data)

    except (OSError, ValueError):
        return None


def place_carplay(x, y, width, height, visible):
    return shell_request({
        "cmd": "carplay",
        "rect": [x, y, width, height],
        "visible": visible,
    })


def shell_status():
    return shell_request({"cmd": "status"})


def show_volume_osd(volume_db, message=""):
    """Volume pop-up drawn by the shell (visible over CarPlay)."""
    return shell_request({"cmd": "osd", "volume": volume_db, "message": message})


def set_dim(level):
    """Night dimming over the whole screen (0 = off)."""
    return shell_request({"cmd": "dim", "level": round(float(level), 3)})


def show_livi_ui(show):
    """Show LIVI's own screen (settings etc.) in the CarPlay area without a phone."""
    return shell_request({"cmd": "livi_ui", "show": bool(show)})
