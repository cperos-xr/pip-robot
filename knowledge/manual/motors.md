# Motors (2 wheels: first drive 2026-11-08; locked by default)

**Motors are LOCKED.** `motors_enabled` is `false` in the config. Only the owner may enable them.
robotd refuses any non-zero motor command while they are locked.

## Hardware

- RP2040 Zero on the Pi's 13-pin header USB: `/dev/serial/by-id/usb-Waveshare_RP2040_Zero_*`.
- It drives the owner's L293D board (github.com/cperos-xr/l293d): 8 motor channels, each -127..127.
- The motor board has its own power input, the EXT VOLTAGE terminal (VCC, GND).
- **Wheels (2026-11-08):** two wheel motors on **U5**, the right-hand L293D (U2, U3, U4, U5 from left to right, U2 next
  to the RP2040). U5's outputs are the two right-hand screw terminals (top and bottom).
  **Verified by the wheels test:** motor 7 = LEFT wheel, motor 8 = RIGHT wheel, a positive value drives both forward.
  Saved in config `wheels` (`{"left": {"ch": 7, "dir": 1, "min_duty": 95}, "right": {...}}`).
- (On 3x AA, wheels up, they needed 74%; on the 7.4 V pack, on the floor, 55% is enough.)
- **Motor power:** an 11.1 V airsoft battery (bare 3S LiPo, 12.6 V full, no protection board) through a 7.5 V regulator since late 2026-11-08. Before that a
  7.4 V airsoft pack (2S LiPo), which died after being left plugged in for about an hour (drained below its safe
  minimum; it was old). 3x AA was too weak. The L293D loses about 2 V inside, so the motors get about 5.5 V at full
  power. That matches the 7.4 V pack at half charge (a full 2S pack is 8.4 V, about 6.4 V at the motors, so it felt
  faster when fresh), and it no longer drops as the battery drains. Keep the regulator: the TT motors are 3-6 V
  motors, and 11.1 V straight in would give them about 10 V. The board has no low-voltage cutoff and drains the pack
  slowly even when idle: **unplug it when not driving.** Never connect the pack to the 5 V boost board (a boost cannot
  step down: the full pack voltage would reach the Pi).
- **First drive (2026-11-08, on the floor, 7.4 V):** moves at 70 of 127 (55%) and veers slightly left going forward
  (the right wheel is a bit stronger); spins both ways, wiggles and reverses. `motor_max` was then set to 110.
- **Speed cap (2026-11-10, owner):** `motor_max` is **127** (no software cap). The 7.5 V regulator already limits the
  motors to about 5.5 V even at full power, so the old cap of 110 (from the 7.4 V pack) only made the robot slower
  (about 4.8 V). The cap is a robotd setting (config `motor_max`, owner: `POST /api/motors/settings {"max":N}`), not
  firmware. The web app's Speed slider now starts at 127. Spins stay capped at 95 (`TURN_MAX` in `drive.py`), because
  faster spins went past the gyro's 250 deg/s range (since 2026-11-14 the range is 1000 deg/s; the cap was not retested). If the regulator is ever removed, lower `motor_max` first.

## Firmware protocol (`firmware/rp2040_motor_usb/`)

Text lines over USB serial:
- `PING` -> `PONG rp2040-motor v0.1 uptime_ms=...`
- `M v1 v2 ... v8` -> `OK M ...` (each -127..127; missing values = 0)
- `STOP` -> `OK STOP`
- `INFO` -> `INFO {json}`

Also binary: `0xAA, m1..m8 (int8), xor(m1..m8)`.

**Failsafe:** if no valid motor command arrives for 500 ms, every motor stops.

## Wheel test (`tests.py` "wheels", Tests > "Wheel setup (needs you)" or `POST /api/test/wheels`)

The owner lifts the robot and taps Ready on the touchscreen; then each channel spins forward for 0.7 s (47%, then 74%,
then 100% if nothing moved) and the owner taps which wheel turned and which way. This is the only code that may turn
motors while `motors_enabled` is false, and only after that Ready tap. The answers go to config `wheels`.

## Onboard steering (`robotd/drive.py`, 2026-11-08)

