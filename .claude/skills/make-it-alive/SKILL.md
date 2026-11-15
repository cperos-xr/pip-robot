---
name: make-it-alive
description: Bring a newly built Pip robot to life from the owner's PC. Use when the owner says "make it alive", "set up my robot", "install Pip", "bring it up", "I plugged it in, now what", or has a brain computer (Raspberry Pi, Orange Pi, any small Linux board or old laptop) connected over Wi-Fi, an Ethernet cable or USB and wants Pip's software on it. Finds the board, installs everything, discovers which parts it was built from, gives it an AI key, and has it say its first words.
---

# Make it alive

The owner has plugged their parts together and connected the brain computer to this PC. Your job: get Pip running on
it, find out what it is made of, and hear it speak. Every Pip is built from whatever parts its owner had, so never
assume the first Pip's parts list: look, then ask.

Work in this order. Talk to the owner in short plain sentences; they may never have used SSH. Say what you are about
to do before each phase, and what you found after it.

## Rules (from the manual: `knowledge/manual/README.md`)

- **Ask first** before: changing boot settings (overlays, `--enable-i2c`), renaming the computer (`--hostname`),
  flashing firmware, enabling motors, deleting anything. Motors stay locked; never unlock them during bring-up.
- **Never print, log or repeat a secret** (API keys, `data/api_token`, passwords). Don't `cat` secret files to the
  terminal; pipe them where they need to go.
- **Private stays private.** Wi-Fi names, addresses, the owner's name go in the robot's `data/` folder, never in
  tracked files. You are not here to commit anything to this repo.
- You cannot type passwords into interactive prompts. When a step needs one, give the owner the exact command to run
  in their own terminal, and wait.

## Phase 0: what do we have?

1. Ask the owner (one message, one short list):
   - Which **brain**? (Raspberry Pi model, Orange Pi, another board, an old laptop.)
   - Does it already have an **operating system** on its SD card, and can it reach the internet?
   - How is it **connected** to this PC: same Wi-Fi, Ethernet cable, or USB cable?
   - What **parts** are plugged in? A photo is perfect. (Screen, camera, speaker, motors, sensors, anything salvaged.)
2. Check this PC: `python --version` (or `python3`), `ssh -V`. The helper scripts need Python 3 on the PC; without it,
   do Phase 2 by hand (below).

## Phase 1: an operating system on the brain (skip if it already boots and the owner can reach it)

Pip runs on any Debian-family Linux, Python 3.11 or newer.

- **Raspberry Pi** (Zero 2 W, 3, 4, 5): Raspberry Pi Imager, *Raspberry Pi OS Lite (64-bit)*. In its OS
  customisation: hostname `pip`, a username and password, the Wi-Fi (country too), and *Enable SSH* with
  *public-key authentication*: paste this PC's public key. Make one if there is none:
  `ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519` (ask first), then show them `~/.ssh/id_ed25519.pub` (public, fine
  to show). A first-generation Pi Zero / Pi 1 is too slow: say so.
- **Orange Pi and other boards**: the Armbian image for that board (minimal/CLI), written with balenaEtcher or
  Raspberry Pi Imager ("use custom"). First login is interactive (`ssh root@<address>`, password `1234`, then it asks
  for a new root password and a user): the owner does that in their own terminal. An Orange Pi Zero has 2.4 GHz
  Wi-Fi only.
- **A laptop or mini PC**: Debian or Ubuntu (server or desktop), with `openssh-server` installed.

## Phase 2: find it

Run `python tools/find_brain.py` (add `--lan` if nothing shows up). It finds the brain by name (`pip.local`,
`raspberrypi.local` ...), on a direct cable, on a shared connection, and among recently seen machines, and it says if
Pip is already alive there (web app answering on port 8080).

If nothing is found, go through this with the owner:
- First boot can take 2-5 minutes. Is a light blinking?
- **Ethernet cable to this PC**: turn on internet sharing for that cable, so the brain gets an address AND internet
  through the PC. Windows: Control Panel > Network Connections > right-click the Wi-Fi adapter > Properties > Sharing >
  allow, and pick the Ethernet adapter. macOS: System Settings > General > Sharing > Internet Sharing. Linux:
  NetworkManager > the wired connection > IPv4 > "Shared to other computers". Wait a minute, run the finder again.
- **USB cable**: only boards that can act as a USB network adapter (Raspberry Pi Zero / Zero 2 W in gadget mode,
  set up on the SD card) show up this way; others need Wi-Fi or Ethernet.
- Without Python on this PC: `ping pip.local`, `ping raspberrypi.local`, `arp -a`, or the router's device list.

## Phase 3: get in without a password

- Test: `ssh -o BatchMode=yes -o ConnectTimeout=8 USER@HOST true` (USER is `root` on Armbian, the chosen user on
  Raspberry Pi OS).
- If it asks for a password, have the owner copy this PC's key over in their own terminal:
  - macOS / Linux: `ssh-copy-id USER@HOST`
  - Windows PowerShell: `type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh USER@HOST "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"`
- Not root? Check `ssh USER@HOST sudo -n true`. Raspberry Pi OS allows this by default. If it asks for a password,
  the owner runs the installer themselves: `ssh -t USER@HOST sudo bash /tmp/install.sh --yes`.
- Use `ssh -o BatchMode=yes` for every later command so nothing ever hangs on a prompt.

## Phase 4: install

