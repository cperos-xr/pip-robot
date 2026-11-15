# Installing the software on a new robot

These steps were written from the first robot's working setup. They have **not** been replayed on a blank SD card
yet. If a step fails for you, please open an issue (or a pull request that fixes this page).

You need: the assembled robot ([`knowledge/manual/body.md`](../knowledge/manual/body.md)), a microSD card (8 GB or
more), and a computer on the same network.

## 1. The operating system

1. Write an **Armbian** image for the **Orange Pi Zero** (Debian, minimal / CLI, "current" kernel) to the SD card.
   The first robot runs Armbian "trixie" on the 32-bit H2+ board with Python 3.13.
2. Boot, sign in as root (serial console or SSH), finish Armbian's first-run questions, and put the board on your
   **2.4 GHz** Wi-Fi (`armbian-config`): the built-in Wi-Fi chip cannot use 5 GHz.
3. Name it: `hostnamectl set-hostname pip`. The docs assume the robot answers at `pip.local`.
4. Use SSH keys, then turn password sign-in off:

   ```bash
   ssh-copy-id root@pip.local                      # on your computer
   # on the robot: set "PasswordAuthentication no" in /etc/ssh/sshd_config, then
   systemctl restart ssh
   ```

## 2. Boot settings (USB ports, I2C, audio)

The touchscreen and the motor board plug into the Pi's 13-pin header, which needs two extra USB ports switched on.

```bash
# /boot/armbianEnv.txt must have these (keep overlay_prefix=sun8i-h3 as it is):
#   overlays=usbhost2 usbhost3 i2c0 analog-codec
armbian-add-overlay /root/robot/tools/system/i2c0-robot.dts     # after step 4: slow I2C with pull-ups for the battery monitor
reboot
```

## 3. Packages

```bash
apt update
apt install -y git python3-venv python3-pip python3-numpy python3-opencv opencv-data python3-pil python3-serial \
  python3-requests python3-yaml python3-smbus2 python3-libgpiod gpiod i2c-tools usbutils v4l-utils alsa-utils \
  espeak-ng ffmpeg tesseract-ocr tesseract-ocr-eng sqlite3 picotool
# the Game Boy app compiles its emulator on the robot the first time it runs:
apt install -y gcc libc6-dev
# optional, for a Bluetooth speaker through a USB dongle:
apt install -y bluez bluez-alsa-utils libasound2-plugin-bluez
```

## 4. The robot's code

The code expects to live in `/root/robot` and to run as root (it flashes boards, restarts itself and reads GPIO).

```bash
git clone https://github.com/cperos-xr/pip-robot.git /root/robot      # or your own fork
cd /root/robot
python3 -m venv --system-site-packages venv
venv/bin/pip install -r requirements.txt
```

Speech recognition model (about 70 MB unpacked), from https://alphacephei.com/vosk/models :

```bash
mkdir -p models && cd models
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip
cd ..
```

## 5. Secrets and settings

```bash
mkdir -p data && cp tools/secrets.env.template data/secrets.env && chmod 600 data/secrets.env
nano data/secrets.env        # ONE AI key is enough to start: OpenRouter or Anthropic
```

Everything in `data/` belongs to your robot only and is never committed: keys, memory, photos, settings
(`data/config.json`) and your robot's own manual pages (`data/manual/`).

Settings worth setting early (stop robotd before you edit `data/config.json` by hand, or use the web app):

```json
{ "tz": "Europe/Berlin" }
```

## 6. Start it

```bash
cp tools/robotd.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now robotd
```

The first start takes up to a minute. Then:

1. Open `http://pip.local:8080` (or `http://<the robot's IP>:8080`) on your phone.
2. On the robot's screen: **Menu > About > PAIR A PHONE**, scan the QR code. Your phone is now signed in as the owner.
3. In the web app: **Settings**, set an owner password (a long passphrase).
4. **Body > Tests**: run the self-tests. They tell you what is connected and working.

Nice to have:

```bash
# pip.local on the home network, and "Pip the robot" in network browsers
mkdir -p /etc/systemd/resolved.conf.d /etc/systemd/network/25-wlan0.network.d /etc/systemd/dnssd
printf '[Resolve]\nMulticastDNS=yes\n' > /etc/systemd/resolved.conf.d/mdns.conf
printf '[Network]\nMulticastDNS=yes\n' > /etc/systemd/network/25-wlan0.network.d/mdns.conf
cp tools/system/pip-web.dnssd /etc/systemd/dnssd/
# steadier Wi-Fi
cp tools/system/10-wifi-disable-powermanagement.rules /etc/udev/rules.d/
```

## 7. The two small boards (firmware)

| Board | Source | Board setting |
|---|---|---|
| Touchscreen (ESP32-2432S028R) | `firmware/cyd_dashboard` | `esp32:esp32:esp32`, partition scheme "Huge APP" |
| Motor board (Waveshare RP2040 Zero) | `firmware/rp2040_motor_usb` | `rp2040:rp2040:waveshare_rp2040_zero` |

The first time, build and flash from your computer with the Arduino IDE or `arduino-cli`. The exact core and library
versions that work are in [`.github/workflows/firmware.yml`](../.github/workflows/firmware.yml)
(ESP32 core 3.3.12, arduino-pico 4.5.3, LovyanGFX 1.2.7, ArduinoJson 7.4.1).

After that the robot can update them itself: push a firmware change to **your own** repo, GitHub Actions builds it,
and the robot downloads and flashes it after you tap Flash on its screen (`changing_things.md`). For that, fork this
repo, point `origin` at your fork, give the robot a deploy key, and fill in `GITHUB_REPO` and `GITHUB_TOKEN` in
`data/secrets.env`.

## 8. Optional extras

- **Reach it from anywhere:** a Cloudflare Tunnel to `http://localhost:8080`, then set `web.public_url` in
  `data/config.json`. Read [SECURITY.md](../SECURITY.md) first, and put an access gate in front of it.
  If the tunnel drops on Wi-Fi, see `tools/system/cloudflared-protocol.conf`.
- **Bluetooth speaker:** RoboOS > Bluetooth scans and pairs one.
- **Bluesky, email:** `knowledge/manual/social.md`, `knowledge/manual/email.md`.
- **Claude Code and other agents:** `mcp/robot_mcp.py` (`knowledge/manual/api.md`).

## 9. Keep your private life out of git

```bash
tools/privacy_scan.py --install-hooks           # every commit in this clone is checked first
nano data/private_terms.txt                     # your name, Wi-Fi name, addresses: one per line
```

Write what is true only for your robot (Wi-Fi name, addresses, what you have decided) in `data/manual/`, not in the
shared manual. The manual's README explains how the robot reads both.
