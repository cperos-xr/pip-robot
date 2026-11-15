"""Battery: an INA219 on the Orange Pi's I2C bus 0 (address 0x40) watches the robot's battery.

Wiring (owner, 2026-11-14): pack -> protection board (BMS) -> INA219 (shunt R100 = 0.1 ohm, max ~3.2 A) -> two
regulators: 5 V (Pi, screen, USB) and 7.5 V (motor board). On USB power (no pack) the INA219 is bypassed and reads
about 0 V: state "external", shown as "USB" on the screen, no warnings and no shutdown. The BMS cuts the power by
itself when a cell gets too low; that would be a hard power cut (bad for the SD card), so this module shuts the Pi
down cleanly before that.

Packs: any number of pack profiles (Settings > Battery > Pack), each with cells in series, rated capacity, chemistry
(curve + default cutoff), and what Pip measured about it: the real capacity (from one run from full to under 30 %)
and the internal resistance (from the voltage dip when the current jumps, e.g. motors starting). At power-up the
voltage is checked against the selected pack; if it cannot be that pack, Pip switches to the one that fits or asks
on the screen when several fit. Packs with the same voltage range (3S 780 vs 3S 2000) cannot be told apart by
voltage: pick them in Settings (tap the battery on the screen).

What it does:
  - reads voltage and current once a second; the level (%) comes from counting the charge used (coulomb counting),
    slowly corrected towards a voltage-based estimate (load-compensated curve) so it cannot drift away
  - warns at the low level (screen, voice) and again at 10 %; shuts down when a cell stays under the cutoff voltage
    for 30 s (owner can cancel once on the screen; it asks again 5 minutes later)
  - "black box": appends a line every 10 s to data/battery/YYYY-MM-DD.csv (fsynced), so after an unexpected
    restart the last readings before the power went away are still there (logged at start as "before the restart")
  - the touchscreen's top bar shows the level ({"t":"batt"}); the RoboOS Battery app draws the history
Manual: knowledge/manual/battery.md. Settings: Settings > Battery (settings.py). API: GET /api/battery (packs are changed through Settings).
"""
import csv
import os
import threading
import time
from collections import deque

from .core import DATA

ADDR = 0x40
REG_CONFIG, REG_SHUNT, REG_BUS = 0x00, 0x01, 0x02
# 32 V range, shunt gain /8 (+-320 mV), 12-bit with 128-sample averaging for bus and shunt, continuous
CONFIG = 0x3FFF
LOG_DIR = DATA / "battery"
LOG_EVERY = 10          # seconds between black-box lines
HEADER = "time,volts,milliamps,pct,state,note,min_volts,max_milliamps"
HIST_EVERY = 10         # seconds between history points (kept in memory for the graph, 24 h)

# resting voltage of one cell -> percent
LIION = [(3.27, 0), (3.61, 5), (3.69, 10), (3.71, 15), (3.73, 20), (3.75, 25), (3.77, 30), (3.79, 35), (3.80, 40),
         (3.82, 45), (3.84, 50), (3.85, 55), (3.87, 60), (3.91, 65), (3.95, 70), (3.98, 75), (4.02, 80), (4.08, 85),
         (4.11, 90), (4.15, 95), (4.20, 100)]
LIHV = [(round(3.27 + (v - 3.27) * 1.08 / 0.93, 3), p) for v, p in LIION]          # same shape, full at 4.35 V
LIFEPO4 = [(2.60, 0), (2.95, 5), (3.10, 10), (3.18, 15), (3.20, 20), (3.24, 30), (3.26, 40), (3.28, 50), (3.29, 60),
           (3.31, 70), (3.32, 80), (3.34, 90), (3.38, 95), (3.45, 100)]          # very flat: the mAh count matters most
CHEM = {
    "liion": {"label": "Li-ion / LiPo", "curve": LIION, "full": 4.20, "cutoffs": (3.2, 3.3, 3.4, 3.5), "cutoff": 3.3},
    "lihv": {"label": "LiHV (4.35 V)", "curve": LIHV, "full": 4.35, "cutoffs": (3.3, 3.4, 3.5, 3.6), "cutoff": 3.4},
    "lifepo4": {"label": "LiFePO4", "curve": LIFEPO4, "full": 3.65, "cutoffs": (2.8, 2.9, 3.0, 3.1), "cutoff": 2.9},
}
CAPACITIES = [260, 390, 520, 780, 1000, 1200, 1500, 1800, 2000, 2200, 2500, 3000, 3500, 4000, 5000, 6000, 8000, 10000]
DEFAULTS = {"shunt_ohm": 0.1, "warn_pct": 20, "auto_shutdown": True, "show": "pct"}   # show: top-bar label pct / time / volts
FIRST_PACK = {"id": 1, "series": 3, "mah": 780, "chem": "liion", "cutoff": 3.3, "ohm": 0.15, "learned_mah": None,
              "learned_n": 0, "note": "nine 260 mAh vape cells, 3S3P (2026-11-14)"}