1. `scp tools/install.sh USER@HOST:/tmp/install.sh`
2. `ssh USER@HOST "sudo bash /tmp/install.sh --check"`: shows what is there and what is missing, changes nothing.
3. `ssh USER@HOST "sudo bash /tmp/install.sh --yes"`. If the owner has their own fork, add
   `--repo <their fork's https URL>` (check `git remote -v` in this checkout). Run it in the background: on a small
   board the Python packages take 5-20 minutes. It ends with `PIP-INSTALL: ok` or `PIP-INSTALL: failed (why)`.
4. If it fails, read the lines above the FAIL, fix the cause (clock, internet, disk, an old Python), and run it again:
   it skips what is already done.
5. `TODO` lines about board settings (Orange Pi overlays, Raspberry Pi I2C) are boot settings: explain in one
   sentence what they are for and ask before applying. Only needed if those parts are present.

## Phase 5: what is it made of?

1. `ssh USER@HOST "sudo /root/robot/venv/bin/python /root/robot/tools/discover_parts.py"`
2. Tell the owner, in plain words, by role: brain, face, eyes, ears, voice, muscles, balance, senses, power. Per part:
   *works today*, *works with a small change* (say the change), or *needs new code*. Then, from their answer in
   Phase 0, name the parts it could not see (wheels and motors, a PIR sensor on a pin, a speaker on the screen board,
   anything salvaged) and the "plugged in but unknown" ones: ask what they are.
3. Save it: `... discover_parts.py --write` writes `/root/robot/data/body.yaml` (private). Then add what the owner told
   you under `owner_notes` (what is wired where, which motor is which wheel, the frame it sits on), and any part it
   could not detect as an entry with `source: owner`. robotd's AI reads this file to know its own body.
4. Start the robot's own private notes, `/root/robot/data/manual/README.md`: the computer's name, how it is reached
   (Wi-Fi / cable), the board, the date it came alive. Short.
5. Parts marked *needs new code* or *works with a small change*: don't do them now. List them at the end as offers.

## Phase 6: firmware for the small boards (only if present; ask first)

- **Cheap Yellow Display** (the touchscreen face) and **RP2040 motor board** need their firmware once:
  `docs/INSTALL.md` step 7 (board settings and library versions; `.github/workflows/firmware.yml` has the exact ones).
  The simplest first time is from this PC with `arduino-cli` or the Arduino IDE, the board plugged into the PC.
  An RP2040 shows up as a drive called RPI-RP2 when it is waiting for firmware: copy the `.uf2` onto it.
- Never flash anything without the owner saying yes. After flashing, plug it back into the brain and run discovery again.

## Phase 7: a brain for the brain (the AI key)

- Pip needs ONE key: OpenRouter (any model, cheapest start) or Anthropic. The owner makes it at openrouter.ai or
  console.anthropic.com.
- Best: the owner types it in themselves, in their own terminal:
  `ssh -t USER@HOST sudo nano /root/robot/data/secrets.env` (fill `OPENROUTER_API_KEY=` or `ANTHROPIC_API_KEY=`,
  Ctrl+O, Enter, Ctrl+X).
- If they paste the key to you instead, write it without echoing it (for example with `sed -i` over ssh, the key
  only inside the command), never repeat it back, and tell them it is in the transcript now, so they may want to
  rotate it later.
- Then `ssh USER@HOST "sudo systemctl restart robotd"` and wait until `http://HOST:8080/` answers (up to a minute).

## Phase 8: it's alive!

1. Get the API token onto this PC without printing it:
   `ssh USER@HOST "sudo cat /root/robot/data/api_token" > ~/.config/ai-multi-bot/robot_token` (make the folder first;
   on macOS/Linux `chmod 600` it). Then use it from the file, never on screen: `T=$(cat ~/.config/ai-multi-bot/robot_token)`.
2. Its first words: `curl -s -H "X-Robot-Token: $T" -H "Content-Type: application/json" -d '{"text":"...","mood":"happy"}' http://HOST:8080/api/say`
   Make the sentence about its own body, from Phase 5: "I'm alive! I can see you with my webcam and I have a
   Bluetooth voice." Keep it short. It speaks through whatever voice it has, and shows it on its screen if it has one.
3. Self-tests: `GET /api/tests` lists them; run the ones for parts it has: `POST /api/test/<id>`. Report the results
   plainly. Never run a motor test without the owner there and the wheels off the ground; motors stay locked anyway.
4. Chat with its own mind once: `POST /api/chat {"text":"Hello! What are you made of?"}` and show the owner the answer.
5. Connect Claude Code to it for next time:
   `claude mcp add pip -e ROBOT_URL=http://HOST:8080 -- python <absolute path to>/mcp/robot_mcp.py`
   (it reads the token from `~/.config/ai-multi-bot/robot_token`). The tools appear after Claude Code restarts.
6. Tell the owner how to meet it on their phone: `http://HOST:8080`. With a touchscreen: Menu > About > PAIR A PHONE,
   scan the code. Without one: type the API token as the password (show them where it is: `/root/robot/data/api_token`
   on the robot, `~/.config/ai-multi-bot/robot_token` on the PC, without printing it), then set an owner password in
   Settings.

## Phase 9: wrap up

Give the owner a short summary:
- what works now,
- what it found but cannot use yet, each with one sentence on what it would take (offer to write it: a new part is
  usually one small class in `robotd/`, one self-test, one line in `knowledge/parts.yaml`; see `docs/YOUR_PARTS.md`),
- what is next: a fork of the repo so the robot can push its own changes (`GITHUB_REPO` in `secrets.env`), the
  printable body (Thingiverse link in `README.md`), custom mounting plates (`docs/MOUNTING_BOARDS.md`).

If a step in this guide was wrong for their hardware, tell them so, and suggest fixing this file in a pull request:
the next builder will have the same problem.
