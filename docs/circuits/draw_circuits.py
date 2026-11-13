"""
Draws the Jeep computer's interface circuits with schemdraw.
Run:  python draw_circuits.py
Writes tach-input.svg/png, acc-sense.svg/png and agm-power.svg/png next to this file.
Every part here is from "Electronics Inventory.md" unless marked BUY.
"""

from pathlib import Path

import schemdraw
import schemdraw.elements as elm

HERE = Path(__file__).resolve().parent
schemdraw.config(fontsize=11, inches_per_unit=0.55)


def opto(d, label):
    """PC817 drawn as a 4-pin box: LED on the left (pins 1, 2), transistor on the right (4, 3)."""
    return elm.Ic(pins=[elm.IcPin(name="A", pin="1", side="left", slot="2/2"),
                        elm.IcPin(name="K", pin="2", side="left", slot="1/2"),
                        elm.IcPin(name="C", pin="4", side="right", slot="2/2"),
                        elm.IcPin(name="E", pin="3", side="right", slot="1/2")],
                  edgepadW=0.6, edgepadH=0.6, pinspacing=1.6, label=label, lblofst=0.2)


def tach():
    with schemdraw.Drawing(file=str(HERE / "tach-input.svg"), show=False) as d:
        d += elm.Label().label("HEI TACH\n(200-400 V spikes)", loc="left").at((0, 0))
        d += elm.Dot(open=True)
        d += elm.Resistor().right().label("R1a 1k")
        d += elm.Resistor().right().label("R1b 1k")
        d += elm.Resistor().right().label("R1c 1k")
        d += elm.Resistor().right().label("R1d 1k")
        d += (nodeA := elm.Dot().label("A", loc="top"))
        d += elm.Line().right(1.4)
        d += (nA2 := elm.Dot())
        d += elm.Diode().right().label("D1 1N4001")
        d += elm.Resistor().right().label("R2 470")
        d += (nC := elm.Dot())
        d += elm.Line().right(0.6)
        u = opto(d, "U1 PC817").anchor("A")
        d += u
        # clamp + filter at node A
        d += elm.DiodeTVS().down().at(nodeA.start).label("TVS1\nP6KE12CA", loc="bottom")
        d += elm.Ground()
        d += elm.Capacitor().down().at(nA2.start).label("C1 0.1uF", loc="bottom")
        d += elm.Ground()
        # reverse protection across the LED
        d += elm.Line().down(1.6).at(u.K)
        d += (k := elm.Dot())
        d += elm.Ground().label("vehicle GND", loc="right")
        d += elm.Line().left(1.6).at(k.start)
        d += (kk := elm.Dot())
        # anode on pin 2 (K), cathode band toward pin 1 (A): conducts only if the LED is reverse-biased
        d += elm.Schottky().up().at(kk.start).toy(nC.start).label("D2 BAT43", loc="bottom")
        d += elm.Dot()
        # Pi side
        d += elm.Line().right(1.2).at(u.C)
        d += (out := elm.Dot())
        d += elm.Resistor().right().label("R3 1k")
        d += elm.Dot(open=True).label("Pi pin 32\nGPIO 12", loc="right")
        d += elm.Resistor().up().at(out.start).label("R4 10k", loc="bottom")
        d += elm.Dot(open=True).label("Pi pin 1  3.3 V", loc="right")
        d += elm.Line().right(1.2).at(u.E)
        d += elm.Line().down(1.0)
        d += elm.GroundChassis().label("Pi GND pin 34", loc="right")
        d += elm.Label().at((0.5, -4.2)).label(
            "R1a-d: four 1 kOhm 1/4 W in series, mounted at the distributor end (each sees ~1/4 of the spike).\n"
            "Left of U1 = vehicle side (vehicle ground). Right of U1 = Pi side (Pi ground). Never join the grounds on this board.",
            halign="left")
        d.save(str(HERE / "tach-input.png"), dpi=150)