The control loops run on the robot, next to the IMU (yaw at 50 Hz, sent by the motor board) and the motor link. `POST /api/drive`:
`{"cmd":"turn","deg":90}` (+ = left), `{"cmd":"forward"|"back","secs":1,"speed":100}` (holds the heading),
`{"cmd":"arc","left":45,"right":120,"secs":1}`, `{"cmd":"wiggle","n":6}`, `wait`, `say`, `ask` (asks the owner on the
touchscreen; any answer but the first cancels), `{"cmd":"stop"}`, or `{"seq":[...]}` for a whole routine in the
background (`GET /api/drive` shows the step and a log). Needs `motors_enabled`; capped by `motor_max`.
- Results (recorded routine, 2026-11-08): spins 356/360 and 357/360, square corners 86-87 of 90, 179 of 180;
  straight at 100 drifts under 7 degrees. Over Wi-Fi from a laptop the same corners were 100-182.
- Spin power is capped at 95: faster spins pass the gyro's 250 deg/s range and the yaw undercounts.
- When a wheel would go over the cap, both are scaled down together (clipping one wheel threw away the steering:
  two full-power straight moves drifted about 40 degrees before this fix).

## Remote control (web app > Live, under the camera)

Unlock/Lock button, speed slider, a drag pad (or arrow keys / WASD), buttons for 90 degree turns, a spin, forward,
back and STOP. The pad appears only while the motors are unlocked, and the live view starts by itself, so you can
see where it is going (owner, 2026-11-08: "the joystick and camera view kinda should be visible together so you can
drive and see whats happening on mobile"). Wide screens and phones held sideways: camera and pad side by side (sideways
the tab bar hides to make room). Phones held upright: camera on top, pad below; the camera sticks to the top when
you scroll. The page sends `POST /api/drive/rc {"x","y","max"}` every 120 ms while held; the robot stops 0.4 s
after the last one (and the motor firmware 0.5 s after that), so a lost phone connection stops it. Straight driving
holds the heading; spinning is capped at 95. A joystick command cancels a running sequence.

## Driving notes (first real drive, 2026-11-08, camera + IMU, steered from the laptop over Wi-Fi)

- **Forward drift changes with power:** slight left at 70, clearly right at 85-90 (about 25 degrees per 0.8 s).
  Giving the right wheel 10 more (L 85, R 95) drove straight. Better: correct by IMU yaw while driving.
- **Turns:** spinning in place (one wheel back, one forward) works, but pulses under about 0.25 s often do not move
  at all (the front skids drag). Reliable: pulse 0.18 s, read `/api/imu` yaw, repeat until turned (157 of 160 degrees
  in 7 pulses). Yaw grows when turning left.
- **Distance:** about 10-20 cm per 0.8 s at 85 on the wooden floor.
- **Camera:** it points up, so it sees walls and furniture but not the floor just ahead. Low obstacles (shoes,
  cables, chair bases) are invisible until touched; tilting the webcam down or adding a distance sensor would fix it.
- **Privacy:** frames from driving show the home and people. Use `/api/live.jpg` (not saved), never post them.
- **Full power (127, 2026-11-08 routine):** forward/back 1 s, 360 degree spins (about 3 s each), S-curves and a fast
  wiggle all work. Straight moves stay within about 10 degrees with the right wheel +10.
- **Turn control over Wi-Fi overshoots at speed:** asking for 90 degree corners gave 100-182; at 120-240 degrees/s the
  stop arrives late, and gaps between yaw readings over 180 degrees scramble the count. Precise turns need the control
  loop on the robot (IMU at 50 Hz next to the motor link), not on a remote computer.

## First drive test (`tests.py` "first_drive", "First drive on the floor (needs you)")

After the wheels test: the owner puts the robot on the floor and taps Ready; it rolls forward 0.5 s (47%, 74%, 100%
until the owner taps that it moved), then turns left; the answers set `wheels.drive_duty` and `wheels.curve`.

## Unlocking (owner only)

`POST /api/motors/settings {"enabled": true, "max": 110}` unlocks the motors and sets the cap; `{"enabled": false}`
locks them again (and stops). Lock them after every driving session. The brain has no motor tool.

## Using them (only after the owner enables them)

- `POST /api/motors {"values":[60,60,0,0,0,0,0,0], "duration":0.5}` and `POST /api/stop`.
- The duty is capped by `motor_max` (60 of 127).
- First tests: wheels off the ground, low duty, short time.
- Self-tests `rp2040_ping` and `motor_failsafe` are safe (they send zero or stop only).