def curve_pct(curve, cell_v):
    if cell_v <= curve[0][0]:
        return 0.0
    for (v0, p0), (v1, p1) in zip(curve, curve[1:]):
        if cell_v <= v1:
            return p0 + (p1 - p0) * (cell_v - v0) / (v1 - v0)
    return 100.0


def ocv_pct(cell_v):                    # kept for older callers: Li-ion curve
    return curve_pct(LIION, cell_v)


def fmt_dur(sec):
    m = int(sec // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def pack_label(p):
    chem = "" if p["chem"] == "liion" else " " + {"lihv": "LiHV", "lifepo4": "LiFePO4"}.get(p["chem"], p["chem"])
    return f"{p['series']}S {p['mah']} mAh{chem}"


def pack_range(p):
    """Pack voltages (resting) this pack can have: a bit under its cutoff .. a bit over full."""
    ch = CHEM.get(p["chem"], CHEM["liion"])
    return p["series"] * (p["cutoff"] - 0.25), p["series"] * (ch["full"] + 0.05)


class Battery(threading.Thread):
    def __init__(self, robot):
        super().__init__(daemon=True, name="battery")
        self.r = robot
        self.ok = False                  # the INA219 answered
        self.err = ""
        self.v = self.i = 0.0            # pack voltage (V), current (A, + = drawing from the battery)
        self.i_avg = 0.0                 # current smoothed over about a minute (time left)
        self.pct = None                  # level 0..100 (None until the first reading)
        self.used_mah = 0.0              # since robotd started
        self.used_wh = 0.0
        self.test = None                 # running battery test (see start_test), or None
        self.last_test = None            # report of the last finished test
        self.state = "unknown"           # ok / low / crit / charging / external / mismatch / unknown
        self.since = time.time()
        self.hist = deque(maxlen=24 * 3600 // HIST_EVERY)   # (ts, V, mA, pct)
        self.peak_ma = 0.0
        self.last_before_start = None
        self._low_said = set()
        self._under_since = None
        self._zero_since = None          # the level has been at 0-1 % since then
        self._shutdown_pending = False
        self._cancelled_at = 0.0
        self._sent = None
        self._sent_t = 0.0
        self._last_log = self._last_hist = 0.0
        self._fh = None
        self._fh_day = ""
        self._win = None                 # (lowest V, highest A) since the last black-box line
        self._check_pack = True          # check the voltage against the selected pack at the next reading
        self._asking = None              # pack ids offered in an open "Which battery?" question
        self._learn = None               # capacity measurement in progress {"vpct", "mah0", "t"}
        self._prev = None                # previous (v, i) for the internal resistance
        self._test_answer = None
        self._dirty_t = 0.0              # learned values changed: save the config (within 30 s)
        robot.bus.on("answer", self._on_answer)
        robot.bus.on("cyd:batt_tap", lambda m: self._on_tap())

    # ------------------------------------------------------------------ config and packs
    def cfg(self):
        c = self.r.cfg.data.setdefault("battery", {})
        for k, v in DEFAULTS.items():
            c.setdefault(k, v)
        if not c.get("packs"):                    # first version kept one pack's numbers at the top level
            p = dict(FIRST_PACK)
            p["series"] = int(c.pop("cells", p["series"]))
            p["mah"] = int(c.pop("capacity_mah", p["mah"]))
            p["ohm"] = float(c.pop("pack_ohm", p["ohm"]))
            p["cutoff"] = float(c.pop("cutoff_cell_v", p["cutoff"]))
            c["packs"] = [p]
            c["pack"] = p["id"]
        return c

    def packs(self):
        return self.cfg()["packs"]

    def pack(self):
        c = self.cfg()
        return next((p for p in c["packs"] if p["id"] == c.get("pack")), c["packs"][0])

    def capacity(self, p=None):
        p = p or self.pack()
        return p["learned_mah"] if p.get("learned_mah") else p["mah"]

    def chem(self, p=None):
        return CHEM.get((p or self.pack())["chem"], CHEM["liion"])

    def set(self, key, value):
        """Top-level options: warn_pct, auto_shutdown, shunt_ohm."""
        self.cfg()[key] = value
        self.r.cfg.save()

    def set_pack(self, key, value):
        """Change the selected pack: series, mah, chem, cutoff. A new size or type starts the measurement again."""
        p = self.pack()
        if key == "chem":
            p["chem"] = value
            p["cutoff"] = CHEM[value]["cutoff"]
        else:
            p[key] = value
        if key in ("series", "mah", "chem"):
            p["learned_mah"], p["learned_n"] = None, 0
        self.r.cfg.save()
        self._reseed()

    def select(self, pid, why="owner"):
        c = self.cfg()
        if not any(p["id"] == pid for p in c["packs"]):
            return False
        c["pack"] = pid
        self.r.cfg.save()
        self._learn = None
        self._reseed()
        self.r.log(f"battery: pack {pack_label(self.pack())} selected ({why})")
        return True

    def new_pack(self):
        """A new pack, a copy of the selected one (the owner then sets cells / size / type), selected."""
        c = self.cfg()
        p = dict(self.pack())
        p.update(id=max(x["id"] for x in c["packs"]) + 1, learned_mah=None, learned_n=0, note="",
                 ohm=FIRST_PACK["ohm"], ohm_n=0)                  # its own resistance gets measured; do not inherit the old one
        p.pop("test_stop_cell_v", None)
        c["packs"].append(p)
        self.select(p["id"], "new pack")
        return p

    def delete_pack(self):
        c = self.cfg()
        if len(c["packs"]) < 2:
            return False
        c["packs"] = [p for p in c["packs"] if p["id"] != c["pack"]]
        c["pack"] = c["packs"][0]["id"]
        self.r.cfg.save()
        self._reseed()
        return True

    # ------------------------------------------------------------------ the sensor
    def _write(self, reg, val):
        bus = self.r.i2c
        bus._do(lambda: bus.bus.write_i2c_block_data(ADDR, reg, [(val >> 8) & 0xFF, val & 0xFF]), addr=ADDR)

    def _read(self, reg):
        b = self.r.i2c.read_regs(ADDR, reg, 2)
        return (b[0] << 8) | b[1]

    def setup(self):
        try:
            self._write(REG_CONFIG, CONFIG)
            self.ok = self._read(REG_CONFIG) == CONFIG
            self.err = "" if self.ok else "config did not stick"
        except OSError as e:
            self.ok, self.err = False, f"INA219 not answering at 0x{ADDR:02x} ({e})"
        return self.ok

    def sample(self):
        """-> (pack V, current A). Pack voltage = the INA219's bus voltage (load side) + the shunt drop."""
        sh = self._read(REG_SHUNT)
        if sh & 0x8000:
            sh -= 0x10000
        bus = self._read(REG_BUS)
        v_shunt = sh * 10e-6
        v_bus = (bus >> 3) * 0.004
        return v_bus + v_shunt, v_shunt / float(self.cfg()["shunt_ohm"])

    # ------------------------------------------------------------------ level
    def volt_pct(self, v=None, i=None, p=None):
        p = p or self.pack()
        v = self.v if v is None else v
        i = self.i if i is None else i
        return curve_pct(self.chem(p)["curve"], (v + max(0.0, i) * p["ohm"]) / p["series"])

    def _reseed(self):
        if self.ok and self.state not in ("external", "unknown") and self.v > 2:
            self.pct = self.volt_pct()

    def cell_v(self):
        return self.v / self.pack()["series"] if self.v else 0.0

    def time_left(self):
        """Seconds until empty at the recent average draw, or None."""
        if self.pct is None or self.state in ("charging", "external", "mismatch", "unknown") or self.i_avg < 0.02:
            return None
        return (self.pct / 100.0) * self.capacity() / (self.i_avg * 1000) * 3600

    def measured_text(self):
        p = self.pack()
        cap = f"{p['learned_mah']:.0f} mAh measured" + (f" ({p['learned_n']} runs)" if p["learned_n"] > 1 else "") \
            if p.get("learned_mah") else "size not measured yet (one run from full to under 30%)"
        return f"{cap}, {p['ohm']:.2f} ohm"

    def status(self, hist=False):
        c = self.cfg()
        tl = self.time_left()
        p = self.pack()
        d = {"ok": self.ok, "error": self.err, "state": self.state, "pct": None if self.pct is None else round(self.pct),
             "volts": round(self.v, 3), "cell_volts": round(self.cell_v(), 3), "amps": round(self.i, 3),
             "amps_avg": round(self.i_avg, 3), "watts": round(self.v * self.i, 2), "peak_ma": round(self.peak_ma),
             "used_mah": round(self.used_mah, 1), "since": self.since, "time_left_s": None if tl is None else int(tl),
             "time_left": fmt_dur(tl) if tl else "", "pack": dict(p, label=pack_label(p), capacity_used=self.capacity()),
             "packs": [dict(x, label=pack_label(x)) for x in c["packs"]],
             "measuring": bool(self._learn), "last_before_start": self.last_before_start,
             "test": self.test_status(), "last_test": self.last_test or self.r.store.kv_get("battery_test_last"),
             "options": {k: c[k] for k in DEFAULTS}, "sensor": f"INA219 0x{ADDR:02x} on Pi I2C bus 0"}
        if hist:
            d["history"] = list(self.hist)
        return d

    def short(self):
        """One line for the brain, the status page and Settings."""
        if not self.ok:
            return "battery sensor not answering (on USB power?)"
        if self.state == "external":
            return "on USB power (no battery connected; wheels have no power)"
        if self.state == "mismatch":
            return f"battery at {self.v:.2f} V does not match any battery pack in Settings > Battery"
        if self.pct is None:
            return "battery: no reading yet"
        s = f"battery {self.pct:.0f}% ({self.v:.2f} V, {self.cell_v():.2f} V/cell, pack {pack_label(self.pack())}), drawing {self.i:.2f} A"
        tl = self.time_left()
        if tl:
            s += f", about {fmt_dur(tl)} left"
        return s + (" - LOW" if self.state == "low" else " - CRITICAL" if self.state == "crit" else "")

    # ------------------------------------------------------------------ main loop
    def run(self):
        if not self.setup():
            self.r.log(f"battery: {self.err} - on USB power, or check its wiring", "warn")
        self._report_last_run()
        last = time.time()
        fails = 0
        tried = 0.0
        while True:
            time.sleep(1.0)
            now = time.time()
            if not self.ok:
                if now - tried < 10:                # missing (e.g. unpowered on USB): look again every 10 s, quietly
                    continue
                tried = now
                if not self.setup():
                    continue
                self.r.log("battery: INA219 found again")
                self._check_pack = True
                last = now
            try:
                v, i = self.sample()
                fails = 0
            except OSError as e:
                fails += 1
                if fails >= 5:
                    self.ok, self.err = False, f"lost the INA219 ({e})"
                    self.r.log(f"battery: {self.err}", "warn")
                    self.state = "unknown"
                    self._push()
                continue
            dt = min(5.0, now - last)
            last = now
            self.update(v, i, dt, now)

    def update(self, v, i, dt, now):
        c = self.cfg()
        self.v, self.i = v, i
        p = self.pack()
        if v < max(2.0, 1.8 * p["series"]):         # no pack (USB power, BMS off): the INA219 reads ~0 V
            if self.state != "external" and self.state != "unknown":
                self.r.log("battery: no battery voltage - on USB power")
            self.state = "external"
            self.pct, self._learn, self._prev = None, None, None
            self._check_pack = True                  # a pack plugged in later is checked (and the level read) again
            self.i_avg = 0.0
        else:
            if self._check_pack:
                self._check_pack = False
                self._match_pack(v, i)
                p = self.pack()
            self.i_avg = i if self.pct is None else self.i_avg + (i - self.i_avg) * min(1.0, dt / 60.0)
            self.peak_ma = max(self.peak_ma, i * 1000)
            if self.state == "mismatch":
                self.pct, self._prev = None, None
            else:
                self._learn_ohm(v, i, dt, p)
                if self.pct is None:
                    self.pct = self.volt_pct(v, i)
                mah = i * 1000 * dt / 3600.0
                self.used_mah += mah
                self.used_wh += v * i * dt / 3600.0
                self.pct -= mah / self.capacity() * 100.0
                steady = abs(i - self.i_avg) < 0.15
                if steady:                          # steady load: drift slowly towards the voltage estimate (~10 min)
                    vp = self.volt_pct(v, i)
                    # Near empty the voltage falls fast and tells more than the count: follow it in ~2.5 min.
                    # (2026-11-13: the count said 1 % while the cells were still at 3.58 V, about 7 % by voltage,
                    # and the robot ran on "at 0 %" for minutes.)
                    self.pct += (vp - self.pct) * min(1.0, dt / (150.0 if min(vp, self.pct) < 25 else 600.0))
                self.pct = max(0.0, min(100.0, self.pct))
                if steady:
                    self._learn_capacity(now, p)
                if i < -0.02:
                    self.state = "charging"
                elif self.pct <= 10 or v / p["series"] < p["cutoff"] + 0.1:
                    self.state = "crit"
                elif self.pct <= c["warn_pct"]:
                    self.state = "low"
                else:
                    self.state = "ok"
        if self._dirty_t and now - self._dirty_t > 30:    # soon: a power cut must not lose a measurement
            self._dirty_t = 0.0
            self.r.cfg.save()
        if now - self._last_hist >= HIST_EVERY:
            self._last_hist = now
            self.hist.append((int(now), round(v, 3), round(i * 1000), None if self.pct is None else round(self.pct, 1)))
        self._win = (min(self._win[0], v), max(self._win[1], i)) if self._win else (v, i)
        if now - self._last_log >= LOG_EVERY:
            self._last_log = now
            self._log_line(now)
        self._warnings(now)
        if self.test:
            self._test_step(now)
        self._push()

    # ------------------------------------------------------------------ which pack is this?
    def _match_pack(self, v, i):
        """Power-up / pack plugged in: is the voltage possible for the selected pack? If not, switch or ask."""
        packs = self.packs()
        sel = self.pack()

        def fits(p):
            lo, hi = pack_range(p)
            return lo <= v + max(0.0, i) * p["ohm"] <= hi
        if fits(sel):
            if self.state == "mismatch":
                self.state = "unknown"
            others = [p for p in packs if p is not sel and fits(p)]
            if others:
                self.r.toast(f"Battery: {pack_label(sel)} (tap the battery to change)", 4000)
            return
        cands = [p for p in packs if fits(p)]
        if len(cands) == 1:
            self.state = "unknown"
            self.select(cands[0]["id"], f"{v:.2f} V fits it")
            self.r.toast(f"Battery: switched to {pack_label(cands[0])}", 5000)
            return
        self.state = "mismatch"                     # no warnings / shutdown on numbers that may be wrong
        if cands:
            self._asking = [p["id"] for p in cands[:3]]
            self.r.cyd.send({"t": "ask", "id": "batt_pack", "title": "Which battery?",
                             "q": f"The battery reads {v:.1f} V. Which pack is it?", "opts": [pack_label(p) for p in cands[:3]]})
        else:
            self.r.log(f"battery: {v:.2f} V fits none of the packs in Settings > Battery", "warn")
            self.r.toast(f"Battery {v:.1f} V matches no pack - add it in Settings > Battery", 8000)

    def recheck(self):
        """After the owner changed packs: check the voltage again at the next reading."""
        self._check_pack = True

    # ------------------------------------------------------------------ learning the pack
    def _learn_ohm(self, v, i, dt, p):
        """Internal resistance from a current jump (motors starting/stopping): R = -dV/dI, smoothed."""
        if self._prev and dt <= 2.0:
            pv, pi = self._prev
            di = i - pi
            if abs(di) > 0.25:
                r_est = (pv - v) / di
                if 0.01 < r_est < 2.0:
                    p["ohm"] = round(p["ohm"] * 0.8 + r_est * 0.2, 4) if p.get("ohm_n") else round(r_est, 4)
                    p["ohm_n"] = p.get("ohm_n", 0) + 1
                    self._dirty_t = self._dirty_t or time.time()
        self._prev = (v, i)

    def _learn_capacity(self, now, p):
        """One run from near full (>= 85 % by voltage) to under 30 %: capacity = mAh counted / share used."""
        vp = self.volt_pct()
        if vp >= 85:
            if not self._learn or vp > self._learn["vpct"]:
                self._learn = {"vpct": vp, "mah0": self.used_mah, "t": now}
            return
        if self._learn and vp <= 30:
            drop = self._learn["vpct"] - vp
            used = self.used_mah - self._learn["mah0"]
            self._learn = None
            if drop < 50 or used <= 0:
                return
            est = used / (drop / 100.0)
            if not 0.3 * p["mah"] <= est <= 2.0 * p["mah"]:
                self.r.log(f"battery: capacity measurement {est:.0f} mAh looks wrong for {pack_label(p)}, ignored", "warn")
                return
            old = p.get("learned_mah")
            p["learned_mah"] = round(est if not old else old * 0.6 + est * 0.4)
            p["learned_n"] = p.get("learned_n", 0) + 1
            self.r.cfg.save()
            self.r.log(f"battery: measured {pack_label(p)} at about {est:.0f} mAh (now using {p['learned_mah']} mAh)")
            self.r.store.event("battery", f"measured pack {pack_label(p)}: {est:.0f} mAh")
            self.r.toast(f"I measured this battery: about {p['learned_mah']} mAh", 6000)

    # ------------------------------------------------------------------ warnings and the clean shutdown
    def _warnings(self, now):
        c = self.cfg()
        if self.state in ("external", "charging", "unknown", "mismatch") or self.pct is None:
            self._under_since = self._zero_since = None
            if self.pct is not None and self.pct > c["warn_pct"] + 5:
                self._low_said.clear()
            return
        if self.pct > c["warn_pct"] + 5:
            self._low_said.clear()                  # charged again: warn again next time
        for lvl in (c["warn_pct"], 10):
            if self.pct <= lvl and lvl not in self._low_said and now - self.since > 20:
                self._low_said.add(lvl)
                msg = f"My battery is at {self.pct:.0f} percent." + (" Please charge me soon." if lvl == 10 else "")
                self.r.log(f"battery low: {self.short()}", "warn")
                self.r.store.event("battery", f"battery low ({self.pct:.0f}%, {self.v:.2f} V)")
                self.r.face("sad" if lvl == 10 else "sleepy", msg, hold=6000)
                self.r.toast(f"Battery {self.pct:.0f}% - {self.v:.1f} V", 6000)
                try:
                    self.r.voice.speak(msg, "sad")
                except Exception:
                    pass
        # cutoff: the voltage under load stays below the limit for 30 s (short motor dips do not count)
        if self.cell_v() < self.pack()["cutoff"]:
            self._under_since = self._under_since or now
        else:
            self._under_since = None
        # ... or the level shows 0-1 % for two minutes (owner, 2026-11-14: "it was at 0% and robot was still on"):
        # empty on the screen must mean a clean shutdown, well before the protection board cuts the power
        self._zero_since = (self._zero_since or now) if self.pct <= 1.0 else None
        empty = (self._under_since and now - self._under_since >= 30) or (self._zero_since and now - self._zero_since >= 120)
        if (empty and c["auto_shutdown"] and not self._shutdown_pending
                and now - self._cancelled_at > 300 and now - self.since > 60):
            self._shutdown_pending = True
            threading.Thread(target=self._shutdown_countdown, daemon=True).start()

    def _shutdown_countdown(self):
        r = self.r
        why = f"{self.pct or 0:.0f} %, {self.v:.2f} V = {self.cell_v():.2f} V per cell (limit {self.pack()['cutoff']:.2f})"
        r.log(f"battery empty ({why}): shutting down in 30 s unless the owner cancels", "warn")
        r.store.event("battery", f"battery empty, clean shutdown ({why})")
        self._log_line(time.time(), note="shutdown")
        r.cyd.send({"t": "ask", "id": "batt_shutdown", "title": "Battery empty",
                    "q": f"Shutting down in 30 seconds to protect my memory card ({why}).", "opts": ["Shut down now", "Cancel"]})
        try:
            r.voice.speak("My battery is empty. I will shut down in 30 seconds.", "sleepy")
        except Exception:
            pass
        t_end = time.time() + 30
        while time.time() < t_end and self._shutdown_pending:
            time.sleep(0.5)
        if self._shutdown_pending:
            r.log("battery: clean shutdown now")
            r.on_power({"action": "shutdown"})

    def _on_answer(self, aid, choice):
        if aid == "batt_test_done":
            self._test_answer = choice
            return
        if aid == "batt_pack" and self._asking:
            for pid in self._asking:
                p = next((x for x in self.packs() if x["id"] == pid), None)
                if p and pack_label(p) == choice:
                    self.state = "unknown"
                    self.select(pid, "owner picked it on the screen")
            self._asking = None
            return
        if aid != "batt_shutdown":
            return
        if choice == "Cancel":
            self._shutdown_pending = False
            self._cancelled_at = time.time()
            self._under_since = None
            self.r.log("battery: owner cancelled the low-battery shutdown (asks again in 5 min)", "warn")
            self.r.toast("OK - I'll ask again in 5 minutes", 4000)
        elif choice.startswith("Shut down"):
            self._shutdown_pending = False
            threading.Thread(target=self.r.on_power, args=({"action": "shutdown"},), daemon=True).start()

    def _on_tap(self):
        self.r.toast(self.short()[:90] if self.ok else "No battery sensor (on USB power?)", 4000)

    # ------------------------------------------------------------------ the top bars (touchscreen and web app)
    def label(self):
        """The short text next to the battery icon: level, time left or volts (Settings > Battery > Top bar shows).
        Time left needs a steady reading, so it falls back to the level while there is none."""
        if self.state == "external":
            return "USB"
        if self.state == "mismatch":
            return f"{self.v:.1f}V"
        show = self.cfg().get("show", "pct")
        if show == "volts":
            return f"{self.v:.1f}V"
        tl = self.time_left() if show == "time" else None
        if tl:
            m = int(tl // 60)
            return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m}m"
        return f"{self.pct:.0f}%" if self.pct is not None else "--"

    def bar_info(self):
        """For the web app's header (GET /api/status "batt")."""
        if not self.ok or self.state == "unknown":
            return None
        return {"s": self.state, "pct": None if self.pct is None else round(self.pct), "txt": self.label(),
                "tip": self.short()}

    def screen_msg(self):
        if not self.ok or self.state == "unknown":
            return {"t": "batt", "s": "none"}
        return {"t": "batt", "p": int(round(self.pct or 0)), "v": round(self.v, 1), "txt": self.label(),
                "s": {"ok": "ok", "low": "low", "crit": "crit", "charging": "chg", "external": "ext",
                      "mismatch": "unk"}.get(self.state, "none")}

    def _push(self, force=False):
        m = self.screen_msg()
        now = time.time()
        if force or m != self._sent or now - self._sent_t > 20:    # the screen hides a value older than a minute
            if self.r.cyd.send(m):
                self._sent, self._sent_t = m, now

    # ------------------------------------------------------------------ battery test (analyse a pack)
    def start_test(self, stop_cell_v=None):
        """Discharge test with the robot as the load: counts mAh / Wh from now until the pack (average per cell, under
        load) stays under stop_cell_v for 20 s, the owner stops it, or the pack is unplugged. 3.7 V default = stops
        with about 15 % left, so even one cell 0.3 V lower than the others (an unbalanced pack) stays near 3.4 V."""
        if not self.ok or self.state in ("external", "unknown", "mismatch") or self.pct is None:
            return {"ok": False, "msg": "no battery reading: plug the pack in and pick it in Settings > Battery > Pack"}
        if self.test:
            return {"ok": False, "msg": "a test is already running"}
        stop_cell_v = float(stop_cell_v or self.pack().get("test_stop_cell_v") or 3.7)   # per pack: an unbalanced one stops earlier
        now = time.time()
        self.test = {"pack": pack_label(self.pack()), "pack_id": self.pack()["id"], "rated_mah": self.pack()["mah"],
                     "t0": now, "mah0": self.used_mah, "wh0": self.used_wh, "v0": self.v, "i0": self.i,
                     "vpct0": self.volt_pct(), "stop_cell_v": float(stop_cell_v), "under": None, "vmin": self.v,
                     "imax": self.i, "ohm0": self.pack()["ohm"]}
        self.r.log(f"battery test started on {self.test['pack']}: {self.v:.2f} V ({self.cell_v():.2f} V/cell), "
                   f"stops at {stop_cell_v:.2f} V/cell")
        self.r.store.event("battery", f"battery test started on {self.test['pack']} at {self.v:.2f} V")
        self.r.toast(f"Battery test started ({self.test['pack']})", 4000)
        return {"ok": True, "msg": "test started", "test": self.test_status()}

    def stop_test(self, why="stopped by the owner"):
        if not self.test:
            return {"ok": False, "msg": "no test running"}
        return {"ok": True, "report": self._test_finish(why)}

    def test_status(self):
        t = self.test
        if not t:
            return None
        return {"pack": t["pack"], "running_s": int(time.time() - t["t0"]), "mah": round(self.used_mah - t["mah0"]),
                "wh": round(self.used_wh - t["wh0"], 2), "volts": round(self.v, 2), "cell_volts": round(self.cell_v(), 3),
                "stop_cell_v": t["stop_cell_v"], "start_volts": round(t["v0"], 2)}

    def _test_step(self, now):
        t = self.test
        if self.state in ("external", "mismatch", "unknown"):
            self._test_finish("the pack was unplugged or no longer matches")
            return
        if self.pack()["id"] != t["pack_id"]:
            self._test_finish("another pack was selected")
            return
        t["vmin"], t["imax"] = min(t["vmin"], self.v), max(t["imax"], self.i)
        if self.cell_v() < t["stop_cell_v"]:
            t["under"] = t["under"] or now
            if now - t["under"] >= 20:
                self._test_finish(f"reached {t['stop_cell_v']:.2f} V per cell")
        else:
            t["under"] = None

    def _test_finish(self, why):
        t, self.test = self.test, None
        now = time.time()
        mah, wh = self.used_mah - t["mah0"], self.used_wh - t["wh0"]
        hours = (now - t["t0"]) / 3600.0
        vpct1 = self.volt_pct()
        share = t["vpct0"] - vpct1                     # % of the pack used, by the voltage curve
        est = mah / (share / 100.0) if share >= 20 and mah > 0 else None
        rated = t["rated_mah"]
        if est is None:
            verdict = "too short to judge the capacity (needs about 20% of the pack or more)"
        elif est >= 0.8 * rated:
            verdict = "healthy: close to its label"
        elif est >= 0.6 * rated:
            verdict = "worn: still usable, shorter runs"
        else:
            verdict = "weak: much less than its label - consider retiring it"
        full_cell = t["v0"] / self.pack()["series"]
        notes = []
        if full_cell < 4.1 and t["vpct0"] < 85:
            notes.append(f"it started at only {full_cell:.2f} V per cell (a full LiPo rests near 4.2): "
                         "the charger stopped early, often because one cell is out of balance")
        if self.pack()["ohm"] > 0.35:
            notes.append(f"high internal resistance ({self.pack()['ohm']:.2f} ohm): voltage sags a lot under load")
        if not self.pack().get("ohm_n") and est:
            notes.append("its resistance was never measured (no big current step yet), so the capacity estimate is rough")
        notes.append("the INA219 sees only the whole pack: measure each cell at the balance plug to find a bad cell")
        rep = {"pack": t["pack"], "why_stopped": why, "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(t["t0"])),
               "minutes": round(hours * 60), "mah_delivered": round(mah), "wh_delivered": round(wh, 2),
               "avg_amps": round(mah / 1000 / hours, 3) if hours > 0 else None,
               "avg_volts": round(wh / (mah / 1000), 2) if mah > 0 else None,
               "start_volts": round(t["v0"], 2), "end_volts": round(self.v, 2), "lowest_volts": round(t["vmin"], 2),
               "peak_amps": round(t["imax"], 2), "level_start_pct": round(t["vpct0"]), "level_end_pct": round(vpct1),
               "estimated_capacity_mah": round(est) if est else None, "rated_mah": rated,
               "capacity_pct_of_label": round(100 * est / rated) if est else None,
               "internal_ohm": round(self.pack()["ohm"], 3), "verdict": verdict, "notes": notes}
        self.last_test = rep
        self.r.store.kv_set("battery_test_last", rep)
        line = (f"battery test {t['pack']}: {rep['mah_delivered']} mAh / {rep['wh_delivered']} Wh in {rep['minutes']} min, "
                f"{rep['start_volts']} -> {rep['end_volts']} V" + (f", about {rep['estimated_capacity_mah']} mAh real "
                f"({rep['capacity_pct_of_label']}% of {rated})" if est else "") + f" - {verdict} ({why})")
        self.r.log(line)
        self.r.store.event("battery", line)
        threading.Thread(target=self._test_done_ask, args=(rep,), daemon=True).start()
        return rep

    def _test_done_ask(self, rep):
        """The robot keeps draining the pack after the test: shut down unless the owner says otherwise."""
        if self.state in ("external", "unknown"):
            return
        cap = f"about {rep['estimated_capacity_mah']} mAh ({rep['capacity_pct_of_label']}% of its label). " \
            if rep["estimated_capacity_mah"] else ""
        self._test_answer = None
        self.r.cyd.send({"t": "ask", "id": "batt_test_done", "title": "Battery test done",
                         "q": f"{rep['pack']}: {rep['mah_delivered']} mAh delivered, {cap}{rep['verdict']}. "
                              "Shut down now so the pack is not drained further?", "opts": ["Shut down", "Keep running"]})
        t_end = time.time() + 120
        while time.time() < t_end and self._test_answer is None:
            time.sleep(0.5)
        if self._test_answer == "Keep running":
            self.r.toast("OK - unplug the test pack soon", 5000)
            return
        if self.state not in ("external", "unknown"):
            self.r.log("battery test done: clean shutdown to protect the test pack")
            self.r.on_power({"action": "shutdown"})

    def add_pack(self, series, mah, chem="liion", note="", select=False, test_stop_cell_v=None):
        """A new pack with these numbers (API / RoboDev). The selected pack stays selected unless select=True."""
        c = self.cfg()
        keep = c["pack"]
        p = self.new_pack()
        p.update(series=int(series), mah=int(mah), chem=chem, cutoff=CHEM[chem]["cutoff"], note=note)
        if test_stop_cell_v:
            p["test_stop_cell_v"] = float(test_stop_cell_v)
        if not select:
            c["pack"] = keep
        self.r.cfg.save()
        self._reseed()
        return p

    def on_hello(self):
        self._push(force=True)

    # ------------------------------------------------------------------ black box
    def _log_line(self, now, note=""):
        try:
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            if day != self._fh_day:
                if self._fh:
                    self._fh.close()
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                f = LOG_DIR / f"{day}.csv"
                first = f.read_text().split("\n", 1)[0] if f.exists() else ""
                self._fh = open(f, "a", buffering=1)
                self._fh_day = day
                if first != HEADER:                  # new file, or one started by an older version: (another) header
                    self._fh.write(HEADER + "\n")
            pct = "" if self.pct is None else f"{self.pct:.1f}"
            lo, hi = self._win if self._win else (self.v, self.i)
            self._win = None                         # lowest volts / highest current since the last line (1 s samples)
            self._fh.write(f"{time.strftime('%H:%M:%S', time.localtime(now))},{self.v:.3f},{self.i * 1000:.0f},{pct},{self.state},{note},"
                           f"{lo:.3f},{hi * 1000:.0f}\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
            for old in sorted(LOG_DIR.glob("*.csv"))[:-30]:      # keep 30 days
                old.unlink()
        except OSError as e:
            self.err = f"black box: {e}"

    def _report_last_run(self):
        """At start: load the recent history and log the last line written before this start."""
        files = sorted(LOG_DIR.glob("*.csv"))[-2:]
        rows = []
        for f in files:
            try:
                with open(f) as fh:
                    for row in csv.DictReader(fh):
                        try:
                            ts = time.mktime(time.strptime(f"{f.stem} {row['time']}", "%Y-%m-%d %H:%M:%S"))
                            rows.append((int(ts), float(row["volts"]), int(float(row["milliamps"])),
                                         float(row["pct"]) if row.get("pct") else None, row.get("state", ""), row.get("note", ""),
                                         row.get("min_volts") or (row.get(None) or [None, None])[0],
                                         row.get("max_milliamps") or (row.get(None) or [None, None])[-1]))
                        except (ValueError, KeyError, TypeError):
                            pass
            except OSError:
                pass
        cutoff = time.time() - 24 * 3600
        for ts, v, ma, p, *_ in rows:
            if ts >= cutoff:
                self.hist.append((ts, v, ma, p))
        if rows:
            ts, v, ma, p, st, note, lo, hi = rows[-1]
            self.last_before_start = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), "volts": v,
                                      "milliamps": ma, "pct": p, "state": st, "note": note, "min_volts": lo, "max_milliamps": hi}
            self.r.log(f"battery: last reading before this start {self.last_before_start['time']}: {v:.2f} V, {ma} mA"
                       + (f", {p:.0f}%" if p is not None else "") + (f" (10 s window: lowest {lo} V, highest {hi} mA)" if lo else "")
                       + (f" ({note})" if note else ""))
        else:
            self.last_before_start = None
