# BLINKERS salvaged LED display — robot agent handoff

## Immediate task: four-wire battery-segment proof of concept

**Objective:** With the existing Cheap Yellow Display (CYD/ESP32) and its attached HLF8574T / PCF8574-compatible I²C expander, test whether one battery indicator LED can be illuminated. **Do not modify the Orange Pi Zero or RP2040 wiring or firmware.** Preserve the CYD's existing firmware/configuration; first inspect its project and I²C setup, and only deploy a small test if safe and authorized by the operator.

### Physical connections (four display signal wires, each with its own resistor)

| PCF8574 expander output | Series resistor | BLINKERS display contact | Function when contact 4 is LOW |
|---|---:|---|---|
| P0 | 1 kΩ | 1 | 100% battery bar |
| P1 | 1 kΩ | 2 | 75% battery bar |
| P2 | 1 kΩ | 3 | 50% battery bar |
| P3 | 1 kΩ | 4 | common cathode for battery bars |

Leave display contacts 5–8 unconnected for this test. The four wires refer to expander-to-display signal connections; existing expander VCC/GND/SDA/SCL wiring stays connected. **No direct 3.3 V or 5 V connection to a display contact.** Ensure expander VCC and I²C pull-ups are at compatible 3.3 V levels before powering.

### Agent execution plan

1. Identify the CYD firmware environment (Arduino, ESP-IDF, MicroPython, etc.) and the *actual* SDA/SCL pins already in use. Do not assume `Wire.begin()` defaults or reconfigure an active I²C bus without checking.
2. Identify the PCF8574's actual I²C address by scanning the existing bus. The module has A0/A1/A2 address jumpers; do not assume `0x20`. Check whether other expanders on the bus share that address. Do not change address jumpers while powered.
3. Confirm the connected expander is not currently driving other hardware on P0–P3. Ensure the operator has installed four 1 kΩ series resistors and verified display pin numbering/orientation.
4. Set all eight PCF8574 port bits HIGH (`0xFF`) first. This is the quasi-bidirectional released state, **not** a true high-impedance or strong push-pull HIGH state.
5. With the wiring table above, write `0xF7` (P3 LOW, P0/P1/P2/P4–P7 HIGH). This attempts to light the 100% bar at display pin 1 relative to cathode pin 4. Hold for about 0.5–1 second, then write `0xFF` to release all pins. Do not leave the display continuously on while unattended.
6. Optional single-bar trials: `0xF7` for pin 1, `0xF7` still leaves all three anodes HIGH and **may light all three battery bars at once**. **Important:** The PCF8574 cannot independently turn the other bars off by writing HIGH, because HIGH is weakly sourced. For this four-wire experiment, `0xF7` is a combined three-bar test, not an isolated 100% test. To test just one bar reliably, temporarily disconnect the other two anode wires, leaving the P3-to-display-4 cathode wire and one anode wire in place. Alternatively test the bars individually using separate two-wire configurations.
7. Record I²C address, bus voltage, firmware environment, whether any bar lights, brightness, current if measurable, and any unexpected glow. If no illumination, do **not** automatically lower resistor values or raise supply voltage; the expander's HIGH current source may be too weak.
8. Restore the expander output to `0xFF` after testing. Avoid disturbing unrelated devices and report results before any larger driver implementation.

### Minimal logic (pseudocode; adapt to existing CYD project)

```text
address = discover_existing_pcf8574_address()
i2c_write_byte(address, 0xFF)  # all released / weak HIGH
sleep(0.1)
i2c_write_byte(address, 0xF7)  # P3 LOW; other bits weak HIGH
sleep(0.5)
i2c_write_byte(address, 0xFF)  # release again
```

**Success criteria:** One or more battery bar LEDs visibly illuminate, without abnormal heating, I²C errors, or unintended effects on the robot. Failure to illuminate is inconclusive because the PCF8574 cannot source normal LED drive current. This is an exploratory test, **not** a validated production drive design.

---

## Hardware inventory and architecture (reported/observed)

- Orange Pi Zero powers the RP2040 via its USB header pins and powers the Cheap Yellow Display (CYD) via a USB port. Existing arrangement should remain unchanged for this test. The precise CYD variant and firmware framework are not yet confirmed.
- RP2040 has only GP26, GP27, GP28 and GP29 available; none are needed for this experiment.
- CYD is an ESP32-based touchscreen board with an I²C-connected PCF8574-compatible expander. The user has shown **three** blue modules marked **HLF8574T** (not all necessarily connected). Modules expose P0–P7, INT, VCC/GND/SDA/SCL and A0/A1/A2 address jumpers.
- The user also owns a PCA9548A, which is an I²C bus switch, **not** a general-purpose GPIO or LED driver.
- Salvaged display came from a BLINKERS-branded vape. Original controller board labeled **LY-HR37A Ver.02**, with **LED1** eight-contact footprint, U1 IC, and other LEDs. Small separate green board labeled **HR-D378**.
- Display is white, and user reports illumination at approximately **2.75 V** in bench testing. This is a reported test point, not a verified operating voltage or current rating. Current limiting is required.

