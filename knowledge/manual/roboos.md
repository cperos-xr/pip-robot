# RoboOS: the tiny desktop on the screen

RoboOS is a small Windows-95-style desktop shown on the touchscreen. The Orange Pi draws it; the screen only shows
the pictures and sends touches back.

## How to open it

- On the screen: **Settings > Desktop (RoboOS) > OPEN**. The top bar's back button leaves it.
- Move a **USB mouse** or press a key on a **USB keyboard** plugged into the Pi: the screen switches to the desktop.
- From code: `POST /api/desktop/open` or `POST /api/page {"name":"desktop"}`.
- Remotely: the web app's **Body** tab shows the desktop; click it to use it (`/api/desktop.png`, `POST /api/desktop/input`).

## Using it

- **Start** button (bottom left) lists every app. **Shut Down...** is at the bottom of the menu.
- Desktop icons open apps. **Long-press** (or right-click) an icon to remove it, or a Start-menu item to add it.
- Each app opens full-window. Title bar: **_** minimises, **X** closes. The taskbar switches between open apps.
- Taskbar tray: **camera icon** = tap to turn the camera off/on (privacy), then mouse/keyboard icons, then the clock.
- Scrollbars are wide: tap the arrows, hold an arrow to keep scrolling, or drag the thumb. You can also drag the list itself.
- Keyboard shortcuts (USB keyboard): Windows key = Start menu, Alt+F4 = close, Alt+Tab = next app, Esc = close menus.

## Apps

