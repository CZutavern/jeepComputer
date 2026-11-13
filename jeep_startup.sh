#!/bin/bash

# Amplifier remote-on controller (auto on/off with the audio, manual
# override from the dashboard's Audio page). Replaces power_monitor.py.
#
# The CamillaDSP GUI is not opened here: it covered the dashboard.
# Open it from any browser on the network instead:
#   http://<pi-ip>:5005/gui/index.html

cd "$(dirname "$0")"
exec .venv/bin/python -u amp_controller.py
