# Robot Scripts (hands-free demos)

A script is a short show: Pip's lines, face moods, pauses for the presenter, small drives, a wheelie, and a journal
entry, played from start to end without anyone touching the robot. Code: `robotd/scripts.py`.

## Where scripts live
- `routines/scripts/*.json`: shipped examples (public, in git). Start from `meet_pip.json`.
- `data/scripts/*.json`: the owner's own (private, never committed). A file here wins over a shipped one with the
  same name. Real journal entries, names and event details belong here, never in `routines/`.

## Playing one
- **Phone (web app, Live tab > Scripts):** pick, Play, confirm. During the show the card is a teleprompter (the
  presenter's next line in big letters, Pip's current line under it) with **Next** and **Stop**.
- **Robot screen (RoboOS > Start > Scripts):** tap a script and answer the one question.
- **API:** `POST /api/scripts/play {"name": "meet_pip", "wheelie": true}`, `/api/scripts/next`,
  `/api/scripts/stop`; `GET /api/scripts` (list, readiness, state), `GET /api/scripts?status=1` (state only).

## Safety: one approval
Pressing Play (and confirming "floor is clear") is the owner's approval for **that one playback**, including a
wheelie later in it. No Start question appears on the screen mid-show; the approval is never stored.
- Play is refused while the motors are locked or the wheels test was never done (unlock them on the Live tab as
  usual: scripts never unlock motors).
- A wheelie that is not ready (`Wheelie.missing()`: battery on USB, no IMU data, setup missing) or "Start without the
  wheelie" runs the step's `fallback` instead (e.g. a 360 turn on the ground and a wiggle).
- **Stop** cancels the rest, halts the wheels and lands a running wheelie. The line being spoken finishes.
- During a show: no bump/tilt faces (a wheelie tilts about 60 degrees) and no motion-sensor greetings.
- A wheelie still needs about 1 m clear in front and behind, the battery (not USB), and someone ready to catch.

## Format
```json
{"title": "Meet Pip", "about": "what it is for",
 "steps": [
  {"cue": "YOU: Pip, say hello!", "wait": 6},
  {"say": "Hello everyone!", "mood": "happy"},
  {"face": "surprised", "hold": 3000},
  {"drive": {"cmd": "wiggle", "n": 4}},
  {"wheelie": {"routine": "spin", "secs": 8},
   "fallback": [{"drive": {"cmd": "turn", "deg": 360}}]},
  {"journal": "I met a new group of people today."}
 ]}
```
| Step | What it does |
|---|---|
| `say` (+ `mood`) | Speaks and shows the line; waits until it is spoken, so lines never overlap |
| `wait` (+ `cue`) | Pause, at most 30 s. `cue` = the presenter's line for the teleprompter. **Next** ends the pause early (a Next tapped while Pip talks ends the next pause) |
| `face` (+ `hold` ms, `say`) | A mood: idle, happy, curious, thinking, warning, sleepy, surprised, sad, love, auto |
| `drive` | One onboard move (`motors.md`): turn (deg), forward/back (secs, speed), arc, wiggle (n), stop. At most 5 s |
| `wheelie` | Kick up, a routine while balancing (stay, spin, spin_both, rock, dance), land; then Pip says how it went. `fallback`: steps used when there is no wheelie |
| `journal` | Writes the text into Pip's journal (src `script`), so a line about today is true afterwards |
| `page` | Switch the screen page (rarely needed: the show starts on the face) |

Checks (self-test `scripts_valid`): at most 100 steps, known steps and moods, waits up to 30 s, moves up to 5 s.
Write lines for espeak: short sentences, spelled-out dates ("November eleventh"), commas where Pip should breathe.
