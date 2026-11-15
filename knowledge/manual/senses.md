# Senses: camera, vision, hearing, voice, balance

## Camera

- Webcam at `/dev/video0`. Live view runs at 640x480 and is scaled down for the screen.
- Photos are saved in `data/photos/`. Photos the AI takes go in `data/photos/ai/`.
- Moments: `moment_photo` takes a photo of something interesting (bird, sunset). Every moment photo gets a nudity check
  first (`Social.nudity_check`, fails closed: if the AI check is not available, nothing is kept). Nudity is refused and never
  shown or saved. Faces are allowed (owner rule, 2026-11-10: photos of the owner and of people the owner approved may be kept for the
  robot's own journal and moments folder; their first names are config `moments.people`, e.g. `["Alex"]`).
  `save_moment` takes `who`: when faces are in view it saves only if `who` names the owner and/or approved people,
  otherwise it refuses. The robot cannot recognise faces by itself; it relies on what the owner has told it.
  Saved files go to `data/photos/moments/moment_<date>_<time>.jpg`, with a `.txt` next to it (what, date and time, faces, file path) and a memory with the path.
  Moment photos are never posted by this feature; posting a photo with unblurred faces needs the owner's Post tap (`social.md`).
  Each saved moment also gets a line in the conversation journal (`journal.md`, src `moment`), so a keyword search for
  "bird" or "sunset" finds it.
  Nothing is saved unless Pip calls `save_moment`. The face count is a local Haar cascade: it can miss faces, so the
  "who" check is a rule for Pip and the owner, not a guarantee.
- Take a photo: `POST /api/snap`, or the brain tool `look`.

## Computer vision (OpenCV, runs on the robot, nothing leaves it)

- Modes: `off`, `faces`, `gaze` (which way eyes look), `motion`, `qr`, `all`. Set with `POST /api/vision {"mode":"faces"}`.
- Options: `follow` (eyes follow faces), `mirror` (mirror the image), `gaze_mirror` (robot's eyes copy your eye direction), `reactions` (smile -> happy face, QR -> toast).
- It shows on the screen's camera page at about 2.5 frames per second.
- Quick look without a photo: brain tool `see_live` (number of faces, smiles, motion, QR text).

## Reading text

- Brain tool `read_text`: takes a sharp photo and runs Tesseract OCR as a hint, and the AI reads the image.
- Screen camera page has a **READ** button.

## Hearing (speech to text)

- Microphone: the webcam (ALSA card `webcam`).
- Recognition (since 2026-11-08): the clip goes to a cheap audio model on OpenRouter (`voice.stt` = `cloud`, model
  `voice.stt_model`, empty = newest Gemini Flash Lite; about $0.0001 a sentence). Audio leaves the robot only while it
  listens. Fallback (offline, or `voice.stt` = `local`): Vosk small English model in `models/vosk-model-small-en-us-0.15`.
- Push-to-talk: tap **TALK** on the face, or `POST /api/listen`. The robot listens (up to 20 s, stops after a 1.4 s
  pause), then answers out loud. Requests to change its software go to RoboDev (`robodev.md`).

## Voice (text to speech)

- Voice: espeak-ng (robotic, offline, free). Three voices, chosen in Settings > Sound > Voice (`voice.tts`):
  - `robot` = the original (en-us, default). `british` = en-gb-x-rp (Received Pronunciation). `female` = en+f3.
  - All three are espeak-ng variants already installed: no packages, no API, no cost. The table is `TTS_VOICES` in
    `robotd/voice.py` (espeak voice, speed, pitch). Piper was considered but needs a package install and a model download.
  - Settings > Sound > Test audio plays the chosen voice.
- Where it comes out is set by `voice.output` (`POST /api/voice`):
  - `cyd` = the earpiece on the screen (current choice; the owner prefers it).
  - `pi` = the Pi's line out (too quiet without an amplifier; the speaker was removed).
  - `auto` (default since 2026-11-09) = the Bluetooth speaker if connected, else a USB speaker, else the earpiece.
  - `bluetooth` = only the Bluetooth speaker (the earpiece while it is off).
- The mouth on the face moves with the sound either way.
- Volume: screen Settings > Volume (0-3), or `POST /api/screen {"vol":2}`.
- **Bluetooth speaker** (2026-11-09): USB dongle "USB2.0-BT" (33fa:0001, BT 5.0, `hci0`) on the hub, BlueZ +
  bluez-alsa (`bluealsa` service). The paired speaker's name and address are config `bluetooth.speaker` (the RoboOS Bluetooth app pairs one). ALSA device
  `bluealsa:DEV=<mac>,PROFILE=a2dp`. Voice, RoboOS music and sounds play there when it is connected.
  - **Always send 48 kHz stereo.** Mono made bluez-alsa switch the link to mono SBC and the speaker played it at double
    speed (chipmunk voice). `Voice.bt_wav()` converts; AudioOut asks ffmpeg for 48 kHz stereo.
  - Loudness: `voice.bt_boost` (default 3, 0 = off, 5 = louder): soft compression, no small-speaker EQ.
  - **One volume** (`voice.volume` 0-10) and **mute** for every output (Settings > Sound). The Pi sets the Bluetooth
    software volume, the Pi line out and the screen speaker from it (`Voice.apply_audio()`).
  - The screen's own sounds (listening beeps, question chimes, reminders) follow the output: the Pi plays them on
    the speaker and sets the screen speaker to 0 (`Voice.on_cyd_sound`, fed by `CydLink.send`).
  - **Keep speaker awake** (`voice.keep_awake`: off / 30m / 2h / always): after the last sound, a 2 s inaudible 20 Hz
    hum every 90 s, so the speaker's own auto-off timer does not switch it off.
  - Settings > Sound > Test audio plays a chime and then the voice on the chosen output.
- **Talking from a phone** (2026-11-09; the webcam mic is poor): web app Live tab > Talk. **Hold to talk** records
  with the phone's microphone (MediaRecorder: webm/opus on Chrome, mp4/aac on iPhone), `POST /api/hear {audio
  (base64), mime}` turns it into text (ffmpeg, then cloud speech-to-text, Vosk if offline; the recording is not
  kept), and then `/api/chat` answers. The robot speaks the reply on its speaker; "read replies here too" also speaks
  it on the phone. Phones only allow the mic on the **https** address. Typing and the keyboard's mic button work
  anywhere.
  - It reconnects by itself every minute while missing (`bluetooth.auto_connect`, default on).
  - Pairing a new speaker: RoboOS Bluetooth app (Scan, tap it). By hand it needs ONE bluetoothctl session that scans,
    trusts, pairs and connects straight away (agent NoInputNoOutput), or the speaker drops the key.

## Balance (IMU)

- MPU-6050 on the motor board (RP2040 GPIO26/27) since 2026-11-14. The motor firmware (0.2+) reads it 500 times a
  second for wheelies and sends 50 samples a second to robotd, which works out pitch, roll and yaw (`"src": "rp2040"`).
  Range is now +-1000 deg/s and +-4 g (it was +-250 deg/s on the screen board).
- `GET /api/imu` gives pitch and roll in degrees. `POST /api/imu/level` saves "this is flat"; `POST /api/imu/forward`
  (front held up) saves which way is forward. Both are done together by the wheelie setup (`motors.md`).
  After moving or turning the sensor, run that setup again.
- Screen page `imu` shows a live tilt picture.

## Motion (PIR sensor, fitted 2026-11-08)

- White-dome passive infrared sensor on the Orange Pi 26-pin header: OUT -> pin 7 (PA6, `/dev/gpiochip0` line 6),
  VCC -> 5 V (pin 2 or 4), GND. Its output is 3.3 V, safe for the Pi. On 3.3 V VCC it triggered itself.
- It notices warm things moving (people, pets) up to a few metres away, with no camera. OUT stays high for 1-5 s per
  detection; the two knobs on the back set sensitivity and that hold time.
- `robotd/sensors.py` `Pir` reads the edges from the kernel (nothing is missed) and emits bus event `motion`.
  `GET /api/motion` gives `active`, `last_s`, `count`, `today`. The device list shows "last motion ... ago", the
  feed logs motion at most once a minute, and the brain's context line mentions movement in the last 5 minutes.
- Tests: "Motion sensor" (is the pin readable) and "Motion sensor cup test (needs you)" (covered = quiet, waving = seen).
- **Modes** (`robotd/motion_modes.py`; web app Body > Motion sensor, `POST /api/motion {"mode": ...}`, or ask the robot):
  - `off`: count and log only. `wake`: the face perks up (no camera).
  - `greet`: looks with the camera and says hello, at most every `greet_cooldown` s (180). With `greet_ai` the photo
    goes to the AI model for a one-line greeting about what it sees (never names, never bodies or clothes); without
    it, local face detection only. Camera privacy mode is respected. Greeting photos are not saved.
  - `security`: armed `arm_delay` s (60) after switching on. Each movement (at most every 20 s) saves an HD photo to
    `data/security/` (owner only: not in Photos, never posted, last 300 kept) and logs a `security` event;
    `security_warn` says a warning out loud. Switching it off tells how many times something moved.
  - Reminders (`robotd/reminders.py`) use the sensor too: a reminder shows as a silent touchscreen prompt on the first
    movement after its time (the owner is up), or at its fallback time.
  - Security can be switched ON by voice but only OFF in the web app (anyone near the robot can talk to it). Mode
    changes and security events stay out of the feed, which guests can see (security mode means the owner is away).
