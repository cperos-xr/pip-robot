# Changing code and firmware safely

Follow these steps in order. Do not skip steps.

## Before you start

1. Read `README.md` (the golden rules).
2. Read the files you will change. Understand them first.
3. Things that need the owner's OK first (use `ask_owner`): flashing firmware, boot or system settings,
   enabling motors, deleting data, installing system packages, anything that costs money.

## Changing Python (robotd)

1. Make the smallest change that works. Keep the existing style.
2. Check it compiles: `cd /root/robot && venv/bin/python -m py_compile robotd/<file>.py`
3. Commit: `git add <files> && git commit -m "Short clear message"`
4. Restart safely: `tools/safe_restart.sh`
   - It restarts robotd and checks health for up to 90 seconds.
   - If robotd does not come up healthy, it **rolls back** to `data/last_good_commit` and keeps your commit on a `failed-*` branch.
5. Test: run the self-test that matches your change (`POST /api/test/<id>`), and read `/api/log`.
6. Check for private details: `venv/bin/python tools/privacy_scan.py` (the code is public: README rule 4).
7. Push to GitHub: `git push origin main` (`origin` is this robot's own repo; the owner publishes from there).

Never commit: `data/`, `venv/`, `models/`, `*.bin`, build folders, secrets.

## Changing firmware

1. Edit the source in `firmware/cyd_dashboard/` or `firmware/rp2040_motor_usb/`.
2. Bump the version (`FW_VER` in `firmware/cyd_dashboard/common.h`).
3. Commit and push. GitHub Actions builds both firmwares (artifact `firmware-<commit>`).
4. Ask the owner. Then `POST /api/firmware/fetch`, then `POST /api/firmware/flash` (it backs up the old firmware first).
5. If the build fails, do not flash. A broken flash can leave the screen dark.
6. Arduino gotcha: put `struct` and type definitions in `common.h`, or the Arduino prototype generator breaks the build.
7. CYD gotcha: memory is tight (see `screen.md`). Check `heap` in the `hello` message after flashing.
8. CYD flashing speed (2026-11-14): at 921600 baud the backup read corrupted or hung on this link; `flash_cyd`
   now uses 230400 (backup ~30 s, write ~15 s) and resets the chip after any failure. A black screen after a
   failed esptool run = the ESP32 is still in its bootloader, not broken: stop robotd, run
   `venv/bin/esptool.py --chip esp32 --port /dev/ttyUSB0 --baud 115200 --after hard_reset chip_id`, start robotd.
   Flash while the robot is on USB power: on the battery's 5 V regulator the Pi reset during a flash attempt.

## RoboDev (the robot changes itself)

- The robot's own coding agent: `robotd/agent.py`, RoboOS app RoboDev, web Dev tab, voice, MCP. See `robodev.md`.
- It follows this page. Its restart tool commits, writes `data/last_good_commit` and runs `tools/safe_restart.sh`;
  its build_firmware tool pushes and waits for GitHub Actions, then flash_firmware installs the image.

## Working from the owner's PC (Claude Code)

- The PC has a checkout of this repo with git remote `pi` = the robot (`ssh://root@pip.local/root/robot`).
- Get the latest: `git fetch pi && git reset --hard pi/main` (only when you have no local edits).
- Send changes: copy the changed files to the robot (tar over ssh), then commit and push from the robot.
- ssh: `ssh root@pip.local` or `ssh root@<robot-ip>` (home Wi-Fi), `ssh root@192.168.77.2` (Ethernet cable).

## After any change

- Add one line to `knowledge/experience_log.jsonl` if you learned something about the robot.
- Update this manual if you changed how something works.
