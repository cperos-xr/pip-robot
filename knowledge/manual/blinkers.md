# BLINKERS display: a salvaged LED display I learned to drive

On 2026-11-11 the owner took a small white LED display out of a used vape (brand BLINKERS; the old controller board
says LY-HR37A Ver.02) and wired it to my PCF8574 expander. I ran experiments with my own camera to see what I can
show on it. The idea is to show my **battery percent** on it later (when the INA226 battery monitor arrives).
Photos and the owner's original notes: `knowledge/BlinkersDisplayExperiment/` (`IMG_5734.jpg` is the setup,
`IMG_5737.jpg` is a close-up, `blinkers_display_agent_handoff.md` is the full measured LED map).

## Wiring now: Orange Pi GPIO (since 2026-11-12, config `blinkers.backend` = `gpio`)

| Display contact | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Orange Pi 26-pin header pin | 11 | 12 | 13 | 15 | 16 | 18 | 22 | 26 |
| gpiochip0 line | PA1 = 1 | PA7 = 7 | PA0 = 0 | PA3 = 3 | PA19 = 19 | PA18 = 18 | PA2 = 2 | PA10 = 10 |

Still through the 1 kOhm resistors. A GPIO line can be HIGH, LOW or **off** (input), so this is real charlieplexing:
`robotd/blinkers_scan.py` (its own process, started by robotd, python `gpiod` 2.2 in the venv) shows one step at a time,
one cathode LOW, the wanted anodes HIGH, everything else off, `blinkers.gpio_dwell_us` (2000) per step. **Every LED can
be lit on its own, so every text, number, scroll and battery level is clean** (no extras, nothing refused). Measured:
brightness change 232-247 on camera (the PCF8574 gave 150-200), 390-570 steps a second, the scanner uses almost no CPU.
Config `blinkers.gpio_lines` maps contact -> line; the camera test (`map`) lights every ordered pin pair alone (66
patterns, about 2 minutes), so it measures every LED directly and learns the wiring (2026-11-12: it confirmed the table).
`blinkers.text_rot180` turns text for someone who sees the display upside down (digit order reversed, segments turned;
API `{"cmd":"text_rot180","on":true}`). The PCF8574 notes below describe the old wiring (`backend` = `pcf`).

## Old wiring (PCF8574)

| PCF8574 pin | Display contact |
|---|---|
| P0 .. P3 | 1 .. 4 (wired 2026-11-11, 1 kOhm each) |
| P4 .. P7 | 5 .. 8 (wired 2026-11-12) |

The PCF8574 is on the screen's I2C port (CN1, address 0x20, 3.3 V). The screen firmware (0.8.0) already had a
`pcf_write` command, so **no firmware flash was needed**. Config `blinkers.wired` maps display contact -> PCF8574 bit
(default: all 8, `{"1":0,...,"8":7}`); config `blinkers.wired_v` = 2 marks that the old 4-contact default was replaced.

## How the display works

- 8 contacts, no ground pin. Every LED sits between two contacts and lights when its **anode** contact is HIGH and
  its **cathode** contact is LOW (charlieplexing). The owner measured the map (`LEDS` in `robotd/blinkers.py`): 44 LEDs.
  - three 7-segment digits `d1A`..`d3G` (cathodes 1, 2, 3);
  - battery bars `bat100`, `bat75`, `bat50`, `bat25` (cathode 4);
  - the battery outline `outline_*`;
  - the **head**: 9 LEDs `head_*` (cathodes 5 and 6, fed from 4 to 8 different anodes);
  - the BLINKERS letters `text_*`.
- The owner's guess (2026-11-12), which fits the map: the head has so many contact pairs because the original vape
  controller **sweeps them in sequence for a shimmer**. See `animate` below.

## What the PCF8574 can and cannot do (important)

- Writing 0 to a pin makes it **LOW** (it sinks current).
- Writing 1 does **not** switch a pin off. It is a weak pull-up of about 100 uA, which happens to light a white LED
  dimly. There is no third, disconnected state.
- So **every wired contact is either HIGH or LOW**, and one write lights **every** LED between a HIGH anode and a
  LOW cathode: a block (HIGH group x LOW group). The current is under 1 mA in total, so nothing can be damaged; it is
  dim and shows best in a dark room.
- **With only contacts 1-4 wired** (2026-11-11) a pattern lit only a few LEDs and "777" was clean (3 patterns).
- **With all 8 wired** (2026-11-12) every pattern lights a bigger block, 12 LEDs on average, and a single LED alone is
  impossible. Extras glow with whatever you show. What that means (checked by the planner, `plan()`):
  - **Battery bars** are always the right ones (a gauge must not lie). 100 % is clean except two outline pieces;
    25 %, 50 % and 75 % also light some digit segments or head dots (`extra`).
  - **Numbers** never show a wrong digit segment (a 7 must not look like a 9); extras glow only in the head, outline
    and text areas. Numbers that cannot be shown that way are **refused** (every "1", "12", ...): `show_number` returns
    `ok: false`. What works: the single characters 0 2 3 7 8 9 and combinations such as "777", "85", "100", "888" (it depends on
    the neighbours: "85" works, "5" alone and "12" do not).
  - Head, outline and text shows always bring some neighbours along.
