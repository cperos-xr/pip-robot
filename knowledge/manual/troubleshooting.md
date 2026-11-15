# Troubleshooting

Find the problem, then do the steps in order. Check `/api/log` first, always.

| Problem | Try this |
|---|---|
| Bluetooth speaker voice sounds like a chipmunk | Something sent it mono or non-48 kHz audio. Send 48 kHz stereo (`Voice.bt_wav`, ffmpeg `-ac 2 -ar 48000`). |
| Bluetooth speaker does not reconnect | It may be playing for a phone. Turn it off and on; the robot retries every minute. `bluetoothctl info <speaker MAC>` (config `bluetooth.speaker`), `bluealsa-cli list-pcms`. "key-missing" = the speaker forgot the pairing: re-pair in the RoboOS Bluetooth app. |
| The robot feels slow / robotd uses a lot of CPU | `GET /api/debug/threads?secs=20` names the busy thread and the code line. Normal when idle (2026-11-13): about 6-9% of one core, mostly the `cyd` thread reading the screen's 25 Hz motion-sensor stream; the first minute after a restart is much higher (start-up). An open web app tab adds a few %; hidden tabs stop polling and stop the live camera. |
| The same post is on Bluesky twice | Fixed 2026-11-13: `publish()` claims the post (status `publishing`) before uploading, so the minute tick, a tap and the web app can never post it twice. A post left `publishing` by a restart becomes `failed` and is never retried by itself. |
| Web app does not load at home | Use `http://pip.local:8080` or `http://<robot-ip>:8080` with `http://` typed. Same Wi-Fi as the robot. Check `systemctl status robotd`. |
| Laptop cannot ping / SSH the robot at home, but its public address works (`ip neigh` on the laptop shows the robot's IP as FAILED) | Until 2026-11-13 the router did not deliver broadcasts between Wi-Fi devices, so ARP never found the robot. Fixed on the router (a consumer Wi-Fi 6 router: WPA2-PSK AES, Group Key Update Period 0, AP isolation off, bands split; tests: broadcasts 35/40 both ways, multicast and mDNS fine). robotd's Wi-Fi keeper (`robotd/wifi_keep.py`, `GET /api/wifi`) still sends each computer in config `wifi.peers` a unicast ARP every 20 s as a safety net. If it happens again: on the laptop `sudo ip neigh replace <robot-ip> lladdr <robot wlan0 MAC> dev <wifi-interface> nud stale`. |
| Robot has no Wi-Fi at all (tunnel error 1033/530, no ping) | Check `iw dev wlan0 link` on the USB serial console (`/dev/ttyACM0`, 115200, root). The chip is 2.4 GHz only: the network must be a 2.4 GHz one (split the router's bands if it uses one name for both). Add a network to `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf`, then `wpa_cli -i wlan0 reconfigure`. |
| The public address shows "Pip is offline" | The browser's offline placeholder (`robotd/web/sw.js`): the robot or its tunnel is down. It reloads by itself when Pip is back. |
| The public address does not load | `systemctl status cloudflared`. The route in the Cloudflare dashboard must be service `http://localhost:8080` (no trailing slash). |
| The public address shows a Cloudflare error for a minute, then works again | The built-in Wi-Fi stalled (`journalctl -k | grep -c "Receive failure"` counts its dropped frames) and the tunnel reconnected. The tunnel runs over TCP (http2) since 2026-11-10 to ride out short stalls; `journalctl -u cloudflared` should say `protocol=http2`. A USB Wi-Fi adapter would be the real fix. |
| Can't log in to the web app | Screen: Menu > About > PAIR A PHONE, scan the QR code. Then set a password in Settings. |
| Screen says "waiting for Orange Pi" | robotd is not running or is restarting. Wait 40 s. Then `systemctl restart robotd`. Also suspect power. |
| Screen reboots over and over | Power first. Then read its serial output: stop robotd, read `/dev/ttyUSB0` at 921600 for a crash backtrace. |
| Screen is dark or broken after flashing | Re-flash the last good `.bin` from `data/` (see `screen.md`). Backups made before each flash are in `data/firmware_backups/`. |
| No sound | `GET /api/voice` (output should be `cyd`). Screen Settings > Volume above 0. Run test `voice_speak`. |
| Robot doesn't understand speech | Run `mic_check` and `voice_ears`. Speak close to the webcam. |
| AI says "could not reach my thinking brain" | `GET /api/brain`: is the active model available? Anthropic models have no credit; pick an OpenRouter one. Run test `ai_brain`. |
| Social post text is cut off or empty | Fixed 2026-11-06: thinking models used up the token budget. Captions now get 3000 tokens, low reasoning, and a shorten-retry. |
| AI chat hangs for minutes | Seen once on 2026-11-06 (cause not found yet). `systemctl restart robotd` fixed it. If it happens again, note which question and model. |
| IMU numbers are wrong | Run `imu_check`. Put the robot flat and `POST /api/imu/level`. Check the I2C wires and ground. |
| Screen IMU page says "waiting for IMU..." | The IMU streams from the screen board only after the screen's hello (it lists the CN1 devices). robotd asks again every 3 s until it gets one (log: "asking again"); if it still waits, run the `i2c_scan` test, which also restarts the stream. |
| I2C errors | Run `i2c_scan` and `i2c_health`. Sensors are on the screen's CN1 port. A loose ground caused problems before. The status page's "I2C bus" row shows the bus the sensors are on (CYD CN1 while the Pi's own bus 0 is empty; it showed a false 100% until 2026-11-08 because scanning the empty Pi bus counted every empty address as an error). |
| Webcam light stays on | Check `GET /api/vision`: `follow` (eyes follow faces) and `gaze_mirror` keep the camera on while the face shows. Turn them off with `POST /api/vision`. |
| Camera never turns on | Camera privacy may be on: `GET /api/camera`. Turn it off in RoboOS (tray camera icon) or `POST /api/camera {"privacy":false}`. |
| RoboOS is frozen or blank | Tap the screen's back button and open it again. Check `GET /api/desktop` (failed frames). `systemctl restart robotd`. |
| RoboTube "Could not play" | YouTube changes often: `/root/robot/venv/bin/pip install -U yt-dlp yt-dlp-ejs`. Node must exist (`node --version`). |
| Camera fails | Run `camera_snap` and `usb_inventory`. Re-plug the hub. |
| Motors don't move | They are locked on purpose (`motors.md`). |
| A code change broke robotd | `safe_restart.sh` rolls back by itself. Look at the `failed-*` branch to see what broke. |
| Time is wrong | `timedatectl`. The Pi needs internet for the time (no clock battery). |
| Disk full | `df -h`. Old photos are in `data/photos/`. Ask the owner before deleting. |
