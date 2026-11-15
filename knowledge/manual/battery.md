# Battery (INA219 monitor, level, warnings, clean shutdown)

**Hardware (owner, 2026-11-14):** a home-made **3S3P pack of nine 260 mAh vape cells** = 780 mAh, 11.1 V nominal,
12.6 V full, with a **3S protection board** (BMS: cuts the power by itself when a cell is too low or on a short).
After it an **INA219** (I2C address 0x40 on the Orange Pi's bus 0, pins 3/5; shunt R100 = 0.1 ohm, max ~3.2 A)
measures **the whole robot**: after it the pack feeds two regulators, **5 V** (Pi, screen, USB) and **7.5 V**
(motor board EXT VOLTAGE). So driving shows up in the current, and 3S (9.9 V at the shutdown point) keeps the
7.5 V regulator in range until the end.

**USB power:** sometimes the robot runs from USB only: the INA219 is bypassed and reads ~0 V, the wheels get no
power. State `external`: the screen shows "USB", no warnings, no shutdown, no level. When a pack is connected
again the level is read fresh from its voltage and the pack is checked (below). If the INA219 itself has no power
it simply stops answering: the battery disappears from the top bar and robotd looks for it every 10 s.

**Several packs:** Settings > Battery keeps a list of packs (`data/config.json` battery.packs), each with
cells in series (1S-6S), rated mAh, type (Li-ion/LiPo, LiHV 4.35 V, LiFePO4: own voltage curve and cutoff
choices) and "Empty at". "Add pack" copies the selected one; set its cells / size / type. Names are made from
the numbers ("3S 780 mAh", "2S 2000 mAh LiFePO4"). Remove = web app only.
- At power-up (and when a pack is plugged in after USB) the voltage is checked against the selected pack. Does not
  fit -> switches to the one pack that fits, or asks "Which battery?" on the screen when several fit, or shows
  "?" + the voltage when none fits (no warnings/shutdown then). Same cell count = same voltage range: Pip cannot
  tell a 3S 780 from a 3S 2000; it keeps the selected one and toasts its name (tap the battery to change).
- Pip **measures every pack**: the real capacity from one run from >= 85% to <= 30% (by voltage, steady load):
  mAh counted / share used, averaged over runs (sanity limit 0.3x-2x rated), and the internal resistance from
  current jumps (motors starting: R = -dV/dI, smoothed). % and time left then use the measured size.
  "Measured" row in Settings. Changing size, cells or type of a pack starts its measurement again.

**Battery test (analyse a pack):** Battery app > Test (or `POST /api/battery {"cmd":"test_start"}`, `test_stop`).
The robot is the load (~0.4 A idle). Counts mAh + Wh until the average cell voltage under load stays below the
pack's test stop (default 3.7 V = about 15 % left; per pack `test_stop_cell_v`) for 20 s, the owner stops it, or the
pack is unplugged. Report (log, events, kv `battery_test_last`, `GET /api/battery` "last_test"): mAh/Wh delivered,
start/end/lowest volts, estimated real capacity = mAh / share of the voltage curve used (needs >= 20 %), % of the
label, internal resistance, verdict (healthy >= 80 %, worn 60-80 %, weak < 60 %) and notes. Then it asks on the
screen "Shut down / Keep running" and shuts down cleanly after 2 min without an answer (the pack would keep draining).
The INA219 sees only the whole pack: one bad cell must be found with a meter at the balance plug.
Packs: owner's airsoft LiPo 3S 1100 mAh 15C (one cell does not finish charging = out of balance), test stop 3.7.

**Software:** `robotd/battery.py` (thread "battery").
- Reads voltage + current once a second (128-sample averaging in the INA219). Pack V = bus V + shunt drop.
- Level %: counts the charge used (mAh) and slowly corrects towards a voltage-based estimate (LiPo curve,
  load-compensated with `pack_ohm`), so motor dips do not make the number jump.
- Time left = level x capacity / current averaged over about a minute.
- States: `ok`, `low` (at "Warn me at", default 20%), `crit` (10% or near the cutoff), `charging` (current
  flows into the pack), `external` (under 5 V: no pack / BMS off), `unknown` (sensor missing).
- Warnings: face + toast + voice once at the warn level and once at 10%. They re-arm after a charge.
- **Empty means off (2026-11-14):** the clean shutdown below also starts when the level has shown 0-1 % for two minutes.
  Near empty (under 25 %) the level follows the voltage in about 2.5 minutes instead of 10: the night before, the
  count said 1 % while the cells were still at 3.58 V (about 7 % by voltage) and the robot ran on "at 0 %".
  The protection board on the pack cuts the power at some lower voltage (not known); the Pi must be off before that.
  Wheelies are refused when the battery is "crit" (a kick pulls the voltage down and could switch the robot off).
- **Clean shutdown:** a cell under `cutoff_cell_v` (default 3.3 V, i.e. 9.9 V for the pack) for 30 s, while working ->
  "Battery empty" question on the screen with a 30 s countdown (Shut down now / Cancel). Cancel = asks again
  after 5 min. This happens BEFORE the BMS cuts the power (a hard cut can damage the SD card). Off in Settings.
- **Black box:** a line every 10 s in `data/battery/YYYY-MM-DD.csv` (fsynced; 30 days kept). At start robotd logs
  "battery: last reading before this start ..." = the last numbers before an unexpected restart. Use this first when
  the robot rebooted by itself (low voltage? big current spike?).

**Where it shows:**
- Touchscreen top bar (firmware 0.9.1+, phone style): clock top left, battery icon + text top right (green / yellow low / red empty /
  blue + bolt charging / "USB" on USB power / "?" + volts = unknown pack); the middle shows the Bluesky numbers
  on the face and "< Page title" elsewhere (tap left/middle = back).
  Tap the battery = Settings (Battery is the first group) + a toast with the numbers.
  The text next to the icon is chosen on the Pi (Settings > Battery > Top bar shows: Level % (default) /
  Time left ("1h16", falls back to % without a steady reading) / Volts) and sent as "txt" in {"t":"batt"}.
- Web app header: the same battery icon + text before "temp · cpu" (GET /api/status "batt" = {s, pct, txt, tip});
  hover = full line, tap = Settings tab. Hidden while the robot is offline.
- Settings > Battery (screen, RoboOS Settings, web Settings): level, current, time left, cell voltage,
  Show graph, Pack, Cells in series, Battery size, Type, Empty at, Measured, Warn me at, Shut down when empty,
  Top bar shows, Add pack, Remove this pack (web only), Sensor (web only).
- RoboOS app **Battery** (`robotd/desktop_battery.py`): big level, V / A / W, time left, graph of the last
  1 / 6 / 24 h (navy = volts, red = amps).
- `GET /api/battery` (add `?history=1` for 24 h of points `[ts, V, mA, %]`). Status page / Devices: "Battery".
- The brain's context line has the battery, so Pip knows its level.

**Config** (`data/config.json` "battery"): warn_pct 20, auto_shutdown true, shunt_ohm 0.1, pack (selected id),
packs [{id, series, mah, chem, cutoff, ohm, learned_mah, learned_n, note}]. The first pack: 3S 780 mAh Li-ion,
cutoff 3.3 (migrated from the first version's top-level cells/capacity_mah/pack_ohm/cutoff_cell_v).

**First readings (2026-11-14):** 12.1-12.2 V, 0.33-0.43 A idle/booting (~4-5 W) -> about 1.5-2 h on a full pack
without driving. The robot restarted by itself once that day right after the pack was fitted (cause unknown;
the black box did not exist yet).
