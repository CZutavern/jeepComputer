import subprocess


# PipeWire link that proves CarPlay audio reaches CamillaDSP
# (created by ~/.local/bin/connect-camilladsp.sh)
ROUTE_SOURCE = "jeep_carplay:monitor_FL"
ROUTE_TARGET = "camilladsp-capture:input_1"


def route_ok():
    """True if the CarPlay sink is linked into CamillaDSP."""

    try:
        links = subprocess.run(
            ["pw-link", "-l", "-o"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout

    except (OSError, subprocess.TimeoutExpired):
        return False

    source = None

    for line in links.splitlines():
        if not line.startswith((" ", "\t")):
            source = line.strip()
        elif source == ROUTE_SOURCE and ROUTE_TARGET in line:
            return True

    return False
