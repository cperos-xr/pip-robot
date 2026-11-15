#!/bin/bash
# Install Pip's software on a brain computer: any Debian-family Linux (Armbian, Raspberry Pi OS, Debian, Ubuntu,
# DietPi) on an Orange Pi, a Raspberry Pi, another single-board computer, or an old laptop.
#
# Run it ON THE BRAIN, as root. From a PC with a checkout of this repo:
#   scp tools/install.sh USER@pip.local:/tmp/ && ssh -t USER@pip.local sudo bash /tmp/install.sh
# Or on the brain itself:
#   curl -fsSL https://raw.githubusercontent.com/cperos-xr/pip-robot/main/tools/install.sh | sudo bash
#
# Safe to run again: every step checks first and skips what is already done. It never overwrites data/ (your keys,
# memory, settings) and never touches boot settings unless you pass the flag for it.
#
# Options:
#   --check            only report what is there and what is missing; change nothing
#   --repo URL         where to get the code (default: the public Pip repo; use your own fork if you have one)
#   --bundle FILE      no internet on the brain: a `git bundle` of the repo copied over from the PC
#   --no-voice-model   skip the 70 MB offline speech model (Pip then needs the internet to understand speech)
#   --no-start         install, but do not start robotd
#   --hostname NAME    rename this computer (then it answers at NAME.local); a system setting: ask the owner first
#   --enable-i2c       Raspberry Pi only: switch I2C on (raspi-config; a boot setting, needs a reboot): ask the owner first
#   --yes              do not ask before installing packages
#
# The last line it prints is   PIP-INSTALL: ok   or   PIP-INSTALL: failed (<why>)   so an AI helper can tell.
set -uo pipefail

REPO="https://github.com/cperos-xr/pip-robot.git"
DIR=/root/robot
VOSK=vosk-model-small-en-us-0.15
CHECK=0; BUNDLE=""; VOICE=1; START=1; NEWHOST=""; I2C=0; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --repo) REPO="$2"; shift ;;
    --bundle) BUNDLE="$2"; shift ;;
    --no-voice-model) VOICE=0 ;;
    --no-start) START=0 ;;
    --hostname) NEWHOST="$2"; shift ;;
    --enable-i2c) I2C=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1 (see --help)"; exit 2 ;;
  esac
  shift
done

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok    %s\n' "$*"; }
todo() { printf '   TODO  %s\n' "$*"; }
warn() { printf '   WARN  %s\n' "$*"; }
fail() { printf '\n   FAIL  %s\n\nPIP-INSTALL: failed (%s)\n' "$*" "$*"; exit 1; }
MISSING=0

# ------------------------------------------------------------------------------------------------ 1. what is this?
say "1. this computer"
[ "$(id -u)" = 0 ] || fail "run as root (sudo bash $0)"
command -v apt-get >/dev/null || fail "no apt-get: Pip needs a Debian-family Linux (Armbian, Raspberry Pi OS, Debian, Ubuntu)"
MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || cat /sys/class/dmi/id/product_name 2>/dev/null || echo unknown)"
. /etc/os-release 2>/dev/null || true
ARCH="$(uname -m)"; MEM=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
ok "$MODEL, $ARCH, ${MEM} MB, ${PRETTY_NAME:-unknown OS}"
case "$ARCH" in armv6l) warn "ARMv6 (first Raspberry Pi Zero / Pi 1): too slow for the AI libraries. A Zero 2 W or newer works." ;; esac
[ "$MEM" -ge 400 ] || warn "less than 400 MB of memory: robotd needs about 150 MB, the rest is tight"
PY="$(command -v python3 || true)"
if [ -n "$PY" ]; then
  PYV="$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  $PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' && ok "Python $PYV" || fail "Python $PYV is too old: Pip needs 3.11 or newer (Debian 12 / Raspberry Pi OS bookworm or newer)"
else
  todo "Python 3 (installed in step 2)"
