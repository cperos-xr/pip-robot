# Software: what runs where

## The big picture

- **robotd** is the main program. It is Python and runs on the Orange Pi as the systemd service `robotd`.
- robotd talks to the **CYD screen** and the **RP2040 motor board** over USB serial.
- robotd serves the **web app + JSON API** on port 8080.
- **Cloudflare Tunnel** (`cloudflared` service, optional) publishes port 8080 at the robot's public address (config `web.public_url`).
- An **MCP server** (`mcp/robot_mcp.py`) lets Claude Code on the owner's PC use the robot as tools.

## Folders in /root/robot

| Path | What is inside |
|---|---|
| `robotd/` | The Python program (one file per job, see below) |
| `robotd/web/` | Web pages: `index.html` (owner app), `login.html`, `public.html` |
| `firmware/cyd_dashboard/` | Touchscreen firmware (Arduino, ESP32) |
| `firmware/rp2040_motor_usb/` | Motor controller firmware (Arduino, RP2040) |
| `knowledge/` | `hardware.yaml` (self-model), `personality.md`, `experience_log.jsonl`, this manual |
| `mcp/` | MCP server for Claude Code |
| `tools/` | `safe_restart.sh`, `clear_execstack.py`, test helpers |
| `data/` | NOT in git. Config, database, photos, secrets, tokens |
| `venv/` | Python virtual environment |
| `models/` | Vosk speech model (not in git) |

## robotd files (robotd/*.py)

| File | Job |
|---|---|
| `main.py` | Starts everything. Class `Robot`. Camera loop, status loop, housekeeping, screen events. |
| `core.py` | Paths, `Config` (data/config.json), `Store` (SQLite data/robot.db), event bus, logging |
| `links.py` | `MotorLink` (RP2040 serial); `CydLink` (screen serial) now lives in `roboos/screen.py` |
| `sensors.py` | IMU (MPU-6050) maths and the PCF8574 |
| `camera.py` | Webcam capture, photos, JPEG sizing |
| `vision.py` | OpenCV: faces, smiles, eye gaze, motion, QR codes |
| `voice.py` | Speech out (espeak-ng) and speech in (cloud audio model on OpenRouter, Vosk offline fallback) |
| `brain.py` | AI conversation, tools, model switching |
| `journal.py` | Conversation journal (compact text per exchange) and notes about people, in robot.db (`journal.md`) |
| `catalog.py` | Live OpenRouter model list and prices, the "variety pack" |
| `bench.py` | AI model benchmark (7 small tasks) |
| `social.py` | Bluesky posting, face blur, safety check, repeat check |
| `social_track.py` | Bluesky stats tracker: per-post likes / reposts / replies / followers over time (`social.md`) |
| `health.py` | Device inventory and improvement ideas |
| `tests.py` | Self-tests |
| `api.py` | Web server, JSON API, login |
| `webauth.py` | Passwords, pairing codes, cookies, public page settings |
| `agent.py` | RoboDev, the robot's coding agent (tool loop, permission modes, firmware builds); see `robodev.md` |
| `desktop_host.py` | `RobotHost`: RoboOS (`roboos/`) with the robot behind its Host API, plus the robot-only apps |
| `desktop_robot.py`, `desktop_bluetooth.py` | RoboOS apps that need the robot: Camera, Settings, Bluetooth |
| `desktop_dev.py` | The RoboDev app on RoboOS |
| `settings.py` | The one settings list shown by the screen, the RoboOS Settings app and the web app (`settings.md`) |
| `browse.py` | Web search and page reading for the brain and RoboDev (`brain.md`, "Looking things up online") |
| `firmware.py` | Flash the CYD / RP2040, fetch firmware built by GitHub Actions |

RoboOS, the desktop on the screen, is its own package next to robotd: `roboos/` (desktop, built-in apps, Game Boy,
the screen link, the app store). It never imports robotd and also runs without a robot (`python -m roboos`).
See `roboos.md`.

## Data (data/, never committed)

| File | What |
|---|---|
| `config.json` | All settings (brain models, voice, vision, social, web public_url, motors_enabled) |
| `robot.db` | SQLite: events (the log), tests, kv, memories, llm_calls, bench_results, posts |
| `secrets.env` | API keys (mode 600). Never print it. |
| `api_token` | Token for the JSON API (header `X-Robot-Token`) |
| `web_auth.json` | Web password hash and sessions |
| `last_good_commit` | Commit that ran fine for 60 s; used for rollback |
| `photos/` | Camera photos (`photos/ai/` = photos the AI took) |

## Everyday commands (run on the Pi as root)

```bash
systemctl status robotd          # is it running?
systemctl restart robotd         # restart (takes ~20-40 s to be fully up; the AI SDK import is slow)
cd /root/robot && git log --oneline -5
curl -s -H "X-Robot-Token: $(cat /root/robot/data/api_token)" localhost:8080/api/log   # the robot's log
```

- **Logs:** journald does not keep robotd logs well on this board. Use `/api/log` or the `events` table in `robot.db`.
- **Settings:** change them through the API when possible. If you edit `data/config.json` by hand, stop robotd first, then start it again. A running robotd overwrites the file.

## Boot setup (do not change without asking the owner)

- `/boot/armbianEnv.txt`: `overlays=usbhost2 usbhost3 i2c0 analog-codec`, `user_overlays=i2c0-robot`.
- Backups of that file: `/boot/armbianEnv.txt.bak-*`.
- Services: `robotd`, `cloudflared` (token in `/etc/cloudflared/token`). `cloudflared` uses TCP (http2), not QUIC, set in
  `/etc/systemd/system/cloudflared.service.d/protocol.conf` (2026-11-10), because the built-in Wi-Fi stalls for seconds
  at a time and QUIC connections timed out.
- Network (2026-11-13): wlan0 via `wpa_supplicant@wlan0` + systemd-networkd. `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf`
  has the home network (it must be a 2.4 GHz one: the chip cannot use 5 GHz). Add or forget networks in the web app
  (Settings > Wi-Fi, `robotd/wifi.py`, via `wpa_cli` + `save_config`): keys are stored as 64-hex PSKs, never in clear.
  An iPhone hotspot needs Maximize Compatibility on (2.4 GHz) and its curly apostrophe; both spellings get saved.
  On a new network Pip toasts its address on the face (`http://<address>:8080`).
  Backup: `/root/wpa_supplicant-wlan0.conf.bak-20261113`. Hostname `pip`; mDNS by systemd-resolved
  (`/etc/systemd/resolved.conf.d/mdns.conf`, `/etc/systemd/network/25-wlan0.network.d/mdns.conf`), and the web app is
  advertised as "Pip the robot" (`_http._tcp`, port 8080) by `/etc/systemd/dnssd/pip-web.dnssd`. Backups of the files
  changed for that: `/root/backup_20261113_mdns/`. No avahi on the robot.
- Offline page: browsers that opened the web app once get a service worker (`robotd/web/sw.js`) that shows
  `robotd/web/offline.html` ("Pip is offline", last seen, rechecks and reloads by itself) when the robot's public address
  answers 530/502/503/504. Only page loads are touched, never API calls. Change offline.html -> bump VERSION in sw.js.
