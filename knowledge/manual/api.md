# API: how to control the robot from code

There are three ways in:
1. The **web JSON API** on port 8080 (any program, curl, the web app).
2. **MCP tools** for Claude Code on the owner's PC (they call the same API).
3. The **brain's own tools** when you are the robot's AI (see `brain.md`).

## Logging in to the API

- Every `/api/...` call needs the owner token in the header: `X-Robot-Token: <token>`.
- The token is in `/root/robot/data/api_token` on the robot. On the owner's PC it is in `~/.config/ai-multi-bot/robot_token`.
- People in a browser log in with the owner password, or by pairing: on the robot screen open Menu > About > **PAIR A PHONE** and scan the QR code.
- GET requests work for the owner and for guests (view-only invites). POST requests need the owner.
- Guessing is slowed down (`robotd/webauth.py`, class `Throttle`): each visitor address gets 5 wrong passwords or pairing
  codes, then must wait 30 s, and the wait doubles with every further wrong try (up to 1 hour). The page answers 429
  "Too many wrong tries". A right answer, or 10 quiet minutes, clears it. The API token is not slowed down.
- Pairing codes (6 characters, on the robot's screen) last 5 minutes. Guest invites are links with a long random token
  (24 hours, one use). Through an https tunnel the session cookie is marked `Secure`.

Base URLs: `http://localhost:8080` (on the robot), `http://192.168.77.2:8080` (owner laptop cable),
`http://pip.local:8080` or `http://<robot-ip>:8080` (home Wi-Fi), the robot's public address if it has one (config `web.public_url`).

Example:
```bash
T=$(cat /root/robot/data/api_token)
curl -s -H "X-Robot-Token: $T" localhost:8080/api/status
curl -s -H "X-Robot-Token: $T" -H 'Content-Type: application/json' -d '{"text":"Hello!","mood":"happy"}' localhost:8080/api/say
```

## Read things (GET)

| Path | Gives you |
|---|---|
| `/api/status` | CPU, temperature, memory, disk, IPs, uptime |
| `/api/devices` | Every part with status ok / warn / missing / planned / unknown |
| `/api/tests` | Self-tests and their last results |
| `/api/log` | Recent log lines |
| `/api/mail` | Owner: Pip's email (inbox, drafts); POST cmds refresh/draft/discard, approve/send need the web sign-in. See `email.md` |
| `/api/files` | Owner: file browser (list, download, upload into `data/files`). See `files.md` |
| `/api/debug/threads?secs=10` | Owner: where robotd's CPU time goes: per thread, CPU % and the code it was seen in (`robotd/profiler.py`) |
| `/api/wifi` | Owner: Wi-Fi keeper (peers the robot announces itself to, neighbour table); see troubleshooting |
| `/api/wifi/networks` | Owner: the current Wi-Fi network, address, signal and the saved networks (`robotd/wifi.py`) |
| `/api/scripts` | Owner: demo scripts with readiness and what is playing; `?status=1` = only the playback state (`scripts.md`) |
| `/api/imu` | Tilt (pitch, roll), acceleration, gyro |
| `/api/wheelie` | Owner: wheelie settings, what is missing, the last runs. `?run=<name>` = one run with its trace (`motors.md`) |
| `/api/brain` | Active AI model and the model list |
| `/api/models?q=&sort=price` | Live OpenRouter catalog with prices |
| `/api/models/pack` | The variety pack (12 good cheap models) |
| `/api/bench` | Benchmark leaderboard and job status |
| `/api/settings` | The one settings list (groups and rows, see `settings.md`) |
| `/api/voice` | Where the voice goes (`auto` / `bluetooth` / `cyd` / `pi`), volume, active output |
| `/api/vision` | Computer-vision mode and what it sees |
| `/api/social` | Bluesky posts (drafts, queued, posted, blocked) and settings |
| `/api/memories` | Long-term memories |
| `/api/photos`, `/api/photo?name=` | Photo list / one photo. `?info=1`: `[{"n","post","h"}]` (post = a copy made for a social post, h = 64-bit difference hash: near-identical photos differ in 3 bits or fewer) |
| `/api/music`, `/api/music/file?name=` | Owner: songs in `data/music` (RoboTube "Save audio" lands here) and what is playing / stream one song (Range requests work) |
| `/api/snapshot.jpg`, `/api/live.jpg?vision=1` | A camera picture (with vision boxes if `vision=1`) |
| `/api/ideas` | Improvement ideas the robot made about itself |
| `/api/camera` | Camera privacy state and who is streaming |
| `/api/desktop`, `/api/desktop.png` | RoboOS status / a screenshot of the desktop (owner) |
| `/api/screen.png` | The REAL touchscreen, read back from the LCD (about 2.5 s, owner) |
| `/api/desktop/demo.mp4?app=checkers&s=20` | A video of the robot playing a RoboOS game by itself (owner) |
| `/api/social/stats` | Bluesky followers, posts, likes (`?refresh=1` to fetch now) |
| `/api/social/report` | `?hours=24`: text report of how the posts are doing (per post, new likes with times and who, followers, replies) |
| `/api/manual?page=` | A page of this manual |
| `/api/agent?since=<seq>` | RoboDev status + conversation items after `seq` (owner). `/api/agent/models`: models it can use |
| `/api/whoami` | Your role, password set?, public page sections |

## Do things (POST, JSON body)

| Path | Body | Does |
|---|---|---|
| `/api/say` | `{"text":"...", "mood":"happy"}` | Speak and show the words |
| `/api/face` | `{"mood":"curious", "say":"..."}` | Change the face. Moods: idle happy curious thinking warning sleepy surprised sad love. A mood set here **stays** (the robot's reactions show on top and return to it) until `{"mood":"auto"}` gives the face back to the robot; add `"hold": ms` for a temporary one. The web app's Auto button sends `auto`. |
| `/api/toast` | `{"msg":"..."}` | Small message on the screen |
| `/api/ask` | `{"title":"...", "q":"...", "opts":["Yes","No"], "timeout":60}` | Ask the owner on the screen, wait for the button |
| `/api/beep` | `{"chime":0}` or `{"hz":1000,"ms":120}` | Beep on the earpiece |
| `/api/chat` | `{"text":"...", "speak":false}` | Talk to the robot's AI, returns `{"reply":...}` |
| `/api/listen` | `{}` | Listen with the microphone and answer out loud |
| `/api/snap` | `{}` | Save a photo |
| `/api/test/<id>` | `{}` | Run a self-test (ids below) |
| `/api/hear` | `{"audio":"<base64>","mime":"audio/webm"}` | Speech recorded in the browser -> `{"text"}` (web app hold-to-talk) |
| `/api/settings` | `{"id":"sound.volume","value":6}`, `{"id":"sound.mute","op":"toggle"}`, `{"id":"robot.reboot","op":"run","confirmed":true}` | Change any setting (ops: next / prev / + / - / toggle / run) |
| `/api/voice` | `{"output":"auto"}`, `{"test":true}` | Voice output (also in `/api/settings`) |
| `/api/vision` | `{"mode":"faces", "follow":true, "mirror":true, "gaze_mirror":false}` | Vision mode: off faces gaze motion qr all |
| `/api/screen` | `{"theme":0..5, "bright":0..255}` | Screen look (volume: `sound.volume` in `/api/settings`) |
| `/api/page` | `{"name":"face"}` | Switch the screen page (face, menu, settings, desktop, camera...) |
| `/api/camera` | `{"privacy":true}` | Camera OFF (privacy) or back on |
| `/api/desktop/open` | `{}` or `{"app":"games"}` | Show RoboOS on the screen (and open an app) |
| `/api/social/show_off` | `{"app":"checkers"}` | Post a short video of the robot playing a game by itself |
| `/api/social/profile` | `{"displayName":"...","description":"..."}` | Set the Bluesky name (64) and bio (256); missing fields are kept |
| `/api/social/avatar` | `{"photo":"file in data/photos"}` | Set the Bluesky profile picture (centre square) |
| `/api/drive` | `{"cmd":"turn","deg":90}` / `{"seq":[...]}` / `{"cmd":"stop"}` | Onboard steering (see `motors.md`); GET = status and log |
| `/api/motion` (GET) | | Motion sensor: `active`, `last_s`, `count`, `today`, `mode`, `armed` (owner also gets `security_photos`) |
| `/api/motion` (POST) | `{"mode":"greet","greet_ai":true,"security_warn":false}` | Owner: motion mode off / wake / greet / security |
| `/api/security/photo` (GET) | `?name=sec_...jpg[&thumb=1]` | Owner only: a security-mode photo |
| `/api/security/clear` | `{}` | Owner: delete all security-mode photos |
| `/api/photos/delete` | `{"names":[...]}` | Owner: delete photos in `data/photos` (web app Media tab) |
| `/api/music/play` | `{"name"}` or `{"stop":true}` | Owner: play a song on the robot's speaker / stop |
| `/api/music/delete` | `{"name"}` | Owner: delete a song from `data/music` |
| `/api/reminders` | `{"text","title","not_before":"2026-11-08T10:00:00-10:00","fallback_at":...,"action":"security_test"}` | Owner: a touchscreen reminder (shown on the first motion after not_before, or at fallback_at); GET lists them |
| `/api/reminders/clear` | `{}` | Owner: remove all reminders |
| `/api/drive/rc` | `{"x":0,"y":1,"max":100}` | Joystick; repeat every 100-200 ms, stops 0.4 s after the last |
| `/api/motors/settings` | `{"enabled":true,"max":127}` | Owner: unlock/lock the motors and set the power cap (of 127) |
| `/api/social/self_portrait` | `{"post":true}` | Draw a self-portrait in Paint (video); `post:false` only saves it |
| `/api/social/draw` | `{"what":"...","from_photo":false,"post":false}` | Draw anything in Paint (video): a subject in words, or what a fresh camera photo shows. `/api/social/scene` (`description`) is an older name for the same thing |
| `/api/desktop/input` | `{"click":[x,y]}`, `{"long":[x,y]}`, `{"key":"a"}` | Use RoboOS remotely (x 0-319, y 0-219) |
| `/api/brain/select` | `{"key":"<model key>"}` | Switch AI model (keys from `/api/brain`) |
| `/api/brain/use` | `{"model_id":"deepseek/..."}` | Switch to any OpenRouter model id |
| `/api/brain/rate` | `{"good":true}` | Rate the last AI answer |
| `/api/bench/run` | `{"models":["id1","id2"]}` | Benchmark models (costs a little money) |
| `/api/memories` | `{"fact":"..."}` | Remember a fact |
| `/api/social/draft` | `{"note":"...", "take_photo":true}` or `{"note","photo","owner_photo":true}` | Make a post (posts by itself if allowed, see `social.md`) |
| `/api/social/publish` | `{"id":3}` | Post a draft now |
| `/api/social/discard` | `{"id":3}` | Throw a draft away |
| `/api/social/settings` | `{"autonomous":true, "max_posts_per_day":4}` | Social settings |
| `/api/agent/send` | `{"text":"...", "attachments":[{"name","data"(base64)}]}` | Message RoboDev (see `robodev.md`). Also `/api/agent/approve {"id","allow"}`, `/stop`, `/new`, `/settings {"mode","model","daily_budget_usd","voice_max_mode","full_minutes"}` |
| `/api/firmware/fetch` | `{}` | Download firmware built by GitHub Actions |
| `/api/firmware/flash` | `{"target":"cyd" or "rp2040", "path":"..."}` | Flash firmware (asks the owner on the screen) |
| `/api/imu/level` | `{}` | Save "this is level" for the IMU |
| `/api/imu/forward` | `{}` | With the front held up: save which way is forward |
| `/api/wheelie` | `{"cmd":"kick"}`, `"arm"`, `"balance"`, `"record"`, `"setup"`, `"set"`, `"stop"`, plus settings | Wheelies. Asks Start on the screen before the wheels move (`motors.md`) |
| `/api/motors` | `{"values":[...8 numbers -127..127], "duration":0.5}` | Drive motors. REFUSED while motors are locked. |
| `/api/stop` | `{}` | Stop all motors |
| `/api/scripts/play` | `{"name":"meet_pip","wheelie":true}` | Play a demo script. Pressing Play is the owner's approval for that playback, wheelie included (`scripts.md`) |
| `/api/scripts/next` / `/api/scripts/stop` | `{}` | End the current pause now / stop the script (lands a wheelie) |
| `/api/wifi/networks` | `{"cmd":"scan"}`, `{"cmd":"add","ssid","password","prefer"}`, `{"cmd":"connect"\|"forget","id"}` | Wi-Fi networks. Adding does not switch; connect falls back by itself after 40 s; the network in use cannot be forgotten |

## Self-test ids (for `/api/test/<id>`)

`i2c_scan`, `i2c_health`, `imu_check`, `pcf_read`, `camera_snap`, `rp2040_ping`, `motor_failsafe`,
`usb_inventory`, `network`, `cyd_link`, `system_health`, `voice_speak` (earpiece), `speaker_pi`,
`voice_ears`, `mic_check`, `ocr_check`, `ai_brain`, `gaze_check` (needs a person looking at the camera).

## MCP tools (Claude Code on the owner's PC)

Config: `~/Arduino/ai-multi-bot/.mcp.json` runs `robot/mcp/robot_mcp.py` with `ROBOT_URL=http://192.168.77.2:8080`.

Tools: `robot_status`, `robot_devices`, `robot_imu`, `robot_look`, `robot_say`, `robot_voice`, `robot_face`,
`robot_chat`, `robot_tests`, `robot_run_test`, `robot_feed`, `robot_memories`, `robot_remember`,
`robot_ask_owner`, `robot_brain`, `robot_select_brain`, `robot_dev_send`, `robot_dev_status`, `robot_dev_approve`, `robot_dev_stop`,
`robot_log`, `robot_ideas`, `robot_manual`.

## Public pages (no login)

`/public` shows only what the owner allowed in Settings > Public page (face, status, said, posts; the camera is off by default).
Data behind it: `/public/data`, `/public/posts`, `/public/live.jpg` (only if the camera is allowed).
