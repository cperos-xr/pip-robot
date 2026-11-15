# Robot manual: START HERE

This manual is for any AI that works with this robot: the robot's own brain, RoboDev (its coding agent),
or a coding assistant on the owner's PC. Read this page first. Then read only the page you need.

Written 2026-11-06. If something here disagrees with the code, the code wins. Fix the manual when you notice.

## What the robot is

- A small home robot named **imalittlerobot**. It is still being built.
- It has a brain computer (Orange Pi Zero), a touchscreen face (CYD), a camera with a microphone, and a motor board.
- It can see, hear, talk, remember, run self-tests, post to Bluesky, and edit its own code.
- It has two wheels (since 2026-11-08). The motors are locked unless the owner unlocks them (see `motors.md`).
  Never say it moved unless it really did.

## The 10 golden rules

1. **Ask the owner first** before you flash firmware, change boot or system settings, enable motors,
   delete data, or spend money. Use the `ask_owner` tool (buttons on the screen).
2. **Motors stay locked.** `motors_enabled` is `false` until the owner turns it on. Do not change it.
3. **Never print, log or commit secrets.** Keys live in `/root/robot/data/secrets.env` (mode 600). The `data/` folder is never committed.
4. **The code is public. The robot's life is private.** This project is open source: what you commit can end up on the
   public internet for good. Never write private things into tracked files (code, this manual, comments, commit messages):
   names of the owner, family or pets, the town or address, Wi-Fi names, IP or MAC addresses, this robot's own web or
   email address, serial numbers, keys. They go in `data/` (never committed): see "This robot's own notes" below.
   Run `tools/privacy_scan.py` before you commit. Push only to `origin` (this robot's own repo); publishing to the
   public project is the owner's step (`tools/publish.sh`), never yours.
5. **No harm in public.** Never post nudity, cruel or humiliating pictures of people, addresses, house numbers,
   screens or mail. People are otherwise fine in a post **when the owner says it is OK** (then faces are not blurred and
   the owner taps Post; owner rule 2026-11-10, made clearer 2026-11-13: the rule was only ever about nudity and harm).
   Photos the robot takes on its own get faces blurred automatically.
   (The robot's own face and games may be shown: see `social.md`.)
6. **Keep answers short when speaking.** The voice is a tiny speaker: 1 to 3 plain sentences, no markdown.
7. **Read before you edit.** Make small changes. Keep the existing style.
8. **Test after you change something.** Run the matching self-test (see `api.md`).
9. **If a change breaks robotd, it rolls back by itself** (`tools/safe_restart.sh`). Do not fight the rollback. Read why it failed.
10. **Be honest.** If you do not know, say so, or read the right manual page.

## Which page to read

| You want to... | Read |
|---|---|
| Know the parts and the wiring | `body.md` |
| Know what THIS robot is made of (robots are built from different parts) | `data/body.yaml` (private; written by `tools/discover_parts.py`), parts catalog `knowledge/parts.yaml`, `docs/YOUR_PARTS.md` |
| Know how the software is organised, where files are, how to restart | `software.md` |
| Call the robot from code (web API, MCP tools, self-tests) | `api.md` |
| Understand the touchscreen and its messages | `screen.md` |
| Use or extend RoboOS, the tiny desktop on the screen | `roboos.md` |
| Find or add a setting (one list: screen, RoboOS and web app) | `settings.md` |
| Use the camera, microphone, voice, OCR, face tracking, IMU | `senses.md` |
| Pick or compare AI models, prices, benchmarks | `brain.md` |
| Find what was said before, or what the owner told me about people | `journal.md` |
| Post to Bluesky | `social.md` |
| Change code or firmware safely | `changing_things.md` |
| Use RoboDev, the robot's coding agent (and its own rules) | `robodev.md` |
| Drive motors (later) | `motors.md` |
| Light the salvaged BLINKERS LED display (and learn any salvaged display) | `blinkers.md` |
| Weather app and the weather on the BLINKERS panel | `weather.md` |
| Battery level, warnings, clean shutdown when empty, battery history (INA219) | `battery.md` |
| Read and write email (Pip's own address; the owner approves every send) | `email.md` |
| Share files with the owner (web file browser, shared folder `data/files`, opening a file the owner mentions) | `files.md` |
| Fix a problem | `troubleshooting.md` |
| Know what the owner likes and has decided | `owner.md` |

## Quick facts

| Thing | Value |
|---|---|
| Brain computer | Orange Pi Zero (Allwinner H2+, 32-bit ARM, 484 MB RAM), Armbian Linux |
| Robot code | `/root/robot` (git). Main program: `robotd` (Python 3.13), systemd service `robotd` |
| Web app (home Wi-Fi) | `http://pip.local:8080` (mDNS), or `http://<the robot's IP address>:8080` |
| Home Wi-Fi | 2.4 GHz only (the Wi-Fi chip has no 5 GHz). Hostname `pip`, `ssh root@pip.local`. The network's name is in this robot's own notes |
| Web app (internet) | Optional: a Cloudflare Tunnel to port 8080 (config `web.public_url`; the address is in this robot's own notes) |
| Owner's laptop link | Home Wi-Fi (`ssh root@pip.local`), or Ethernet cable: laptop `192.168.77.1`, robot `192.168.77.2`, or the USB serial console (`/dev/ttyACM0` on the laptop) |
| Screen | CYD (ESP32-2432S028R), 320x240 touch, USB serial at 921600 baud |
| Motor board | RP2040 Zero + 4x L293D (8 motor channels), USB serial |
| Sensors | MPU-6050 IMU and PCF8574 on the screen's I2C port (CN1). The PCF8574 drives the salvaged BLINKERS LED display (`blinkers.md`) |
| Voice out | Bluetooth speaker (via USB dongle) when connected, else the screen earpiece (`senses.md`). |
| Desktop | RoboOS on the screen (Settings > Desktop), USB mouse + keyboard work |
| Camera privacy | Taskbar camera icon in RoboOS, or `POST /api/camera {"privacy":true}` |
| Default AI | Claude Haiku 5.5 through OpenRouter (cheap); the web app's AI tab changes it (`brain.md`) |
| Repo | `origin` = this robot's own repo (its name is in this robot's own notes). The open-source project it comes from: top-level `README.md` |

## This robot's own notes (private)

The code and this manual are shared by every robot built from this project. What is true only for THIS robot lives in
`data/manual/` (never committed, never public):

- `data/manual/README.md`: its own quick facts (home Wi-Fi name, IP addresses, public web address, repo, accounts).
- `data/manual/owner.md`: what its owner wants and has decided.
- Any other page there, e.g. `data/manual/troubleshooting.md` for fixes that only make sense in this home.

`read_manual(page)` shows the shared page with this robot's own page of the same name added at the end, so you never
have to look in two places. When you learn something private about this robot or its home, write it THERE, not here.
