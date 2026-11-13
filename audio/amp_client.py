import json
import os
import socket


SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
    "jeep-amp.sock",
)


def amp_request(request, timeout=0.5):
    """
    Send one request to the amp controller service (amp_controller.py).
    Returns its reply dict, or None if the service isn't running.
    """

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(SOCKET_PATH)
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


def amp_status():
    return amp_request({"cmd": "status"})


def set_amp_mode(mode):
    return amp_request({"cmd": "set_mode", "mode": mode})


def set_amp_idle_off(seconds):
    return amp_request({"cmd": "set_idle_off", "seconds": seconds})