fi
FREE=$(df -Pm / | awk 'NR==2 {print $4}')
[ "$FREE" -ge 1500 ] && ok "${FREE} MB free on the SD card / disk" || warn "only ${FREE} MB free; the install needs about 1.5 GB"
if [ "$(date +%Y)" -lt 2025 ]; then
  warn "the clock says $(date +%F): no internet time yet. apt and https fail with a wrong clock. Trying to fix it:"
  [ $CHECK = 1 ] || timedatectl set-ntp true 2>/dev/null; sleep 5; date
fi
NET=0
if curl -fsI --max-time 8 https://github.com >/dev/null 2>&1 || wget -q --spider --timeout=8 https://github.com 2>/dev/null; then
  NET=1; ok "internet"
else
  warn "no internet: packages cannot be installed. On a cable to a PC, turn on internet sharing for it; or join Wi-Fi (armbian-config / raspi-config / nmtui)"
fi
BOARD=other
case "$MODEL" in
  *"Orange Pi Zero"*) BOARD=opizero ;;
  *"Raspberry Pi"*) BOARD=rpi ;;
esac
[ -d /proc/device-tree ] || BOARD=pc

# ------------------------------------------------------------------------------------------------ 2. packages
say "2. packages"
NEED="git python3 python3-venv python3-pip python3-numpy python3-opencv opencv-data python3-pil python3-serial
python3-requests python3-yaml python3-smbus2 python3-libgpiod gpiod i2c-tools usbutils v4l-utils alsa-utils espeak-ng
ffmpeg fswebcam tesseract-ocr tesseract-ocr-eng sqlite3 gcc libc6-dev curl"
EXTRA="bluez bluez-alsa-utils libasound2-plugin-bluez picotool"     # nice to have; not on every distribution
miss=""
for p in $NEED; do dpkg -s "$p" >/dev/null 2>&1 || miss="$miss $p"; done
if [ -z "$miss" ]; then
  ok "all needed packages are installed"
elif [ $CHECK = 1 ]; then
  todo "missing:$miss"; MISSING=1
else
  [ $NET = 1 ] || fail "packages missing ($miss ) and no internet"
  if [ $YES = 0 ] && [ -t 0 ]; then
    read -r -p "   install:$miss ? [Y/n] " a; case "$a" in n*|N*) fail "packages not installed" ;; esac
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q || fail "apt-get update failed (clock wrong? no internet?)"
  # shellcheck disable=SC2086
  apt-get install -y -q $miss || {
    warn "installing all at once failed; trying one by one"
    for p in $miss; do apt-get install -y -q "$p" >/dev/null 2>&1 && ok "$p" || warn "could not install $p"; done
  }
  ok "packages installed"
fi
for p in $EXTRA; do
  dpkg -s "$p" >/dev/null 2>&1 && continue
  if [ $CHECK = 1 ] || [ $NET = 0 ]; then todo "optional: $p"; else apt-get install -y -q "$p" >/dev/null 2>&1 && ok "optional: $p" || warn "optional $p is not available here (fine)"; fi
done

