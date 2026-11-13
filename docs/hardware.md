# Hardware: shopping list and pin map

## Shopping list

### Tachometer input (see tach-circuit.md)
| Qty | Part | Notes |
|---|---|---|
| 1 | H11L1 optocoupler, DIP-6 (onsemi H11L1M or Vishay H11L1) | Schmitt-trigger output |
| 1 | DIP-6 socket | |
| 1 | 3.3 kΩ **2 W** metal-oxide resistor | R1, mounted at the distributor end |
| 1 each | 470 Ω, 1 kΩ, 4.7 kΩ ¼ W resistors | R2, R3, R4 |
| 1 | 1N4746A zener, 18 V 1 W | Z1 |
| 1 | 1N4007 diode | D1 |
| 1 | 1N4148 diode | D2 |
| 1 | 100 nF **100 V film** capacitor | C1 (buy a 220 nF too, in case) |
| 1 | 100 nF ceramic capacitor | C2 |
| 1 | Small perfboard + project box | |
| ~3 m | 2-core shielded cable | distributor to board |
| 1 | 1/4" insulated female spade | for the HEI TACH terminal |

### Weather
| Qty | Part | Notes |
|---|---|---|
| 1 | AS3935 lightning sensor breakout, I²C (DFRobot Gravity or SparkFun) | Storms up to 40 km away, with distance. I²C 0x03, IRQ → GPIO 13 |
| 1 | Small solar radiation shield (stacked-plate "Stevenson" style) | For the SHT45 outside sensor; stops sun and engine heat from skewing readings |

### GPS
| Qty | Part | Notes |
|---|---|---|
| 1 | u-blox M10 GPS module with UART (e.g. Beitian BE-220 / BN-220 class) | True elevation, position, speed, time without internet. UART3 on GPIO 8/9 |
| 1 | Active GPS antenna with a long lead, if the module has an SMA/U.FL connector | Roof or dash top, not under metal |

### Power and clock
| Qty | Part | Notes |
|---|---|---|
| 1 | Raspberry Pi RTC battery (rechargeable ML-2020, JST plug) | Keeps the clock through full power-off; plugs into the Pi 5's BAT connector |
| 1 | INA238 breakout, with the address jumper set to 0x41 | Optional: real draw on the UPS output (the planned INA238 at 0x40 measures the vehicle battery) |
| 1 | 12 V → 5 V 5 A buck converter, fused, on ignition-switched power | Recommended: power the monitor and dongle from it instead of the Pi's USB ports (see below) |

**Not needed:** a USB Wi-Fi adapter. The Carlinkit dongle runs CarPlay's
Wi-Fi itself (5 GHz channel 36), so the Pi's built-in Wi-Fi stays on the
house network.

### Why a separate 5 V supply for the monitor
The monitor and CarPlay dongle both draw from the Pi's USB ports, which share
a 1.6 A limit and the Pi's own supply. Plugging and unplugging them has
knocked USB devices off and taken the desktop down. Powering the monitor and
dongle from an ignition-switched 5 V converter:
- takes that load off the Pi and the UPS,
- turns the monitor off automatically when the Jeep is off, so weather-station
  mode no longer has to cut USB power,
- leaves the UPS batteries for the Pi alone, for longer logging.

## GPIO map (BCM numbers)

| GPIO | Header pin | Use | Status |
|---|---|---|---|
| 2 / 3 | 3 / 5 | I²C (all sensors, UPS fuel gauge) | in use |
| 4 | 7 | BNO085 IMU reset | in use |
| 5 | 29 | HiFiBerry DAC8x ADC-detect | **reserved** |
| 6 | 31 | X1209 UPS: vehicle power present | **reserved** |
| 7 | 26 | GPS PPS (optional) | planned |
| 8 / 9 | 24 / 21 | GPS UART3 TX / RX | planned |
| 12 | 32 | Tachometer input | in use |
| 13 | 33 | AS3935 lightning IRQ | planned |
| 14 / 15 | 8 / 10 | serial console | **reserved** |
| 16 | 36 | X1209 UPS: charge control (HIGH = charging off) | **reserved, never touch** |
| 17 | 11 | Amplifier remote | in use |
| 18–27 | | HiFiBerry DAC8x | **reserved** |
| 10 / 11 | 19 / 23 | free | |

I²C addresses in use: 0x18 MCP9808, 0x29 TSL2591, 0x36 UPS fuel gauge, 0x40
INA238, 0x44 SHT45, 0x48 ADS1115, 0x4A BNO085, 0x77 BMP388. New: 0x03 AS3935,
0x41 second INA238.