def acc():
    with schemdraw.Drawing(file=str(HERE / "acc-sense.svg"), show=False) as d:
        d += elm.Label().label("ACC +12 V\n(key on / acc)", loc="left").at((0, 0))
        d += elm.Dot(open=True)
        d += elm.Fuse().right().label("F1 1 A\ninline (BUY)")
        d += elm.Resistor().right().label("R5 2.2k")
        d += (nB := elm.Dot().label("B", loc="top"))
        d += elm.Resistor().right().label("R6 2.2k")
        d += (nD := elm.Dot())
        d += elm.Line().right(0.6)
        u = opto(d, "U2 PC817").anchor("A")
        d += u
        d += elm.DiodeTVS().down().at(nB.start).label("TVS2\nP6KE12CA", loc="bottom")
        d += elm.Ground()
        d += elm.Line().down(1.6).at(u.K)
        d += (k := elm.Dot())
        d += elm.Ground().label("vehicle GND", loc="right")
        d += elm.Line().left(1.6).at(k.start)
        d += (kk := elm.Dot())
        # anode on pin 2 (K), cathode band toward pin 1 (A): conducts only if the LED is reverse-biased
        d += elm.Schottky().up().at(kk.start).toy(nD.start).label("D3 BAT43", loc="bottom")
        d += elm.Dot()
        d += elm.Line().right(1.2).at(u.C)
        d += (out := elm.Dot())
        d += elm.Resistor().right().label("R7 1k")
        d += (g := elm.Dot())
        d += elm.Line().right(0.8)
        d += elm.Dot(open=True).label("Pi pin 19\nGPIO 10", loc="right")
        d += elm.Capacitor().down().at(g.start).label("C2 0.1uF", loc="bottom")
        d += elm.GroundChassis()
        d += elm.Resistor().up().at(out.start).label("R8 10k", loc="bottom")
        d += elm.Dot(open=True).label("Pi pin 17  3.3 V", loc="right")
        d += elm.Line().right(1.2).at(u.E)
        d += elm.Line().down(1.0)
        d += elm.GroundChassis().label("Pi GND pin 20", loc="right")
        d.save(str(HERE / "acc-sense.png"), dpi=150)


def power():
    with schemdraw.Drawing(file=str(HERE / "agm-power.svg"), show=False) as d:
        d += elm.Label().label("AGM +12 V\n(constant)", loc="left").at((0, 0))
        d += elm.Dot(open=True)
        d += elm.Fuse().right().label("F2 5 A, within\n18 cm of battery (BUY)")
        d += elm.Line().right(1.5)
        d += (n1 := elm.Dot())
        d += elm.Line().right(4.5)
        d += (n2 := elm.Dot())
        d += elm.Line().right(3.2)
        d += (n3 := elm.Dot())
        d += elm.Line().right(1)
        d += elm.Dot(open=True).label("X1209 DC IN\n(5.5x2.1 jack)", loc="right")
        # Unidirectional TVS: cathode (band) to +12 V, anode to ground
        d += elm.Zener().up().at((n1.start[0], n1.start[1] - 3)).to(n1.start).label("TVS3 P6KE18A\n(unidirectional, BUY)\nband to +12 V", loc="bottom", ofst=0.3)
        d += elm.Ground().at((n1.start[0], n1.start[1] - 3))
        d += elm.Ground()
        d += elm.Capacitor(polar=True).down().at(n2.start).label("C3 2200uF\n35 V", loc="bottom")
        d += elm.Ground()
        d += elm.Capacitor().down().at(n3.start).label("C4 0.1uF", loc="bottom")
        d += elm.Ground().label("AGM - / chassis", loc="right")
        d += elm.Label().at((0.5, -4.6)).label(
            "Check polarity with a meter before connecting: Geekworm documents reverse protection for the X1209's batteries, not its DC input.\n"
            "Optional later: an IRF4905 reverse-polarity switch (needs a 15 V zener, not on hand).",
            halign="left")
        d.save(str(HERE / "agm-power.png"), dpi=150)


if __name__ == "__main__":
    for f in (tach, acc, power):
        f()
        print("drew", f.__name__)