- To get clean digits again, **unplug contacts 5-8** (set `blinkers.wired` to `{"1":0,"2":1,"3":2,"4":3}`) or use a driver
  with a real off state: an **MCP23017** (each pin can be an output or a high-impedance input) makes every LED
  individually showable. Ask the owner first; it is a hardware change (about 2 USD).

## Speed and flicker

- Each write goes Pi -> USB serial -> screen board -> I2C. The screen board reads one pass of serial lines per pass of
  its main loop. **While the face animates a pass takes 15-30 ms (measured 15 ms per write, 58 writes/s for "777": each of the three
  patterns about 19 times a second: visible flicker). On a quiet page (the RoboOS desktop) a pass takes about 5 ms
  (201 writes/s, "777" at 157 writes/s: about 52 per pattern).**
- Since 2026-11-12 a multiplexed show therefore moves the screen to the RoboOS desktop while it runs (config
  `blinkers.quiet_screen`), sends one pattern every `blinkers.period_ms` (default 2 ms) on a **steady beat** without
  waiting for each reply (at most 6 writes ahead of the screen's replies), and puts the face back afterwards. Equal
  beats mean every pattern is on for the same time. `blinkers.pace` = `"sync"` goes back to waiting for every reply.
- Even faster and independent of the screen's drawing: a scan command in the screen firmware that cycles a list of bytes on
  its own (needs the owner's OK to flash).
- `flicker_test` (API) films the three digits during "777" and reports how even and steady they are.

## Text and scrolling (2026-11-12)

- `show_text(text)` (API `{"cmd":"text","text":"72°"}`, brain action `text`): up to 3 characters on the digits, from a
  7-segment font with several shapes per character (`GLYPHS`: S looks like 5, O big or small, R as r, ...). It tries
  right-aligned, then left, then centred, and every shape combination, and shows the first one with **no wrong digit
  segment**. If there is none it refuses (a wrong segment makes a different word or number).
- `scroll(text)` (API `{"cmd":"scroll","text":"RAIN 72","step_ms":450}`, brain action `scroll`): 3-character windows
  sliding right to left; a window that cannot be shown cleanly is left out (the result lists them as `skipped`).
- **With every wire on the PCF8574 most text cannot be shown cleanly.** Each digit's segments A and B are fed from the other
  two digits' contacts (d1A from contact 2, d1B from contact 3, ...). Without an off state, a digit can only show shapes
  that include A and B (0 2 3 7 8 9 A P q °) unless its neighbours help. Measured with the planner: 32 of 127 shapes per
  digit on their own, about 45 % of temperatures ("72°", "H80" work; "L65", "SUN", "FOG", "1" do not); scrolling "RAIN 72"
  skips "  R" and "RAI".
- Every text works with drivers that can switch a pin off (high impedance): 8 free GPIO pins of the Orange Pi header, or an
  MCP23017 on the I2C port. Then one cathode is LOW at a time, the wanted anodes HIGH and everything else off. (Not built
  yet: needs the owner's choice and rewiring.)
- Uses: the RoboOS **Weather** app (manual `weather.md`) cycles the temperature, high, low and the sky on the panel.

## The camera test (`experiment`)

Finds the display in the camera picture (the one place that brightens every time a pattern turns on), films patterns
and measures. **Only crops around the display are saved**, never whole room frames, because people may be in view.
Dim the room first (the LEDs are dim): in a lit room the test says "display not found".

| mode | what | time |
|---|---|---|
| `quick` | find the display, film the 7 "one cathode" patterns | about 30 s |
| `map` (the app's **Test** button) | film every pin LOW alone (8) and every pin pair LOW (28), **learn how the display is wired**, measure where every LED is | about 1 min |
| `full` | all 254 patterns, to catch any ghost glow | about 10 min |

- Each pattern is filmed off and on, twice. A pattern is "lit" when it brightens the same place both times.
  Controls (`0xFF` all released, and all low) must stay dark.
- **Learning the wiring (2026-11-12).** After the owner wired contacts 5-8 the buttons lit the wrong LEDs ("Full battery" made
  a 0 on digit 1): the contacts were not on the pins `blinkers.wired` said (byte 0x77 can only light that 0 if contacts 1 and 8
  sit on P3 and P7). The `map` test now works in raw pin space: pin q LOW alone lights every LED whose cathode is on q; pins p
  and q LOW turns off exactly the LED p -> q (a spot that goes fully dark; other spots only get dimmer because two cathodes
  share an anode's current). That gives an 8 x 8 table of which pin pairs have an LED. Every order of the 8 contacts on the
  8 pins (40320) is compared with the owner's map. The map is symmetric (contacts 1 and 2 can swap, so can 3-6), so 48 orders
  fit equally well; the shape decides (digits are 8s with A on top, digits left to right, bars in a row; the display may be
  upside down or mirrored in the camera's view). The winner is written to `blinkers.wired` and logged. Offline test:
  `tools/sim_blinkers.py wiring` (scrambled wirings, upside-down view).
- **Where is an LED?** The pattern with one cathode LOW and all other contacts HIGH lights all LEDs of that cathode.
  The same pattern with the LED's anode also LOW differs by exactly that LED. Column minus pair = that LED alone.
  The result is `data/blinkers/leds.json` (position and box of every LED in the camera crop) and a labelled picture
  `data/blinkers/led_map.jpg`. The RoboOS app draws its picture of the display from `leds.json`.
- The layout check: every digit must be an "8" (A above G above D, B and C right of F and E) and the digits must run left to
  right. `summary.layout_ok` says whether the owner's map fits what the camera sees.
- Saved in `data/blinkers/exp_*/`: `results.json`, contact sheets `sheet_01.jpg`.. (24 patterns each, camera crop plus
  heat map), `p_XX.jpg` per pattern, `led_map.jpg`. It runs in the background; `GET /api/blinkers` shows `progress`.
- Memory: the images are analysed while the next pattern is filmed (a small queue), only the patterns that locate LEDs
  are kept. The 2026-11-11 version held every frame and could not have run 254 patterns on this board.
- 2026-11-11 results with 4 contacts: 14 of 14 patterns lit as predicted, controls dark, peak about 240 against noise
  about 13, readback of the PCF8574 always equals the written byte (the LEDs do not pull a HIGH pin below the logic
  threshold, so readback cannot see LEDs).

## How to learn any salvaged LED display (the method)

1. Get the contact map: someone touches about 3 V through a resistor to each pair of contacts and writes down what lights.
2. Wire each contact through its own resistor (1 kOhm) to an output pin.
3. Write the map into `LEDS` and the wiring into `blinkers.wired`.
4. Run `experiment` mode `map`. It tries the patterns, films them, finds every LED in the picture and checks that the
   layout looks like the map. Controls must stay dark; anything lit unexpectedly means the map or the wiring is wrong.

## Using it

- RoboOS app **Blinkers** (desktop icon: a battery and a 7; `robotd/desktop_blinkers.py`):
  - The top is a picture of the display, drawn at the measured LED positions ("mapped", or a sketch until the first
    test). Tap the nearest LED to pick it (grey = not wired). Then **Show**; the status line says how many patterns it
    needs, which `extra` LEDs must glow and which it `can't` show.
  - **Test** runs the map test with a progress bar and says at the end how many LEDs it mapped.
  - **Full** (battery 100 %), **777**, **Batt -/+** (bars in 25 % steps), **Shimmer** (the head twinkles), **Scan**
    (every cathode in turn), **Chase** (the three digits one after another), **Pulse** (the battery blinks).
- Brain tool `blinkers`: actions `status`, `show` (LED names), `number`, `battery`, `animate` (shimmer, scan, chase, pulse),
  `raw` (a byte), `plan`, `off`, `experiment` (background; arg quick, map or full). It switches itself off after `secs`
  (at most config `blinkers.max_secs`, 600).
- Web API (owner): `GET /api/blinkers` (status, progress, whether it is mapped), `POST /api/blinkers {"cmd": ...}`:
  - `{"cmd":"show","value":"0xF7","secs":10}`
  - `{"cmd":"show","leds":["bat100","d2A"],"allow_extra":true}`
  - `{"cmd":"number","text":"777"}` and `{"cmd":"battery","percent":80}`
  - `{"cmd":"animate","name":"shimmer","secs":10,"frame_ms":80}` (or `"frames":["0x2F","0x5F"]`)
  - `{"cmd":"plan","leds":[...]}`
  - `{"cmd":"experiment","mode":"map","background":true}`
  - `{"cmd":"flicker_test","text":"777","secs":3}`
  - `{"cmd":"layout"}` (measured LED positions)
  - `{"cmd":"photo","label":"777"}` (a photo of just the display, averaged over 4 frames, in `data/blinkers/photos/`)
  - `{"cmd":"off"}`, `{"cmd":"write","value":"0xF7"}` (stays on until the next write), `{"cmd":"read"}`, `{"cmd":"link_test"}`
- Code: `robotd/blinkers.py` (class `Blinkers`, `robot.blinkers`).
- The housekeeping check "PCF8574 inputs changed" no longer logs the display's own patterns (they used to show up in
  the diary posts as raw text).

## Next steps

- Battery percent: when the INA226 is working, call `battery` with the real percent.
- Clean digits and every LED on its own: an MCP23017 (true off state), or leave contacts 5-8 unplugged.
- A scan command in the screen firmware for flicker-free multiplexing at any speed (needs the owner's OK to flash).
