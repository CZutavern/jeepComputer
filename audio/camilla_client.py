import json

import websocket


class CamillaClient:
    """
    Minimal CamillaDSP websocket client.

    Every call is a short request/response on localhost. Any socket
    error drops the connection so the next call (or the dashboard's
    reconnect timer) starts fresh.
    """

    def __init__(self, url="ws://127.0.0.1:1234", timeout=1.0, log_errors=True):
        self.url = url
        self.timeout = timeout
        self.log_errors = log_errors
        self.ws = None

    @property
    def connected(self):
        return self.ws is not None

    def connect(self):
        self.close()

        try:
            self.ws = websocket.create_connection(
                self.url,
                timeout=self.timeout
            )
            return True

        except Exception as e:
            if self.log_errors:
                print("Unable to connect to CamillaDSP:", e)
            self.ws = None
            return False

    def close(self):
        if self.ws is None:
            return

        try:
            self.ws.close()
        except Exception:
            pass

        self.ws = None

    def call(self, command, argument=None):
        """
        Send one command and return its value.

        Raises RuntimeError if CamillaDSP rejects the command or the
        connection is lost.
        """

        if self.ws is None and not self.connect():
            raise RuntimeError("CamillaDSP not connected")

        message = command if argument is None else {command: argument}

        try:
            self.ws.send(json.dumps(message))
            response = json.loads(self.ws.recv())

        except Exception as e:
            self.close()
            raise RuntimeError(f"CamillaDSP connection lost: {e}")

        reply = response.get(command, {})

        if reply.get("result") != "Ok":
            raise RuntimeError(f"CamillaDSP {command} failed: {response}")

        return reply.get("value")

    # ---------------------------------------------------------
    # Convenience wrappers
    # ---------------------------------------------------------

    def get_volume(self):
        return float(self.call("GetVolume"))

    def set_volume(self, db):
        self.call("SetVolume", float(db))
        return self.get_volume()

    def get_state(self):
        return self.call("GetState")

    def get_mute(self):
        return bool(self.call("GetMute"))