# ------------------------------------------------------------------------------------------------ 3. the code
say "3. the code in $DIR"
if [ -d "$DIR/.git" ]; then
  ok "already there ($(git -C "$DIR" log -1 --format='%h %s' 2>/dev/null | cut -c1-70))"
  if [ $CHECK = 0 ] && [ $NET = 1 ]; then
    if [ -z "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
      git -C "$DIR" pull -q --ff-only 2>/dev/null && ok "updated to $(git -C "$DIR" log -1 --format=%h)" || warn "could not update (offline, or the robot made its own commits): left as it is"
    else
      warn "it has local changes: not updating it"
    fi
  fi
elif [ $CHECK = 1 ]; then
  todo "not installed yet"; MISSING=1
else
  if [ -n "$BUNDLE" ]; then
    git clone -q "$BUNDLE" "$DIR" || fail "could not clone the bundle $BUNDLE"
    git -C "$DIR" remote set-url origin "$REPO"
  else
    [ $NET = 1 ] || fail "no code and no internet: copy a bundle over (git bundle create pip.bundle --all) and use --bundle"
    git clone -q "$REPO" "$DIR" || fail "could not clone $REPO"
  fi
  ok "cloned $(git -C "$DIR" log -1 --format='%h %s' | cut -c1-70)"
fi

# ------------------------------------------------------------------------------------------------ 4. Python environment
say "4. Python environment"
if [ -x "$DIR/venv/bin/python" ] && "$DIR/venv/bin/python" -c 'import anthropic, vosk, serial' 2>/dev/null; then
  ok "venv ready"
elif [ $CHECK = 1 ]; then
  todo "venv and Python packages (requirements.txt)"; MISSING=1
elif [ -d "$DIR" ]; then
  [ -x "$DIR/venv/bin/python" ] || python3 -m venv --system-site-packages "$DIR/venv" || fail "could not make the venv"
  echo "   installing Python packages (on a small board this takes 5-20 minutes) ..."
  "$DIR/venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1
  "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt" || fail "pip install -r requirements.txt failed (read the error above)"
  ok "Python packages installed"
fi
if [ -d "$DIR/robotd" ]; then
  if "${DIR}/venv/bin/python" -m compileall -q "$DIR/robotd" "$DIR/roboos" >/dev/null 2>&1 || python3 -m compileall -q "$DIR/robotd" "$DIR/roboos" >/dev/null 2>&1; then
    ok "robotd compiles on this Python"
  else
    fail "robotd does not compile on this Python (python3 -m compileall $DIR/robotd shows why)"
  fi
fi

# ------------------------------------------------------------------------------------------------ 5. voice model
say "5. offline speech model"
if [ -d "$DIR/models/$VOSK" ]; then
  ok "$VOSK"
elif [ $VOICE = 0 ]; then
  ok "skipped (--no-voice-model): speech is understood by a cloud model when a key is set"
elif [ $CHECK = 1 ] || [ $NET = 0 ]; then
  todo "$VOSK (about 70 MB)"
else
  command -v unzip >/dev/null || apt-get install -y -q unzip >/dev/null 2>&1
  mkdir -p "$DIR/models" && cd "$DIR/models" && \
    curl -fsSLO "https://alphacephei.com/vosk/models/$VOSK.zip" && unzip -q "$VOSK.zip" && rm -f "$VOSK.zip" \
    && ok "$VOSK" || warn "could not download the speech model (Pip still works; try again later)"
  cd / || true
fi

# ------------------------------------------------------------------------------------------------ 6. this robot's own data
say "6. this robot's private folder ($DIR/data, never committed)"
if [ -d "$DIR" ] && [ $CHECK = 0 ]; then
  mkdir -p "$DIR/data/manual"
  if [ ! -f "$DIR/data/secrets.env" ]; then
    cp "$DIR/tools/secrets.env.template" "$DIR/data/secrets.env" && chmod 600 "$DIR/data/secrets.env"
    ok "made data/secrets.env (empty: an AI key goes in there)"
  else
    chmod 600 "$DIR/data/secrets.env"; ok "data/secrets.env is there (not touched)"
  fi
  if [ ! -f "$DIR/data/config.json" ]; then
    TZ_NOW="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"
    case $BOARD in
      rpi) printf '{\n  "tz": "%s",\n  "i2c_bus": 1\n}\n' "$TZ_NOW" >"$DIR/data/config.json"; ok "data/config.json: time zone $TZ_NOW, I2C bus 1 (Raspberry Pi)" ;;
      *)   printf '{\n  "tz": "%s"\n}\n' "$TZ_NOW" >"$DIR/data/config.json"; ok "data/config.json: time zone $TZ_NOW" ;;
    esac
  else
    ok "data/config.json is there (not touched)"
  fi
elif [ -f "$DIR/data/secrets.env" ]; then ok "data/secrets.env is there"; else todo "data/secrets.env"; fi
if [ -f "$DIR/data/secrets.env" ] && grep -qE '^(ANTHROPIC|OPENROUTER)_API_KEY=[^ #]+' "$DIR/data/secrets.env"; then
  ok "an AI key is set"
else
  todo "no AI key yet: put ONE key (OpenRouter or Anthropic) in $DIR/data/secrets.env, then: systemctl restart robotd"
fi

