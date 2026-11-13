# Jeep computer: tach input, ACC sense, AGM power feed

## CHANGES (2026-10-11)

- **INA238 retired.** It burned out and was removed (nothing at I2C 0x40). `sensors/ina238.py` stays in the repo, unused; the sensor manager, pages, web page and engine log no longer use it.
- **A3 = starting battery.** ADS1115 input A3 reads the starting battery through R10 100k / R11 22k (A3 = volts x 0.18033). `sensors/battery.py` converts it (`~/.config/jeep_computer/battery.json`: divider, scale, offset_v) and publishes the `battery` sensor. Calibrate with a multimeter: `.venv/bin/python tools/calibrate_start_battery.py <meter volts>`. Readings above 18 V are treated as "not connected". The engine log stores it in the old `v_system` column and counts >= 13.2 V as charging (running).
- **ACC on GPIO 10 is read by `sensors/acc.py`** (`~/.config/jeep_computer/acc.json`: pin, active_low). LOW = key in Accessory/On; key_on turns True after 0.3 s LOW and False only after 2 s HIGH, so cranking dips are ignored. Shown on the Electrical and Diagnostics pages and the phone page. GPIO 10 is now in the tach's reserved pins.
- **`vehicle_signal` option in `ups.json`:** `"ups"` (default, GPIO 6 as before) or `"acc"` (the key state from GPIO 10, falling back to GPIO 6 if it can't be read). With `"acc"` the UPS monitor owns the GPIO 10 line and shares the state with the dashboard through `/run/jeep-ups/acc.json`. Restart `jeep-ups.service` after changing it.

Designed 2026-10-06 from the parts in `..\Electronics Inventory.md`. Schematics are
drawn by `draw_circuits.py` (schemdraw): run it again after any change.
These replace the H11L1 version in the Pi repo's `docs/tach-circuit.md` (a PC817 is on hand, an H11L1 isn't).

**The rule for both inputs:** the vehicle side (left of the PC817) and the Pi side
(right of it) share no wire. The only link is light inside the optocoupler, so a
spike or a wiring mistake on the Jeep side can't reach the Pi.

---

## 1. Tach input (`tach-input.png`) → GPIO 12, header pin 32

**Source:** Summit Racing Blueprinted HEI for AMC V8 (SUM-850048: coil in the cap,
magnetic pickup). Its cap **TACH** terminal is the coil's negative side. It sits at
battery voltage between sparks, drops near 0 V while the coil charges, and rings
**200-400 V** each time a plug fires. One spark = one pulse; a V8 fires 4 times per
revolution: RPM = pulses/s × 15 (idle ~45 Hz, 6000 rpm = 400 Hz).

| Part | Value | Job |
|---|---|---|
| R1a-d | 4 × 1 kΩ ¼ W **in series**, at the distributor end | Limits current. Four in series share a 400 V spike (~95 V each; a single ¼ W resistor is only rated ~250 V). At the distributor end, a chafed cable can't short TACH to ground and stall the engine. |
| TVS1 | P6KE12CA, node A to vehicle ground | Clamps spikes to about ±17 V. It's safe here because R1 limits its current to <0.1 A. |
| C1 | 0.1 µF ceramic | Soaks up the coil's ringing |
| D1 | 1N4001 | Blocks reverse voltage |
| R2 | 470 Ω | Limits LED current during clamped spikes |
| D2 | BAT43, **band toward pin 1** | Protects the LED from reverse voltage |
| U1 | PC817 | Isolates; LED pins 1-2, transistor pins 4 (C) - 3 (E) |
| R4 | 10 kΩ, pin 4 to Pi 3.3 V (header pin 1) | Pull-up |
| R3 | 1 kΩ, pin 4 to GPIO 12 | Protects the GPIO |
| — | PC817 pin 3 to Pi GND (header pin 34) | |

**Numbers:**
- LED current is 2.8 mA running at 14.4 V and 1.7 mA cranking at 9.5 V (node A sits at ~3 V; TVS1 is off).
- At a clamped spike: about 96 mA through R1 and about 31 mA through the LED (PC817 limit: 50 mA continuous, 1 A pulse).
- R1 heat: about 0.03 W steady plus about 0.2 W of spikes at 6000 rpm, so about 0.06 W per resistor. Fine.
- Pi side: the PC817 needs only 0.33 mA into the 10 kΩ pull-up to pull GPIO 12 low; even a weak PC817 manages ~0.7 mA at 1.7 mA LED current. The Pi 5's inputs have built-in hysteresis, and `sensors/tach.py` ignores edges under 1 ms apart.

