# Engine log, fuel use and gauge self-calibration

`engine/logger.py` runs on the dashboard's sensor thread and keeps
`~/.local/share/jeep_computer/engine.db` (`engine/store.py`).

## What's stored

- **samples**, every 10 s while the ignition is on: raw sender voltages (A0
  fuel, A1 coolant, A2 oil, A3 spare), RPM, the gauge supply used, and the
  converted fuel %, coolant °F, oil psi. A year is kept. This is what the
  sender calibration can be refitted from as trips pile up.
- **trips**: each key-on: engine running time, fuel at the start and end,
  and gallons added before it (a fill-up: the level up 8 %+ since last time).
- **anchors**: calibration moments (below).

## Fuel use

No distance yet (no GPS), so **gallons per engine hour** since the last
fill-up: the drop in fuel level × the 21 gal tank ÷ engine hours. Shown on
the Home page ("Fuel use", with hours of running left) once there's half an
hour of engine time and 4 % of the tank used; until then it says
"learning". The Engine card shows total engine hours logged. With GPS
distance this becomes MPG.

## Self-calibration

The gauges run from a pulsing voltage regulator; what the ADS1115 sees of
it (about 1.35 V) sets every reading (`sensors/senders.py`). Every key-on
with the engine off is a free reference: oil pressure is 0, so the oil
sender is at its 0 psi resistance and its voltage gives the supply. The
logger stores those moments and after each start re-learns the supply as the
median over the last 20 trips, into `~/.config/jeep_computer/senders_cal.json`,
which the conversion then uses.

They only count once the tach has seen the engine run (so "no tach signal"
really means off) and when within 30 % of the current value. Until then
they're stored as untrusted, and the supply stays at the measured 1.35 V.

## Refitting the curves later

Everything raw is in the samples table, e.g. coolant voltage against the
outside temperature after the Jeep sat overnight, or fuel voltage right
after a fill to full. `python3 -c "import sqlite3; ..."` or ask Claude.