## Display electrical model and caveats

- Eight display contacts, with directional LED paths identified by applying positive voltage to a source contact and negative to a selected cathode contact. There is **no dedicated ground pin** for the display.
- Mapping is consistent with a multiplexed/charlieplexed LED network; it is **not yet proven** that all LED paths are independent or free of ghosting.
- PCF8574-compatible HLF8574T has **quasi-bidirectional** outputs: LOW sinks current, HIGH provides weak current source and is not equivalent to a normal RP2040/ESP32 push-pull HIGH or a true high-impedance output. Therefore direct LED scanning may be extremely dim or fail. Do not claim it is a confirmed suitable driver.
- For a future proper driver, use bidirectional tri-state-capable GPIO or appropriate LED driver hardware, with verified output-current ratings. Eight resistors, one in series with each contact, are a conservative experimental approach; each active two-pin path then traverses two resistors.

## Full measured mapping

**Notation:** For each cathode/LOW contact, the listed other contact is the anode/HIGH contact. Numbers are **physical display contacts 1–8**, not GPIO numbers. The user mapped these by systematically applying ground to each contact and ~3 V positive to others. Descriptions below retain their original observations; no unobserved features are invented.

### Three seven-segment numeric digits

Segment names: A=top, B=upper right, C=lower right, D=bottom, E=lower left, F=upper left, G=middle.

| Segment | Digit 1: cathode 1, anode | Digit 2: cathode 2, anode | Digit 3: cathode 3, anode |
|---|---:|---:|---:|
| A | 2 | 1 | 1 |
| B | 3 | 3 | 2 |
| C | 4 | 4 | 4 |
| D | 5 | 5 | 5 |
| E | 6 | 6 | 6 |
| F | 7 | 7 | 7 |
| G | 8 | 8 | 8 |

All 21 segment paths are mapped. The apparent regularity is real but note the cathode changes, and segment A/B assignments follow the explicit measurements above.

### Battery display, cathode contact 4

| Anode contact | Observed feature |
|---:|---|
| 1 | 100% battery fill |
| 2 | 75% battery fill |
| 3 | 50% battery fill |
| 4 | same contact / cathode; no test |
| 5 | 25% battery fill |
| 6 | battery outline top |
| 7 | battery outline right |
| 8 | battery outline right lower corner |

### Battery outline and head, cathode contact 5

| Anode contact | Observed feature |
|---:|---|
| 1 | battery outline bottom |
| 2 | battery outline left lower corner |
| 3 | battery outline left upper corner |
| 4 | head illuminated |
| 5 | same contact / cathode |
| 6 | head illuminated |
| 7 | head illuminated |
| 8 | head illuminated |

### Head and BLINKERS lettering, cathode contact 6

| Anode contact | Observed feature |
|---:|---|
| 1 | head center |
| 2 | head right |
| 3 | head lower left |
| 4 | head lower left (possibly another LED / effect) |
| 5 | head right lower corner |
| 6 | same contact / cathode |
| 7 | BLINKERS text |
| 8 | BLINKERS text |

### BLINKERS lettering, cathode contact 7

| Anode contact | Observed feature |
|---:|---|
| 1 | BLINKERS text |
| 2 | BLINKERS text right |
| 3 | none observed |
| 4 | none observed |
| 5 | none observed |
| 6 | none observed |
| 7 | same contact / cathode |
| 8 | none observed |

### Cathode contact 8

No LED illuminated for any tested positive contact 1–7. Contact 8 may act only as an anode in the observed wiring.

## Future expansion after successful proof of concept

For all display features, connect PCF8574 P0–P7 via eight individual current-limiting resistors to display contacts 1–8 respectively, **only after** confirming adequate source current, brightness and absence of ghosting. The PCF8574 may not be electrically suitable for full multiplexed operation. A dedicated tri-state GPIO controller or proper LED driver is preferable for full animations. No need to modify Orange Pi/RP2040 power topology to run this first test.

## Open questions / evidence to collect

- Actual CYD model and firmware toolchain; existing SDA/SCL pins and PCF8574 address.
- How many HLF8574T modules are currently connected; whether their I²C addresses conflict.
- Expander module supply voltage and I²C pull-up arrangement. Onboard resistors photographed with `102` marking may be 1 kΩ; multiple boards in parallel can create overly strong pull-ups.
- Measured LED current and voltage for a single battery segment at visible brightness.
- Whether graphics contain parallel/series LEDs, and whether multiplexing causes ghosting.
