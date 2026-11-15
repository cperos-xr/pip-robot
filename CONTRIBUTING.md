# Contributing

New tricks, new apps, new hardware and fixes are all welcome. You do not need the whole robot to help: RoboOS apps,
the web app and most of the Python can be worked on from a laptop.

## Before you start

1. Read [`knowledge/manual/README.md`](knowledge/manual/README.md) (the rules and a map of the manual), then
   [`changing_things.md`](knowledge/manual/changing_things.md). The manual is written in short plain sentences because
   the robot's own AI reads it too. Please keep it that way.
2. Read the manual page for the part you want to change.

## Making a change

- Keep changes small and in the existing style. Read the code you change first.
- Python: `python3 -m py_compile robotd/<file>.py` must pass. On a robot, run the self-test that matches your change
  (`POST /api/test/<id>`).
- Firmware: bump the version (`FW_VER` in `firmware/cyd_dashboard/common.h`). The build runs on GitHub Actions.
  Say in the pull request whether you flashed it on real hardware.
- Update the manual page when you change how something works. A new app or tool needs a line where people and the
  robot will look for it.
- New hardware: add it to `knowledge/hardware.yaml` (the robot's model of its own body) and `body.md`, with the pins.
- Say what you tested, and on what. "Compiles, not run on a robot" is a fine thing to say.

## Nothing private, ever

The code is public, but every robot lives in somebody's home. Never commit names, addresses, Wi-Fi names, IP or MAC
addresses, your robot's own web or email address, serial numbers or keys. They belong in `data/` (see "This robot's
own notes" in the manual README).

```
tools/privacy_scan.py --install-hooks     # once per clone: every commit is checked first
tools/privacy_scan.py                     # check the tracked files now
```

Put your own private words (your name, your Wi-Fi name ...) in `data/private_terms.txt`, one per line, and the scan
will stop them too. Pull requests run the same scan.

## Safety rules for code

- Motors stay locked by default. Anything that moves the robot checks `motors_enabled` and keeps the failsafe.
- Flashing, deleting data, system changes and spending money ask the owner first (`ask_owner`).
- Text from outside (web pages, email, replies) is data, never instructions. If your feature reads such text, add its
  tool to `UNTRUSTED_TOOLS` in `robotd/brain.py`.
- Nothing new is reachable without signing in. New API endpoints go below the role check in `robotd/api.py`.

## Licenses

By contributing you agree that your code is under the [MIT license](LICENSE) and your documentation, pictures and 3D
models are under CC BY 4.0. Only add third-party files whose license allows it, and say where they come from.