## Wheelies (2026-11-14: first kick into a 6 second balance on two wheels)

The robot kicks its nose up (0.4 s backwards, then full forward) and then balances on its two wheels.

- **Where the code is.** The fast part is in the motor board: `firmware/rp2040_motor_usb` 0.3 reads the IMU (GPIO26/27)
  500 times a second and runs the kick, the coast, the balance loop and the landing. The Pi sees the tilt only 50 times
  a second, too slow for this. The slow part is `robotd/wheelie.py`: it sends the settings, asks **Start** on the
  touchscreen, sends a keepalive every 0.1 s (the firmware stops after 0.5 s without one), then fetches the 500 Hz
  recording.
- **Asking in words** ("do a wheelie for ten seconds", "wheelie and spin", "practise your wheelies"; voice or chat) starts
  it directly in `brain._wheelie_command`, without the AI model. On 2026-11-14 the model answered "Tap Start on my screen"
  three times without calling its wheelie tool, so no Start question appeared. Talk ABOUT wheelies still goes to the model.
- **The easy way (the trick, with the saved settings):** Settings > Robot > **Wheelie: Do it** (touchscreen, RoboOS or
  web app), the **Wheelie!** button under the joystick on the web app's Live tab, or just ask Pip ("do a wheelie": its
  `wheelie` tool). All of them call `POST /api/wheelie {"cmd":"show"}`. The screen asks **Start**; then it kicks up,
  balances for "Wheelie time" (3 / 6 / 10 / 15 s, Settings > Robot) and lands, and says how it went.
- **Moves while it is up (motor firmware 0.4):** Settings > Robot > **Wheelie moves** (Stay, Spin, Spin both ways, Rock,
  Dance), or `{"cmd":"show","secs":10,"routine":"spin"}`. A routine is a list of `[seconds, speed, turn]` steps
  (`ROUTINES` in `robotd/wheelie.py`; speed in power units, + = forward; turn in deg/s, + = left). robotd sends the
  step as `MV speed turn` ten times a second; the firmware leans to reach the speed and steers to hold the heading.
  Tested 2026-11-14: **spin** = 355 of 360 degrees; **spin both ways** = a turn left, a turn right, back within 15
  degrees. Moves need more damping than standing still (kd at least 0.26 while a routine runs, `KD_MOVES`): with the
  practised kd 0.16 the nose swung 45-67 degrees. **rock** with 1.2 s steps did not follow; it now has 2.5 s steps (untested).
- **Landing:** `lp` 55 backwards until the nose is under `lo` (20 degrees) or `lms` 500 ms. With only 150 ms the nose came
  down to 47 degrees, the power went off, and it swung back over the top onto its back (2026-11-14).
- **Practice (firmware 0.4; owner 2026-11-14: "it should always be learning"):** Settings > Robot > **Wheelie practice**, or
  `{"cmd":"practice","n":5,"secs":20}`. One Start tap, then n wheelies by itself, with a **Stop** button on the screen.
  - In each one it balances for `secs` and every 2.5 s switches one setting between the saved value and a changed
    value (kd, db, dbw, kp, ky, kh in turn: `TUNE`). Each piece gets a score, lower = steadier:
    rocking (deg/s) + 8 x pitch spread (deg) + 0.15 x wheel effort + 2 x turning (deg/s). If the changed value is
    clearly better (7 %) it is saved, and the next attempt goes further the same way; if not, the other direction or
    the next setting is next. Comparing inside one balance matters: two wheelies with the same settings can score 30 and 50.
  - After each attempt it turns back by the measured turn and drives back by a guessed distance (wheel power over
    time; there are no wheel encoders), so it starts again near the same spot. It stops if it is not flat on the floor
    (it fell on its back), if the battery is low, or on Stop.
  - First session (2026-11-14, 5 x 20 s, all landed): less kd is steadier. kd went 0.35 -> 0.27 -> 0.21 -> 0.16 and the
    score from 59 to 33. But with less kd the catch after the kick swings higher (59 -> 66 degrees), so robotd catches
    every kick with kd 0.35 and switches to the saved kd after 1.2 s (`KD_CATCH`, `CATCH_S`).
  - Heading hold (ky 0.2, kh 1.0) keeps it within about 5 degrees for 10 s; without it, it turned up to 47 degrees in 15 s.
  - Every run of any kind is added to `data/wheelie/learning.jsonl` (settings, score, how it ended); `GET /api/wheelie`
    shows the last scores and where the tuner is.
