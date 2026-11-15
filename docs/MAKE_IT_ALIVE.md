# Make it alive

The easy way to install Pip: plug your parts together, connect the brain to your computer, and ask a coding AI to do
the rest. No Linux experience needed.

## What you need

- A **brain**: a Raspberry Pi (Zero 2 W or newer), an Orange Pi, another small Linux board, or an old laptop.
  With an SD card if it is a board (8 GB or more).
- **Whatever parts you have:** a webcam, a speaker, a screen, motors, sensors. None of them is required. See
  [`YOUR_PARTS.md`](YOUR_PARTS.md).
- **Your computer** (Windows, macOS or Linux) with this repo on it (`git clone https://github.com/cperos-xr/pip-robot`)
  and a coding AI: [Claude Code](https://claude.com/claude-code) knows this repo best, but any coding agent that can
  run commands will do.
- **One AI key** for the robot's own mind, from [OpenRouter](https://openrouter.ai) (any model; a cheap one is
  plenty) or [Anthropic](https://console.anthropic.com).

## 1. Plug it together

Plug the parts into the brain, mostly by USB. The wiring the first Pip uses is in
[`knowledge/manual/body.md`](../knowledge/manual/body.md), but you do not have to copy it. Mount the boards on
printed stud plates if you like: [`MOUNTING_BOARDS.md`](MOUNTING_BOARDS.md).

## 2. Give the brain an operating system

- **Raspberry Pi:** use Raspberry Pi Imager to write *Raspberry Pi OS Lite (64-bit)*. Before writing, open its
  settings and set: hostname `pip`, a username and password, your Wi-Fi, and *Enable SSH*.
- **Orange Pi and other boards:** write the board's Armbian image (minimal) with balenaEtcher or Raspberry Pi Imager.
- **A laptop:** install Debian or Ubuntu, with the SSH server.

If you are not sure, skip this step: your AI helper will walk you through it.

## 3. Connect it to your computer

Any one of these:

- **The same Wi-Fi** as your computer (set in step 2).
- **An Ethernet cable** straight from the brain to your computer. Turn on internet sharing for that cable, and the
  brain gets internet through your computer too.
- **A USB cable**, for a Raspberry Pi Zero set up as a USB network adapter.

Power it on and give it a few minutes for its first boot.

## 4. Ask your AI to make it alive

Open a terminal in this repo's folder, start your coding AI, and say:

> Make it alive.

With Claude Code you can also type `/make-it-alive`. The AI will:

1. ask what you built (a photo of it helps),
2. find the brain on your network (`tools/find_brain.py`),
3. install everything on it (`tools/install.sh`),
4. look at what is plugged in and tell you what each part can do (`tools/discover_parts.py`),
5. ask you to put your AI key in,
6. and have your robot say its first words.

It asks you before anything risky: changing boot settings, flashing firmware, unlocking motors. When it needs a
password, it tells you the exact command to type yourself.

The steps it follows are in [`.claude/skills/make-it-alive/SKILL.md`](../.claude/skills/make-it-alive/SKILL.md).
Other AI agents can follow the same file.

## 5. Meet it

Open `http://pip.local:8080` on your phone (or the address your AI tells you). If it has the touchscreen, pair your
phone from its screen: Menu > About > PAIR A PHONE. Then say hello.

## Doing it by hand

Everything the AI does is in [`INSTALL.md`](INSTALL.md), step by step. The shortcut on the brain itself:

```bash
curl -fsSL https://raw.githubusercontent.com/cperos-xr/pip-robot/main/tools/install.sh | sudo bash
sudo /root/robot/venv/bin/python /root/robot/tools/discover_parts.py --write
```

## Something did not work?

Tell your AI what happened: it can read the robot's logs and the manual. If a step was wrong for your hardware,
please fix it in a pull request (or open an issue). The next builder will have the same problem.