# ------------------------------------------------------------------------------------------------ 7. boot settings (only when asked)
say "7. board settings"
case $BOARD in
  opizero)
    if grep -q 'usbhost2' /boot/armbianEnv.txt 2>/dev/null && grep -q 'i2c0' /boot/armbianEnv.txt 2>/dev/null; then
      ok "Orange Pi Zero overlays are on (13-pin USB, I2C)"
    else
      todo "Orange Pi Zero: the screen and motor board on the 13-pin header need  overlays=usbhost2 usbhost3 i2c0 analog-codec"
      echo "         in /boot/armbianEnv.txt (a boot setting: back the file up, ask the owner, then reboot). docs/INSTALL.md step 2."
    fi ;;
  rpi)
    if [ -e /dev/i2c-1 ]; then
      ok "I2C is on (/dev/i2c-1)"
    elif [ $I2C = 1 ] && [ $CHECK = 0 ]; then
      raspi-config nonint do_i2c 0 && ok "I2C switched on (takes effect after a reboot)" || warn "raspi-config could not switch I2C on"
    else
      todo "I2C is off. Only needed for sensors on the Pi's own pins. Switch on: --enable-i2c (a boot setting: ask the owner)"
    fi ;;
  pc) ok "a PC: no board settings" ;;
  *)  ok "no known board settings for this board (check which /dev/i2c-N is on its header: config i2c_bus)" ;;
esac
if [ -n "$NEWHOST" ] && [ $CHECK = 0 ]; then
  hostnamectl set-hostname "$NEWHOST" && sed -i "s/127.0.1.1.*/127.0.1.1\t$NEWHOST/" /etc/hosts && ok "this computer is now called $NEWHOST ($NEWHOST.local)"   # privacy-scan: ok (the loopback name entry every Debian has)
else
  ok "hostname: $(hostname) (answers at $(hostname).local when mDNS is on)"
fi

# ------------------------------------------------------------------------------------------------ 8. the service
say "8. robotd (the robot's program) as a service"
if [ $CHECK = 1 ]; then
  systemctl is-enabled robotd >/dev/null 2>&1 && ok "robotd service enabled" || { todo "robotd service"; MISSING=1; }
elif [ -d "$DIR" ]; then
  cp "$DIR/tools/robotd.service" /etc/systemd/system/robotd.service && systemctl daemon-reload && systemctl enable -q robotd && ok "service installed and enabled at boot"
  "$DIR/venv/bin/python" "$DIR/tools/privacy_scan.py" --install-hooks >/dev/null 2>&1 && ok "privacy check runs before every commit the robot makes" || true
  if [ $START = 1 ]; then
    systemctl restart robotd
    echo "   starting (the first start takes up to a minute) ..."
    up=0
    for _ in $(seq 1 45); do
      sleep 2
      if curl -fs --max-time 2 -o /dev/null http://127.0.0.1:8080/ 2>/dev/null; then up=1; break; fi
      systemctl is-active -q robotd || break
    done
    if [ $up = 1 ]; then ok "robotd is up: http://$(hostname).local:8080"; else
      warn "robotd did not answer yet. Look:  journalctl -u robotd -n 40 --no-pager"
      journalctl -u robotd -n 15 --no-pager 2>/dev/null | sed 's/^/         /'
      fail "robotd did not start"
    fi
  fi
fi

# ------------------------------------------------------------------------------------------------ done
say "done"
if [ $CHECK = 1 ]; then
  [ $MISSING = 0 ] && echo "PIP-INSTALL: ok" || echo "PIP-INSTALL: incomplete (see the TODO lines; run without --check to install)"
  exit $MISSING
fi
cat <<EOF
   Next:
   - What is it made of?   python3 $DIR/tools/discover_parts.py --write
   - Give it a brain:      one key in $DIR/data/secrets.env, then  systemctl restart robotd
   - Meet it:              http://$(hostname).local:8080 on your phone. With the touchscreen: Menu > About >
                           PAIR A PHONE and scan the code. Without one: type the contents of $DIR/data/api_token
                           as the password, then set your own owner password in Settings.
PIP-INSTALL: ok
EOF