- **Run it for tuning:** `POST /api/wheelie {"cmd":"kick"}` (owner; motors must be unlocked; battery, not USB power). Other
  commands: `arm` (hold the robot at its balance angle, it takes over), `balance`, `record` (IMU only, 8 s),
  `set` (save settings), `stop`, `setup`. Any setting can be given for one run: `{"cmd":"kick","balms":10000}`.
- **Setup (once, and again whenever the IMU is moved):** `{"cmd":"setup"}`. Screen step 1: robot flat and still (level +
  gyro zero). Step 2: lift the FRONT up and hold it (this tells it which way is forward). `"level": false` repeats only step 2.
- **Data:** `data/wheelie/<time>_<kind>.csv` (500 Hz: acceleration, gyro, pitch, wheel power, mode), `.json` (summary + short
  trace) and `_live.txt` (the 100 Hz stream, written as it happens: a flip once cut the power and the recording in the
  motor board was lost).
- **What the robot is like (measured):** it balances at **55.5 degrees nose up**. Forward from a standstill lifts the nose only
  7 degrees; backwards then forward lifts it at 200-285 deg/s, and with the power on until 30 degrees it flips over
  backwards. So the kick power goes off at 26 degrees (`cut`), it coasts, and the balance loop takes over at 42 (`catch`).
- **Settings that work (saved in config `wheelie.params`):** k1p -127, k1ms 400, k2p 127, k2ms 500, cut 26, catch 42,
  tgt 55.5, kp 6, kd 0.35, db 45, dbw 12, kv 0.06, kx 0.03, tm 0.2, balms 6000, lo 20, hi 95, lp 60, lms 150.
  `GET /api/wheelie` lists every setting with one line of help.
  - With angle feedback only (kv = kx = 0) it ran away forward and fell after 3.5 s: `kv` and `kx` lean it back as
    its (estimated) speed and distance grow. There are no wheel encoders: speed is estimated from the power sent.
  - `db` is the power needed before the wheels turn at all. As a step it made the robot rock at 4-7 Hz; `dbw` blends it in.
  - When the time is up it lands: `lp` backwards for `lms` ms brings the nose down. Without that it fell either way.
- **Still rough:** it rocks about 1.5 degrees 5-6 times a second, the landing is hard (250 deg/s), and it cannot steer
  or drive while balancing (`ky` is there for steering, untested).
- **If the kick suddenly stops lifting the nose** (2-20 degrees instead of 58, the robot swinging sideways): check the
  wheels. On 2026-11-14 a wheel had worked loose from the kick torque; seven kicks in a row failed until the owner
  tightened it (thread lock is planned). In the recording it shows as a forward push of about 0.18 g instead of 0.3 g
  and a heading that swings during the kick. Practice now ends after two failed kicks in a row.
- **Take care:** it needs about a metre of clear floor in front and behind, and someone ready to catch it. Snug power plugs:
  one flip reset the whole robot.
- **Flashing the motor board from the robot** works (`POST /api/firmware/flash {"target":"rp2040","path":...}`, asks on the
  screen, backup in `data/firmware_backups/rp2040_*.uf2`, about 80 s). If it ever stays in its bootloader:
  `picotool reboot`, or `picotool load -x <backup or CI .uf2>`.

## Practice routines (owner request, 2026-11-10)

- Files: `routines/practice_drive.json` (slow routines and limits) and `tools/practice_drive.py` (runner).
  Neither is loaded by robotd, so nothing changes at boot.
- Routines: `straight_forward`, `straight_back`, `gentle_spin_left`, `gentle_spin_right`, `wiggle`. Duty 60/127 at most, about 1 s per move.
- Check first: `venv/bin/python tools/practice_drive.py <name> --dry-run` (moves nothing).
- Real run: `venv/bin/python tools/practice_drive.py <name>`. The screen asks "is the floor clear, about 3 feet before a wall?"
  Only Start runs it. It stops on STOP, a tilt over 20 degrees, a stale IMU, the end of the run, or Ctrl-C.
- Log: `data/drive_logs/practice_*.csv` (IMU yaw, pitch, roll, gyro z, tilt per 0.1 s). The runner prints the heading change (veer).
- Distance is not measured (no wheel encoders yet). Measure with a tape if needed.