| App | What it does |
|---|---|
| RoboDev | Chat with the robot's coding agent: ask for changes (new apps, firmware, settings), type or use the mic, attach a photo, Allow/Deny risky steps, switch mode (Manual/Auto/FULL) and model. See `robodev.md`. |
| Terminal | A real root shell on the Pi. Quick buttons (ls, df, free, ps, ip, clear) and an on-screen keyboard. |
| Files | Browse folders, view text and pictures, play audio, **Edit** text files in Notepad. Secret files are never shown. **Long-press a file > Delete** (asks first; only files in `data/music`, `photos`, `recordings`, `notes` and `security`, so the robot's code and settings cannot be deleted by accident). |
| Notepad | Write and edit text. Notes are saved in `data/notes/`. Can open the manual pages. |
| RoboWeb | A text web browser: links, back, home, bookmarks. Searches use Brave Search. YouTube links open in RoboTube. |
| RoboTube | Search YouTube and watch (about 3 frames a second, sound on the voice output). Long-press a result (or "Save audio" while playing): save its audio to Music (`data/music/`; also listed in the web app, Media tab, to listen, download or delete). |
| Music | Plays audio files from `data/music/` (mp3, ogg, m4a, wav...) and recordings. Next song plays automatically. Long-press a song: Play / Delete. |
| Sound Recorder | Records from the webcam microphone (max 10 min) into `data/recordings/`. Long-press: copy to Music or delete. |
| Paint | Finger painting. Save puts a PNG in `data/photos/`. The robot also paints self-portraits here (off-screen, see `social.md`). |
| Games | A grid of game tiles (3 across, like the screen's menu) with each game's score. The screen's Menu > Games tile opens it. Add a game: one line in `Games.GAMES` (`desktop_games.py`); 7-9 games get smaller tiles, more scroll. |
| RoboMunch | A maze-chase game: eat every dot, dodge the four ghosts, eat them after a power pellet. Swipe or tap a direction, or arrows / WASD. 3 lives, levels get faster, best score kept. It pauses when its window is covered or the screen leaves RoboOS. The robot says the score at game over, and a record (or 1000+) can become a show-off clip. Opened from the Games launcher or its desktop icon (not in the Start menu). Code: `robotd/desktop_munch.py`. |
| Game Boy | Plays original Game Boy games (`.gb`) put in `data/files/games` (web app > Files > Upload). None come with the robot; free homebrew games work. Pick a game, then: D-pad on the left, A and B on the right, SELECT and START below; slide between buttons, touch between A and B for both. The touchscreen feels one finger at a time: for two buttons together use a USB keyboard (arrows/WASD, X or K = A, Z or J = B, Enter = START, Backspace or Tab = SELECT, Esc = list). The game runs at full speed; the screen shows what the USB link carries (number top right): about 5-7 frames a second while the whole screen scrolls, more when only a little moves (measured 2026-11-14: grid scroll 5.0, platformer scroll 6.8). Good for puzzle games and RPGs; fast action looks choppy until the screen firmware gets a compact frame message. Pauses when covered; saves in `data/gameboy/saves/`. No Game Boy Color, no sound yet. Never recorded for posts. Code: `robotd/desktop_gameboy.py`, emulator `robotd/gameboy.py` (Peanut-GB, compiled on the robot on first use into `data/gameboy/gbcore.so`). |
| RoboMines | Find the mines. Tap = dig, long-press or right-click = flag (the Dig/Flag button swaps them). Tap an open number whose mines are flagged to open the rest. Tap the robot face for a new game; the level button cycles Easy/Medium/Hard and keeps best times. Keys: arrows, Space, F, N. |
| Tic-Tac-Toe | Play against the robot (you are X). Who starts swaps every game. Levels: Easy, Normal, Hard (Hard never loses). Keys 1-9 pick a square. |
| Checkers | Play against the robot (you are red, at the bottom). Tap a piece, then a green dot (or drag it). Jumps are compulsory and multi-jumps must be finished; a yellow ring shows which pieces must jump. Levels: Easy, Normal, Hard (Hard thinks up to 3 s). |
| System Monitor | CPU graph, memory, disk, temperature, IPs, busiest processes. |
| Camera | Camera privacy switch and the eye-follow options (those keep the camera on while the face shows). |
| Settings | The robot's one settings list (`settings.md`): tabs Sound, Screen, Camera, AI, Posting, Robot. The same list as the touchscreen's Settings page and the web app. Replaced the Robot Control Panel on 2026-11-09. |
| Bluetooth | Speaker setup: adapter, whether the speaker is connected, Scan + tap to pair a new speaker, Connect. Connecting switches the voice to auto and says hello on it. |
| Logs | The robot's log, live. |
| About | Versions and tips. |
| Shut Down | Restart robotd, reboot the Pi, or shut down (asks first). |

## Games against the robot

Tic-Tac-Toe and Checkers show the robot's face, a speech box and the score (You / Robot / Draw, long-press the score to
reset it). The robot says a short line out loud when a game ends; turn that off with `"desktop": {"game_talk": false}`
in `data/config.json`. Scores, levels and RoboMines best times are kept in `data/config.json` under `desktop`.
Code: `robotd/desktop_games.py` (the checkers brain is an alpha-beta search in a background thread).
RoboMunch is a solo game (no robot opponent). Its maze is the `MAZE` list in `robotd/desktop_munch.py` (24 x 14, row 7 is a tunnel).

## Recordings for Bluesky

While a game or Paint is in front, the desktop keeps the last 40 s of frames (`Desktop.replay`, nothing else is ever
recorded). When a game ends, `game_moment()` turns the last 30 s into an MP4 and hands it to the social scheduler.
`Desktop.demo_clip(app)` lets a game play by itself off-screen (apps with `demo()` / `demo_step()`), for show-off videos.

## Sound

Only one sound plays at a time (Music, Recorder playback, RoboTube). It goes to the voice output
(`GET /api/voice`): the screen earpiece by default, streamed at 11 kHz.

## For programmers

- Code: `robotd/desktop.py` (window manager, input, first apps), `robotd/desktop_apps.py` (Keyboard, AudioOut, media/web apps)
  and `robotd/desktop_games.py` (RoboMines, Tic-Tac-Toe, Checkers).
- All coordinates are whole-desktop pixels: 320 x 220 (the area under the screen's 20 px bar).
- Add an app: subclass `App` (or `ListApp` for lists), set `name`, `title`, `icon`, implement `draw()` and the
  input methods you need (`click`, `press`, `drag`, `release`, `hold`, `context`, `key`, `key_up`, `scroll`, `tick`), then add it
  to `APPS` at the bottom of `desktop.py`. Icons are drawn in `draw_icon()`. `long_press = 0.45` makes long-press
  faster for that app; `None` turns it off (Paint).
- Only the changed rectangle is sent to the screen as a JPEG (slot `desk`). Set `video = True` and `fast = True` on an
  app that shows moving pictures; the desktop then sends smaller frames more often.
- USB input is read straight from `/dev/input/event*` (no extra libraries). The webcam also shows up as an input
  device (its snapshot button); it is ignored because it has no letter keys.
- Status: `GET /api/desktop` (frames sent/failed, open windows, connected input devices).
- `key_up(k)` (USB keyboard only) tells an app that a key was let go; the Game Boy uses it to hold buttons. Keys from
  the web app have no release.
- Tools installed for it on the Pi: ffmpeg, nodejs (Debian), yt-dlp + yt-dlp-ejs (in the venv), gcc (the Game Boy). RoboTube needs Node
  because YouTube streams require a JavaScript runtime.
- Never add big buffers to the screen firmware for this; the screen has very little memory (see `screen.md`).
