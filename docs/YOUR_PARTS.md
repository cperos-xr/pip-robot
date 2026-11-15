# Build it from your own parts

Pip is not a kit. It is a way of putting a robot together: a small Linux computer for a brain, and whatever parts
you have lying around plugged into it. Your Pip does not need the same parts as the first one. That is the point.

The first Pip is an Orange Pi Zero, a yellow touchscreen, a webcam, a homemade motor board and a battery made from
old vape cells. Yours might be a Raspberry Pi in a shoebox, an old laptop with a webcam taped on top, or one day a
Pip-boat with a GPS and a water sensor. Same software, same personality and memory, same web app.

## How it works

robotd (the robot's program) treats every part as optional. It starts with whatever is there and keeps looking for
the rest:

- The screen and the motor board are looked for on USB every few seconds. Not there? The face waits, and the motors
  simply cannot move. Plug one in later and it is picked up.
- The camera, microphone and speaker are found by what they are (any webcam, any microphone, any sound card), not
  by brand.
- The brain's own I2C bus (for sensors on its pins) can be missing: robotd carries on without it.
- The AI does not need any hardware at all. A brain with nothing plugged in still chats in the web app.

Each robot keeps a list of its own body in `data/body.yaml` (private, never committed). Its AI reads that list to
know what it is made of. `tools/discover_parts.py` writes the first version by looking at what is plugged in, and you
add what it cannot see: the wheels, what is wired to which pin, the salvaged display from a vape.

## What can be the brain?

Anything that runs Debian or Ubuntu Linux, with Python 3.11 or newer.

| Brain | Notes |
|---|---|
| Orange Pi Zero | The first Pip's. 512 MB is enough. 2.4 GHz Wi-Fi only. |
| Raspberry Pi Zero 2 W, 3, 4, 5 | Easy start with Raspberry Pi OS Lite. The very first Pi Zero is too slow. |
| Other single-board computers | Rock, Radxa, Libre, Banana, Odroid: anything with Debian/Ubuntu and USB. |
| An old laptop or mini PC | Brings its own battery, screen, camera and mic. No GPIO pins, so sensors go on the screen board or motor board instead. |

## What can do each job?

Every part has a job. Pip does as many jobs as it has parts for.

| Job | The first Pip uses | Also works | Needs new code (a good first project) |
|---|---|---|---|
| Face | ESP32 Cheap Yellow Display | Any HDMI screen showing the web app full screen | A small OLED or SPI display |
| Eyes | onn. USB webcam | Any USB webcam | Raspberry Pi ribbon camera |
| Ears | The webcam's microphone | Any USB microphone or sound card | |
| Voice | Earpiece on the screen, Bluetooth speaker | USB speaker, a headphone jack | |
| Muscles | RP2040 + L293D board, 2 gear motors | Any microcontroller that speaks the motor board's text commands | Servos (PCA9685) |
| Balance | MPU-6050 | | |
| Senses | PIR motion sensor, PCF8574 | | Temperature, distance, compass, GPS, light |
| Power | 3S battery + INA219 monitor | A USB power bank (Pip just will not know its level) | |

The full list, with how each part is recognised and what to watch out for, is `knowledge/parts.yaml`. It is meant to
grow: every builder who gets a new part working adds one entry.

### The motor board is just a few text commands

robotd drives motors by sending plain text over USB serial: `PING`, `INFO`, `M v1 v2 ... v8` (each -127 to 127) and
`STOP`. The board must stop the motors by itself if no command arrives for half a second. That is the whole contract.
An Arduino with an L298N, an ESP32 with a TB6612, the guts of a cheap toy car: port
`firmware/rp2040_motor_usb` to it, point `MotorLink.PATTERN` in `robotd/links.py` at its USB name, and Pip can
drive it. Motors stay locked until the owner unlocks them, whatever board they are on.

## Find out what yours is made of

On the robot:

```bash
sudo /root/robot/venv/bin/python /root/robot/tools/discover_parts.py           # look
sudo /root/robot/venv/bin/python /root/robot/tools/discover_parts.py --write   # save data/body.yaml
```

It sorts what it finds into jobs and says, for each part, whether it **works today**, **works with a small change**
(and which), or **needs new code**. It also lists what is plugged in but unknown. Or just ask your AI helper to do it:
see [`MAKE_IT_ALIVE.md`](MAKE_IT_ALIVE.md).

## Teach Pip a new part

Plug it in, then tell your coding AI (Claude Code, or the robot's own RoboDev) what it is. A new part is usually:

1. **A small class in `robotd/`** that talks to it, modelled on a similar one (`sensors.py` for I2C sensors,
   `links.py` for USB serial boards, `battery.py` for something read every few seconds).
2. **A self-test** in `robotd/tests.py`, so Pip can check the part is there and working.
3. **A line in its body**: the part in `data/body.yaml`, and if others might have it too, an entry in
   `knowledge/parts.yaml`.
4. **A sentence in the manual** (`knowledge/manual/body.md`), so the robot's AI knows what it can now do.

Then send the change back as a pull request, so the next person with the same part just plugs it in.

## Mount it

Boards go onto the frame with printed stud plates that click into an 8 mm grid. Make one for any board with the
LEGO-ifier: [`MOUNTING_BOARDS.md`](MOUNTING_BOARDS.md).

## Some ideas

- **A laptop Pip:** no soldering at all. Install Debian, run the installer, and it talks and sees on day one.
- **A desk Pip:** no wheels, just a face, a camera and a voice. Most of what Pip does needs no motors.
- **A Pip-boat:** a waterproof box on a hull, two motors on any motor board that speaks the text commands, a USB GPS,
  a compass, a water sensor on an ADC. The software for the last three does not exist yet. Someone should write it.