**Wiring:** 2-core shielded cable, shield grounded at the Pi end only, routed away from the plug wires, with a ferrite clip at the board end. A ¼" insulated female spade goes on the TACH terminal. **Never** tap the BAT terminal or a plug wire.

**Test:** build the Pi side first and boot. ENGINE should show "--". Then connect TACH and start the engine; it should read 600-800 rpm at idle. If it reads double, add a second 0.1 µF at C1.

---

## 2. ACC sense (`acc-sense.png`) → GPIO 10, header pin 19

Tells the Pi when the key is in Accessory/On, so it can pick its power mode itself (needed once the UPS runs off the AGMs, see 3).

| Part | Value | Job |
|---|---|---|
| F1 | 1 A inline fuse **at the ACC tap** | Protects the new wire |
| R5, R6 | 2.2 kΩ each | LED current; R5 also limits TVS2's current |
| TVS2 | P6KE12CA, node B to vehicle ground | Clamps spikes. Node B sits at ~8 V running, under the TVS's 10.2 V standoff, so it stays off normally. |
| D3 | BAT43, band toward pin 1 | Reverse protection for the LED |
| U2 | PC817 | |
| R8 | 10 kΩ, pin 4 to Pi 3.3 V (header pin 17) | Pull-up |
| R7 + C2 | 1 kΩ + 0.1 µF to Pi GND | GPIO protection and a little noise filtering |
| — | pin 3 to Pi GND (header pin 20) | |

**Numbers:** LED current is 3.0 mA at 14.4 V and 2.4 mA at 12 V. A 100 V spike gives about 38 mA in R5, which TVS2 absorbs easily.

**Logic:** key in ACC/ON → LED on → **GPIO 10 reads LOW**. The software would require it stable for ~2 s (it dips while cranking).

**GPIO 10 is free (checked 2026-10-06):** nothing claims it, there are no SPI devices, and `config.txt` has `dtoverlay=nospi10`. Keep SPI0 off, since GPIO 10 is its MOSI pin.

---

## 3. UPS on the AGMs (`agm-power.png`)

Feeds the X1209's DC input from the AGMs all the time, so the UPS cells never drain.

| Part | Value | Job |
|---|---|---|
| F2 | 5 A fuse within 18 cm of the battery post, 18 AWG wire | Wire protection (the X1209 pulls up to ~2 A at 12 V while charging and running the Pi) |
| TVS3 | **P6KE18A, unidirectional (BUY)**, band to +12 V | Clamps spikes while staying off at 14.7 V AGM charging. **Not the P6KE12CA**: across a 12 V supply it would conduct constantly and burn. |
| C3 | 2200 µF 35 V | Bulk smoothing at the X1209 input |
| C4 | 0.1 µF | High-frequency bypass |
| Ferrite | clip-on, on the feed cable | |

Check polarity with a meter before plugging in.

### What changes in software (once built)

- **"Vehicle on" comes from GPIO 10 (ACC sense) instead of GPIO 6.** The X1209's GPIO 6 would then only mean "AGM power present", which is always true.
- **Parked: station mode indefinitely.** The AGMs carry it at about 0.25 A for weather logging and VPN access, and the UPS cells stay full. That's about 6 Ah a day: about 8 days per 100 Ah AGM taken down to 50%, or about 16 days if both 100 Ah batteries feed it.
- **Protect the AGMs:** shut down fully when they fall to about 12.1 V resting (~50%) or after a set number of days parked. Using the INA238 reading needs confirming that it measures the AGM.
- **Heat:** with charging running all the time, the monitor should switch the X1209's charging off (GPIO 16 HIGH) when the box gets hot. Lithium cells shouldn't charge above ~45 °C, and a parked Jeep in summer gets hotter than that. The external fans are on ACC, so they'll be off while parked.
- **After a full shutdown** the Pi won't wake on ACC by itself, because the UPS input never goes away. Press the X1209 button, or add a wake circuit later.

### Open questions

1. **Are the 2 × 100 Ah AGMs the starting battery, or a separate house bank behind the isolator?** If they also start the Jeep, the cut-off voltage needs to be more conservative.
2. Which of the two batteries does the INA238 measure?
3. Please confirm the resistor kit's values and the diode kit's contents (see the inventory).
