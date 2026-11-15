# Body: parts and wiring

The detailed self-model is `knowledge/hardware.yaml`. This page is the simple version.

This page and `hardware.yaml` describe the **first** Pip. Other robots are built from whatever parts their owners had:
a robot's own parts list is `data/body.yaml` (private, written by `tools/discover_parts.py`), and when that file
exists the robot's AI reads it instead of `hardware.yaml`. Known parts and how they are recognised:
`knowledge/parts.yaml`. Mounting boards on the frame: `docs/MOUNTING_BOARDS.md`.

## The map

```
Power (USB 5V) --> Orange Pi Zero --USB--> USB hub --> Webcam (camera + microphone)
                        |                     +--> Bluetooth dongle (speaker)
                        +--13-pin header USB3 (pins 5/6)--> CYD touchscreen --I2C (CN1)--> PCF8574 I/O (0x20)
                        |                                         |
                        |                                         +--> earpiece (the robot's voice)
                        +--13-pin header USB2 (pins 3/4)--> RP2040 Zero --> L293D board --> 2 wheel motors (on U5)
                        |                                         \--I2C1 (GPIO26 SDA, GPIO27 SCL)--> MPU-6050 IMU (0x68), since 2026-11-14
                        |   (CYD and RP2040 both take 5 V from 13-pin pin 1 and GND from pin 2; since 2026-11-13)
                                                                     ^-- 3S3P pack -> BMS -> INA219 -> 7.5 V regulator (motors); same INA219 output -> 5 V regulator (Pi)
Orange Pi 26-pin header pin 7 (PA6) <-- PIR motion sensor (VCC 5 V, GND; its output is 3.3 V)
```

The Orange Pi + USB hub + webcam are one detachable unit.

## What it looks like (photos: `knowledge/pics/front.jpg`, `front2.jpg`, `side.jpg` 2026-11-06; `with-wheels.jpg` 2026-11-08)

A small stack on the owner's desk, built on grey perforated plastic plates with black LEGO bricks at the corners:
- **Top:** the black onn. webcam on a black stand, held with zip ties, with a white USB cable looping round it.
- **Middle plate:** the Orange Pi Zero and the small boards, lots of purple, blue and orange jumper wires, the red LED
  of the tilt sensor, the black earpiece hanging off the left side.
- **Front:** the yellow CYD touchscreen on brass standoffs, tilted up, showing the face (eyes, mouth, TALK and MENU).
- **Underneath:** the L293D motor board (blue screw terminals) and red/black power wires.
- **Wheels (2026-11-08, photo `knowledge/pics/with-wheels.jpg`):** two big black rubber wheels on the sides with red
  hubs, on a red and blue brick frame. The front rests on two red spring-shock skids (no front wheel), so it drags a
  little. Motor power is an 11.1 V battery through a 7.5 V regulator.
- **Motion sensor:** a white-dome PIR sensor on the middle plate, fitted 2026-11-08.

## Parts

