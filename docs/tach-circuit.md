# Tachometer input (HEI distributor → Pi GPIO 12)
> **Superseded 2026-10-06:** the build to use is the PC817 version in [circuits/README.md](circuits/README.md) (an H11L1 is not on hand). This page is kept for its background.

Engine: 1978 AMC 360 V8, Summit Racing Blueprint HEI distributor.
The HEI cap has a **TACH** terminal: the coil's negative (primary) side. It sits
at battery voltage while the module is off, drops near 0 V while the coil
charges (dwell), and rings with **200–400 V spikes** each time it fires. That
must never reach a GPIO (3.3 V max), so the signal goes through a clamp, a
filter and an optocoupler. The Pi only ever sees a clean 3.3 V logic signal.

One spark = one pulse. A V8 fires 4 times per revolution:
`RPM = pulses per second × 15` (idle ~650 rpm = 43 Hz, 6000 rpm = 400 Hz).

## Schematic

Signal path:

```
HEI TACH ─► R1 ─► [node A: Z1 + C1 to ground] ─► D1 ─► R2 ─► H11L1 LED ─► vehicle GND
                                                            ║ (light only, no wire)
Pi 3.3 V ─► R4 pull-up ─► H11L1 output ─► R3 ─► GPIO 12
```

Every connection, node by node:

| Node | Connects |
|---|---|
| TACH | HEI TACH terminal → R1 (end 1) |
| A | R1 (end 2), Z1 cathode (band), C1 (either leg), D1 anode |
| B | D1 cathode (band) → R2 (end 1) |
| C | R2 (end 2), H11L1 pin 1 (LED anode), D2 cathode (band) |
| VGND | H11L1 pin 2 (LED cathode), D2 anode, Z1 anode, C1 (other leg) → vehicle ground |
| 3V3 | Pi pin 1 → H11L1 pin 6 (Vcc), R4 (end 1), C2 (leg 1) |
| OUT | H11L1 pin 4 (Vo), R4 (end 2), R3 (end 1) |
| GPIO | R3 (end 2) → Pi pin 32 (GPIO 12) |
| PGND | H11L1 pin 5 (GND), C2 (leg 2) → Pi pin 34 (GND) |

H11L1 pinout: 1 LED anode, 2 LED cathode, 3 not connected, 4 output (Vo), 5 GND, 6 Vcc.
Pin 1 is next to the dot or notch. Check this against the datasheet of the exact part you buy
(H11L1M from onsemi/Fairchild, or Vishay H11L1).

- **R1 3.3 kΩ, 2 W** metal-oxide: limits current. **Mount it at the distributor
  end** of the cable, in line with the TACH wire. Then if the cable ever chafes
  through to ground it can't short the TACH terminal and stall the engine.
- **Z1 18 V, 1 W zener (1N4746A)**, cathode toward R1: clamps the spikes.
- **C1 100 nF, 100 V film**: filters the coil's ringing (~40 µs).
- **D1 1N4007**: blocks reverse voltage.
- **R2 470 Ω**: sets the LED current: 2 mA while cranking (9.5 V), 3.3 mA
  running (14.4 V), 34 mA peak on a clamped spike (limit 60 mA).
- **D2 1N4148** across the LED, pointing the opposite way (anode on pin 2,
  cathode band on pin 1): protects the LED from reverse voltage.
- **H11L1** optocoupler with a Schmitt-trigger output: clean, snappy edges even
  from a messy input. It turns on at 1.6 mA or less.
- Pi side: **R4 4.7 kΩ** pull-up to 3.3 V, **R3 1 kΩ** in series to the GPIO,
  **C2 100 nF** across H11L1 pins 6 and 5.

When the HEI fires, the LED lights and the H11L1 output goes **low**. The
software counts falling edges on GPIO 12.

## Wiring notes

- Tap the **TACH** terminal only, never the BAT (+12 V) terminal or a plug wire.
  The HEI TACH terminal takes a 1/4" female spade.
- Use 2-core **shielded** cable from the distributor to the board. Ground the
  shield at the Pi end only. Keep it away from the plug wires.
- Build the board on perfboard, with a DIP-6 socket for the H11L1, in a small
  box near the Pi.
- The H11L1 is powered from the **Pi's 3.3 V** (pin 1). Don't power it from the car.

## Pins used

| Pi header pin | Use |
|---|---|
| 1 | 3.3 V to H11L1 pin 6 and R4 |
| 32 | GPIO 12, the tach input (from R3) |
| 34 | GND to H11L1 pin 5 |

GPIO 16 is **not** available: the X1209 UPS uses it for charge control.

## Software

`sensors/tach.py` reads the pin. Settings are in `~/.config/jeep_computer/tach.json`:
`{"pin": 12, "cylinders": 8}`. It ignores edges closer than 1 ms apart
(coil ringing) and shows RPM as the median of the last 8 intervals. The
Home page's ENGINE card shows "--" until the first spark is seen, then the RPM.

## First test

1. Build the board and connect only the Pi side. ENGINE should show "--".
2. Connect the TACH wire and start the engine. It should read about 600–800 rpm at idle.
3. If it reads exactly double (1300+ at idle), the ringing is getting through:
   change C1 to 220 nF.
