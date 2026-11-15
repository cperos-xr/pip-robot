# Settings: one list, shown in three places

Since 2026-11-09 the robot has **one** settings list: `robotd/settings.py`. Three places show it, with the same
groups, names and order:

| Where | How it gets the list |
|---|---|
| Touchscreen: Menu > Settings (firmware 0.8.0+) | The Pi sends `{"t":"settings","g":[{"n":group,"r":[rows]}]}`. A tap sends `{"t":"set","i":id,"op":...}`. Without the Pi, the screen shows its old local page (brightness, touch...). |
| RoboOS: the **Settings** app (gear icon; it replaced the Robot Control Panel) | `SettingsApp` in `robotd/desktop.py` reads `robot.settings.all()`. |
| Web app: Settings tab, "Robot settings" card (owner) | `GET /api/settings`, `POST /api/settings {"id","op"\|"value","confirmed"}`. |

A change made in one place shows in the others, because the value is only stored once:
- in `data/config.json` (voice, vision, social, desktop...), or
- on the screen, for its own hardware (brightness, auto brightness, theme, rotate, invert, panel driver). The screen
  reports these in its `hello` (`"set"`) and in a `cyd_cfg` message after every change. The Pi mirrors them in
  config `screen` and sends changes as `cfg`.

Groups: **Sound** (output, volume, mute, keep speaker awake, Bluetooth speaker, pair a new speaker, test audio, talk
during games), **Screen**, **Camera** (camera on/off, vision mode, eye options), **AI** (brain, speech recognition, web search),
**Posting** (Bluesky), **Robot** (motors status, self-tests, restart, reboot, shut down).

Not in the list on purpose, because they are features rather than settings: driving, photos, RoboDev and its
permission modes (in RoboDev itself), passwords, API keys and guest links (web app only).

## Adding a setting (for agents)

1. Add one `Item(...)` to the right group in `Settings.build()` (`robotd/settings.py`). Give it:
   - `kind`: `choice` (options `[(value, label)]`), `range` (`lo`, `hi`, `step`), `toggle`, `action` (`run=`), or `info`;
   - `get`, plus `set` (or `run` for an action);
   - `confirm="question?"` for anything drastic. Every place then asks first.
2. Restart robotd. That is all: the screen, RoboOS and the web app all show it. **No firmware flash is needed.**
3. Keep labels short (about 20 characters) and value texts under about 20 characters, because the screen is 320 px
   wide.