| Part | What it does | Status |
|---|---|---|
| Orange Pi Zero (H2+) | Runs everything (robotd, AI, web app) | working |
| USB hub | Connects webcam and Bluetooth dongle (the screen moved to the 13-pin header on 2026-11-13) | working |
| Webcam (onn., Sonix) | Camera up to 2592x1944, has a microphone | working |
| CYD touchscreen (ESP32) | Face, dashboard, touch, earpiece audio, I2C sensors. On the Pi's 13-pin header USB3 (pins 5/6) since 2026-11-13 | working |
| Earpiece | Plugged into the CYD speaker output. This is the voice. | working |
| MPU-6050 (GY-521) | Tilt / motion sensor. Since 2026-11-14 on the RP2040 motor board (5 V, GND, SDA = GPIO26, SCL = GPIO27), so the wheelie loop can read it 500 times a second (`motors.md`, Wheelies). Before that on the CYD I2C port. | working |
| PCF8574 | 8 digital inputs/outputs on the CYD I2C port (0x20) | P0-P3 drive the BLINKERS LED display from a used vape, through 1 kOhm each (since 2026-11-11, `blinkers.md`) |
| RP2040 Zero | Motor controller, on the Pi's 13-pin header USB2 (pins 3/4) | working, motors locked |
| L293D board | 8 motor channels (owner's design: github.com/cperos-xr/l293d) | ready |
| 2 wheel motors | On U5 (right-hand L293D): motor 7 = left, motor 8 = right, + = forward | first drive 2026-11-08; locked by default |
| Motor power | Since 2026-11-14 from the main pack: pack -> BMS -> INA219 -> 7.5 V regulator -> the motor board's EXT VOLTAGE (the 5 V regulator for the electronics sits on the same INA219 output). Before: an 11.1 V airsoft battery (bare 3S LiPo) through the same 7.5 V regulator. | since 2026-11-14 |
| PIR motion sensor | White dome; Pi 26-pin header pin 7 (PA6), VCC 5 V, GND (on 3.3 V it triggered itself) | works (cup test 2026-11-08); not used by robotd yet |
| Servos, PCA9685 | Planned | not installed |
| Main battery | Since 2026-11-14: home-made 3S3P pack of nine 260 mAh vape cells (780 mAh, 11.1 V nominal, 12.6 V full) + 3S protection board (BMS) -> INA219 (Pi I2C bus 0, 0x40, pins 3/5, 0.1 ohm shunt) -> 5 V regulator (Pi, screen, USB) + 7.5 V regulator (motors). Monitored by robotd/battery.py: level, warnings, clean shutdown at 3.3 V/cell, black box (`battery.md`). | installed 2026-11-14 |
| Pi analog line out | 13-pin header pins 7 + 8. Works but too quiet without an amplifier. | speaker removed |

## Pins worth knowing

- **CYD I2C (CN1):** IO27 = SDA, IO22 = SCL, plus 3.3V and GND.
- **CYD speaker output:** driven by an amplifier. Never connect either speaker wire to ground.
- **Pi 13-pin header:** pin 1 = 5V, pin 2 = GND, pins 3/4 = USB2 D-/D+ (RP2040), pins 5/6 = USB3 D-/D+ (CYD, since
  2026-11-13; both boards share pins 1 and 2 for power), pins 7/8 = line out R/L,
  pin 9 = TV out, pin 10 = MIC bias, pin 11 = MIC1P (mic in +), pin 12 = MIC1N (mic in -), pin 13 = IR receiver.
  (Corrected 2026-11-10: an earlier version swapped 11 and 12. The linux-sunxi wiki and the Orange Pi community docs
  both say 11 = MIC1P, 12 = MIC1N.)
  - Planned (2026-11-09): the owner's new microphone, on a cut 3.5 mm extension cable (female jack; wires: green,
    2 blue, 2 red, 2 copper). Identify the wires with a multimeter first (enamelled strands need scraping). Electret
    mic: mic + to pin 11, mic - / ground to pin 12 and pin 2, and bias from pin 10 through about 2.2 kOhm to mic +.
    Headphone L/R could go to pins 8/7. Software: ALSA card `Codec` has `Mic1` / `Mic1 Boost` capture; robotd's
    `MIC_DEVICE` (voice.py) would change from the webcam to `plughw:CARD=Codec,DEV=0`.

## Known limits (learned the hard way)

- **The old I2C power problem was a bad ground wire.** Readings of about 1.3 V where 5 V was expected and IMU resets came from the ground. The sensors then moved to the CYD's I2C port and worked with 0% errors. (2026-11-14: the owner moved the IMU on to the RP2040 for wheelies; the PCF8574 stays on the CYD.)
- **The CYD cannot do Bluetooth audio.** It was tried (firmware 0.5.0). The face canvas uses 70 KB, and Bluetooth needs more than 100 KB of the ~190 KB available, so it crashed. Bluetooth audio will go through the Pi with a USB dongle.
- **The Pi's line out cannot drive a bare speaker loudly.** It needs a small amplifier (for example a PAM8403).
- **The HM-11 module is Bluetooth Low Energy only.** It cannot play audio.
- **Power:** a weak USB supply causes resets. Suspect power first when things reboot.
- **The Pi has no real-time clock.** The time comes from the internet (NTP). With no internet after boot, the clock is wrong.
