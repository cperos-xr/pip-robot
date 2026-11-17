"""Wheelies (owner, 2026-11-14: "do you think a wheelie mode is possible? ... use the imu to balance perfectly on 2
wheels"). The fast part runs in the motor board (firmware rp2040_motor_usb 0.2+: IMU on its GPIO26/27, read 500 times a
second, kick + balance loop). This module is the slow part: it sends the settings, asks the owner on the touchscreen,
keeps the run alive, fetches the 500 Hz recording afterwards and saves it in data/wheelie/.

POST /api/wheelie (owner only):
  {"cmd": "setup"}                    one-time, after the IMU was moved: flat + still, then front held up (on-screen steps)
  {"cmd": "show", "secs": 6}          THE TRICK, with the saved settings: answers at once, the run goes on by itself and
                                      the result is said and shown on the screen (Settings > Robot > Wheelie, the
                                      web app's Wheelie button and Pip's own `wheelie` tool all use this)
  {"cmd": "show", "secs": 10, "routine": "spin"}   the same with moves while balancing (ROUTINES below; firmware 0.4)
  {"cmd": "practice", "n": 5, "secs": 20}   PRACTICE (firmware 0.4): one Start tap, then n attempts by itself. In each
                                      one it balances for secs and switches every 2.5 s between the saved settings and
                                      one changed setting; the better one is kept. After each attempt it turns and
                                      drives back to where it started (estimated: there are no wheel encoders).
                                      Everything is logged in data/wheelie/learning.jsonl. Stop button on the screen.
  {"cmd": "kick", ...settings}        back/forward kick; the nose comes up; power off (or balance when balms > 0)
  {"cmd": "arm", ...settings}         hold the robot at its balance angle; it takes over when it is within 4 degrees
  {"cmd": "balance", ...settings}     balance right now
  {"cmd": "record", "secs": 8}        only record (e.g. while the owner does a wheelie with the joystick)
  {"cmd": "set", ...settings}         save settings      {"cmd": "stop"}
  "ask": false skips the Start question on the touchscreen (only inside 10 minutes after a Start tap).
GET /api/wheelie: settings, what is missing, the last runs.   GET /api/wheelie?run=<name>: one run with its full trace.
"""
import json
import math
import os
import threading
import time

from .core import DATA

DIR = DATA / "wheelie"
ACC_LSB, GYRO_LSB = 8192.0, 32.8          # must match the firmware
# name: (default, lowest, highest, what it is) - the same table as `prm` in the firmware, minus the wheel wiring
PARAMS = {
    "k1p": (-127, -127, 127, "kick part 1 power (minus = backwards)"),
    "k1ms": (0, 0, 1500, "kick part 1 time, ms (0 = skip)"),
    "k2p": (127, -127, 127, "kick part 2 power"),
    "k2ms": (300, 0, 1500, "kick part 2 longest time, ms"),
    "catch": (50, 5, 120, "nose this many degrees up: kick is over"),
    "tgt": (70, 20, 110, "balance angle, degrees nose up"),
    "kp": (8, 0, 60, "power per degree off balance"),
    "kd": (0.3, 0, 5, "power per degree/second of tipping"),
    "ki": (0, 0, 50, "power per degree*second"),
    "kv": (0, 0, 0.5, "lean back per unit of speed (stops it running away)"),
    "kx": (0, 0, 0.5, "lean back per unit of distance"),
    "tm": (0.15, 0.02, 2, "motor time constant for the speed estimate, s"),
    "ky": (0, 0, 2, "steering: power per degree/second of turning"),
    "db": (0, 0, 90, "power added to pass the motors' dead zone"),
    "max": (127, 0, 127, "power cap while balancing"),
    "balms": (0, 0, 120000, "longest balance, ms (0 = kick only; over 15000 needs firmware 0.4)"),
    "lo": (20, -10, 100, "nose lower than this: give up"),
    "hi": (110, 30, 170, "nose higher than this: give up"),
    "tau": (0.5, 0.05, 10, "gravity correction time when idle, s"),
    "taur": (2, 0.05, 20, "gravity correction time in a run, s"),
    "recdiv": (1, 1, 20, "record every n-th 500 Hz sample"),
    "tail": (1500, 0, 5000, "ms recorded after the run ends"),
    "armms": (20000, 1000, 60000, "arm: wait this long to be held at the balance angle"),
    "cut": (120, 0, 120, "nose this high: kick power off, coast from here (firmware 0.3)"),
    "cp": (0, -127, 127, "power while coasting (firmware 0.3)"),
    "cms": (800, 0, 3000, "longest coast, ms (firmware 0.3)"),
    "dbw": (1, 1, 40, "blend the dead-zone power in over this much power (1 = a step; firmware 0.3)"),
    "lp": (70, 0, 127, "landing: power backwards (firmware 0.3)"),
    "lms": (200, 0, 1000, "landing: for this long, ms (0 = just power off; firmware 0.3)"),
    "kh": (0, 0, 10, "heading hold: power per degree off the heading (firmware 0.4)"),
    "smax": (40, 0, 127, "cap on the steering power (firmware 0.4)"),
    "va": (60, 5, 500, "moves: how fast the speed setpoint changes, power units per second (firmware 0.4)"),
}
NEEDS_03 = ("cut", "cp", "cms", "dbw", "lp", "lms")
NEEDS_04 = ("kh", "smax", "va")
# moves while balancing: [seconds, speed (power units, + = forward), turn (deg/s, + = left)]; after the last one: stay
ROUTINES = {
    "stay": [],
    "spin": [[1.5, 0, 0], [4, 0, 90], [1, 0, 0]],
    "spin_both": [[1.5, 0, 0], [4, 0, 90], [1, 0, 0], [4, 0, -90], [1, 0, 0]],
    "rock": [[1.5, 0, 0], [2.5, 25, 0], [2.5, -25, 0], [1.5, 0, 0]],      # 1.2 s steps were too quick to follow (2026-11-14)
    "dance": [[1.5, 0, 0], [2, 0, 90], [2, 0, -90], [2.5, 25, 0], [2.5, -25, 0], [4, 0, 90], [1.5, 0, 0]],
}
# practice: the settings it may change by itself: (name, "mul" | "add", step, lowest, highest)
TUNE = [("kd", "mul", 1.3, 0.12, 1.0), ("db", "add", 8, 15, 70), ("dbw", "mul", 1.6, 3, 40), ("kp", "add", 1.5, 3, 14),
        ("ky", "add", 0.15, 0, 1.5), ("kh", "add", 0.6, 0, 6)]
SEG, SETTLE = 2.5, 2.0                    # practice: seconds per A/B piece; seconds before the first piece
KD_CATCH, CATCH_S = 0.35, 1.2             # the kick is caught with at least this much kd, for this long (see _run)
KD_MOVES = 0.26                           # moves disturb the balance: at least this much kd while a routine runs
P0, TLAG = 40, 0.12                       # distance guess: power below P0 does not move the wheels; they follow in TLAG s
LEARN = DIR / "learning.jsonl"
MODES = {0: "idle", 1: "kick1", 2: "kick2", 3: "balance", 4: "armed", 5: "coast", 6: "land"}


class Wheelie:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.Lock()              # one run at a time
        self._ev = threading.Event()
        self._end = ""
        self.busy = ""
        self.ok_until = 0.0                        # a Start tap covers the next runs for 10 minutes
        robot.motor.on_event = self._on_event
        robot.motor.on_imu = self._on_imu
        self._live = None                          # open file while a run is on: every IMU sample goes into it at once
        robot.motor.on_connect = self.configure
        robot.motor.on_note = self._on_note
        self._bal_t = 0.0                          # when the balancing of the current run started (firmware 0.4 says so)
        self._samples = None                       # list of (time, numbers) of the current run's IMU lines
        self._stop = False                         # the Stop button on the screen during practice
        robot.bus.on("answer", self._on_answer)
        robot.imu.on_mount = lambda: threading.Thread(target=self.configure, daemon=True).start()

    # ------------------------------------------------------------------ settings
    def cfg(self):
        return self.r.cfg.data.setdefault("wheelie", {"params": {}})

    def params(self):
        saved = self.cfg().get("params") or {}
        return {k: saved.get(k, d[0]) for k, d in PARAMS.items()}

    @staticmethod
    def clean(vals):
        out = {}
        for k, v in (vals or {}).items():
            if k in PARAMS:
                _, lo, hi, _ = PARAMS[k]
                out[k] = max(lo, min(hi, float(v)))
        return out

    def set(self, vals):
        new = self.clean(vals)
        self.cfg().setdefault("params", {}).update(new)
        self.r.cfg.save()
        return new

    # ------------------------------------------------------------------ motor board
    def fw_ok(self):
        return self.r.motor.up and self.r.motor.version >= (0, 2)

    def configure(self):
        """Tell the motor board where up and forward are, the gyro bias, which channels are the wheels, and to send
        50 IMU samples a second. Called when it (re)starts and when the mount changes."""
        m, r = self.r.motor, self.r
        if not self.fw_ok():
            return False
        c = r.cfg["imu"]
        ok = (m.cmd("GB " + " ".join(f"{v:.4f}" for v in c["gyro_bias"])) or "").startswith("OK")
        if c.get("leveled") and c.get("forward_set"):
            ok &= m.cmd("AX " + " ".join(f"{v:.5f}" for v in list(c["up"]) + list(c["forward"]))) == "OK AX"
        w = r.cfg.data.get("wheels") or {}
        if w.get("left") and w.get("right"):
            for name, val in (("wl", w["left"]["ch"] - 1), ("wr", w["right"]["ch"] - 1),
                              ("dl", w["left"].get("dir") or 1), ("dr", w["right"].get("dir") or 1)):
                ok &= (m.cmd(f"SET {name} {val}") or "").startswith("OK SET")
        ok &= (m.cmd("IMU 50") or "").startswith("OK IMU")
        r.log(f"motor board set up for the IMU ({'ok' if ok else 'some commands failed'})", "info" if ok else "warn")
        return ok

    def _on_note(self, text):
        if text.startswith("bal"):
            self._bal_t = time.time()

    def _on_answer(self, aid, choice):
        if aid == "wheelie:stop":
            self._stop = True
            self.r.log("wheelie practice: Stop tapped")

    def _on_imu(self, v):
        self.r.imu.on_rp2040(v)
        sm = self._samples
        if sm is not None:
            sm.append((time.time(), v))
        f = self._live
        if f:
            try:
                f.write("%.3f %s\n" % (time.time(), " ".join(map(str, v))))
            except ValueError:
                pass

    def _live_sync(self, f):
        """Keeps the live log on the SD card (2026-11-14: a flip cut the power and the recording in the motor board
        was lost). Its own thread, because a slow fsync must not delay the keepalive."""
        while self._live is f:
            try:
                f.flush()
                os.fsync(f.fileno())
            except (ValueError, OSError):
                return
            time.sleep(0.1)

    def _on_event(self, text):
        self._end = text
        self._ev.set()

    def missing(self):
        """Why a run cannot start now ('' = ready)."""
        r = self.r
        if not r.motor.up:
            return "the motor board is not answering"
        if r.motor.version < (0, 2):
            return "the motor board needs firmware 0.2 (it has %d.%d)" % r.motor.version
        if r.imu.source != "rp2040" or time.time() - r.imu.last > 0.5:
            return "no IMU data from the motor board (check the wires on GPIO26/27)"
        c = r.cfg["imu"]
        if not (c.get("leveled") and c.get("forward_set")):
            return "run the wheelie setup first (the IMU was moved)"
        if not (r.cfg.data.get("wheels") or {}).get("left"):
            return "run the wheels test first"
        if r.battery.ok and r.battery.state == "external":
            return "on USB power the wheels have no power: connect the battery"
        if r.battery.ok and r.battery.state == "crit":
            return "the battery is almost empty (a kick could switch me off): charge it first"
        return ""

    # ------------------------------------------------------------------ one-time setup
    def setup(self, level=True):
        r, ask = self.r, self.r.api.ask_owner
        if not self.fw_ok():
            return False, "the motor board needs firmware 0.2"
        if r.imu.source != "rp2040" or time.time() - r.imu.last > 0.5:
            return False, "no IMU data from the motor board (check the wires on GPIO26/27)"
        if not self.lock.acquire(blocking=False):
            return False, "busy: " + self.busy
        self.busy = "setup"
        try:
            msg = msg2 = "kept"
            if level or not r.cfg["imu"].get("leveled"):
                if ask("Wheelie setup 1/2", "Put me FLAT on the floor, hands off, then tap Ready. I will hold still for 3 seconds.",
                       ["Ready", "Cancel"], 120) != "Ready":
                    return False, "cancelled: nobody tapped Ready"
                time.sleep(1.0)
                ok, msg = r.imu.level_here()
                if not ok:
                    return False, "level: " + msg
                ok, msg2 = r.imu.gyro_cal()
                if not ok:
                    return False, "gyro: " + msg2
            r.cyd.send({"t": "ask", "id": "", "title": "Wheelie setup 2/2",
                        "q": "Now lift my FRONT end up, like a wheelie (about halfway up), and hold it still. I will beep.", "opts": ["OK"]})
            r.voice.speak("Now lift my front end up, like a wheelie, and hold it.")
            end, steady, seen = time.time() + 60, 0, 0.0
            while time.time() < end:
                time.sleep(0.2)
                t = r.imu.tilt_from_up()
                g = max(abs(v) for v in r.imu.gyro)
                seen = max(seen, t or 0)
                steady = steady + 1 if (t is not None and 20 < t < 85 and g < 30) else 0
                if steady >= 4:
                    break
            else:
                r.toast("Setup: front not held up - try again")
                return False, f"flat and gyro are done, but the front was never held still 20-85 degrees up (most I saw: {seen:.0f}): run it again"
            ok, msg3 = r.imu.forward_here()
            if not ok:
                return False, msg3
            self.configure()
            r.voice.speak("Got it. You can put me down.")
            r.toast("Wheelie setup done")
            r.log(f"wheelie setup: {msg}; {msg2}; {msg3}")
            return True, f"{msg}; {msg2}; {msg3}"
        finally:
            self.busy = ""
            self.lock.release()

    # ------------------------------------------------------------------ runs
    def run(self, kind="kick", ask=True, secs=8, moves=None, **over):
        r = self.r
        kind = {"bal": "balance"}.get(kind, kind)
        if kind not in ("kick", "arm", "balance", "record"):
            return {"ok": False, "msg": "kind: kick | arm | balance | record"}
        if kind != "record":
            if not r.cfg["motors_enabled"]:
                return {"ok": False, "msg": "motors are locked (owner: unlock them first)"}
            miss = self.missing()
            if miss:
                return {"ok": False, "msg": miss}
        elif not self.fw_ok():
            return {"ok": False, "msg": "the motor board needs firmware 0.2"}
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "msg": "busy: " + self.busy}
        self.busy = kind
        try:
            return self._run(kind, ask, secs, over, moves=moves)
        finally:
            self.busy = ""
            self.lock.release()

    @staticmethod
    def _move_at(moves, el):
        for secs, v, w in moves:
            if el < secs:
                return v, w
            el -= secs
        return 0, 0

    def _run(self, kind, ask, secs, over, moves=None, ab=None):
        """One run (the caller holds self.lock). ab = {"name", "a", "b"}: while balancing, switch that setting between
        a and b every SEG seconds; the result gets "ab": the score of each piece."""
        r, m = self.r, self.r.motor
        marks = []
        try:
            p = {**self.params(), **self.clean(over)}
            if kind in ("arm", "balance") and p["balms"] < 1:
                return {"ok": False, "msg": "balms is 0: give a balance time, e.g. balms 3000"}
            if m.version < (0, 4):
                p["balms"] = min(p["balms"], 15000)
                moves = ab = None
            if moves:            # 2026-11-14: spin both ways with kd 0.16 swung 45-67 degrees, with 0.26 only 51-60
                p["kd"] = max(p["kd"], KD_MOVES)
            if kind != "record" and (ask or time.time() > self.ok_until):
                what = {"kick": f"Kick: {'back %d ms, ' % p['k1ms'] if p['k1ms'] else ''}forward up to {p['k2ms']:.0f} ms at power {p['k2p']:.0f}"
                                + (f", then balance at {p['tgt']:.0f} deg for up to {p['balms'] / 1000:.0f} s." if p["balms"] >= 1 else ", then power off."),
                        "arm": f"Tap Start, then hold me lightly on my two wheels, nose up about {p['tgt']:.0f} degrees (where I feel "
                               f"weightless). When I am steady there the wheels start: let go, keep your hands close. Up to {p['balms'] / 1000:.0f} s.",
                        "balance": f"Balance now at {p['tgt']:.0f} degrees for up to {p['balms'] / 1000:.0f} s."}[kind]
                tail = "" if kind == "arm" else " Clear floor in front and behind, hands off, then tap Start."
                ans = r.api.ask_owner("Wheelie", what + tail, ["Start", "Cancel"], 180)
                if ans != "Start":
                    if ans is None:      # the question stays on the screen after its time is up: a late tap would do nothing
                        r.cyd.send({"t": "ask", "id": "", "title": "Wheelie", "opts": ["OK"],
                                    "q": "Nobody tapped Start in 3 minutes, so I cancelled it. Nothing will move."})
                    return {"ok": False, "msg": "nobody tapped Start"}
                self.ok_until = time.time() + 600
                time.sleep(1.5)                                   # hand away from the screen
            if kind != "record":
                for k, v in p.items():
                    if (k in NEEDS_03 and m.version < (0, 3)) or (k in NEEDS_04 and m.version < (0, 4)):
                        continue
                    if not (m.cmd(f"SET {k} {v:g}") or "").startswith("OK SET"):
                        return {"ok": False, "msg": f"the motor board did not take the setting {k}"}
                r.drive.stop()
            # Practice found that less kd balances more steadily, but the first second after the kick needs the damping
            # (2026-11-14: with kd 0.16 the nose swung up to 66 degrees instead of 59). So the catch keeps its own kd.
            soft_kd = kind == "kick" and m.version >= (0, 4) and p["kd"] < KD_CATCH
            if soft_kd:
                m.cmd(f"SET kd {KD_CATCH:g}")
            v0 = r.battery.v if r.battery.ok else None
            self._ev.clear()
            self._end = ""
            self._bal_t = 0.0
            self._samples = []
            DIR.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d_%H%M%S") + "_" + kind
            live = open(DIR / (name + "_live.txt"), "w")     # time, then the numbers of each "I" line (100 a second)
            live.write("# " + json.dumps(p) + "\n")
            self._live = live
            threading.Thread(target=self._live_sync, args=(live,), daemon=True).start()
            m.cmd("IMU 100")
            t0 = time.time()
            if kind == "record":
                rep = m.cmd("REC 5000")
                if not (rep or "").startswith("OK REC"):
                    return {"ok": False, "msg": f"motor board: {rep}"}
                r.toast("Recording the IMU...", int(secs * 1000))
                time.sleep(max(1.0, min(10.0, float(secs))))
            else:
                rep = m.cmd({"kick": "GO", "arm": "ARM", "balance": "BAL"}[kind])
                if not (rep or "").startswith("OK"):
                    return {"ok": False, "msg": f"motor board: {rep}"}
                longest = (p["k1ms"] + p["k2ms"] + p["cms"] + p["balms"] + p["lms"] + (p["armms"] if kind == "arm" else 0)) / 1000 + 1.0
                seg = -1
                while not self._ev.is_set() and time.time() - t0 < longest:
                    line, bt = "K", self._bal_t              # without a line every 0.5 s the firmware stops
                    if bt:
                        el = time.time() - bt
                        if soft_kd and el >= CATCH_S:
                            soft_kd = False
                            m.cmd(f"SET kd {p['kd']:g}", 0.3)
                        if moves:
                            line = "MV %g %g" % self._move_at(moves, el)
                        if ab and el >= SETTLE and int((el - SETTLE) // SEG) != seg:
                            seg = int((el - SETTLE) // SEG)
                            which = "b" if seg % 2 else "a"
                            m.cmd(f"SET {ab['name']} {ab[which]:g}", 0.3)
                            marks.append((time.time(), which))
                    if self._stop:                           # Stop on the screen: land if it is up, else just stop
                        if not (bt and (m.cmd("LAND", 0.3) or "").startswith("OK")):
                            m.cmd("STOP")
                        self._ev.wait(1.0)
                        break
                    m.send(line)
                    self._ev.wait(0.1)
                if not self._ev.is_set():
                    m.cmd("STOP")
                marks.append((time.time(), "end"))
                time.sleep(p["tail"] / 1000 + 0.3)                # the firmware records the landing
            samples, self._samples = self._samples, None
            rows = m.read_dump() or []
            v1 = r.battery.v if r.battery.ok else None
            res = self._save(name, kind, p, rows, self._end, v0, v1)
            if ab:
                res["ab"] = {"name": ab["name"], "a": ab["a"], "b": ab["b"],
                             "pieces": [dict(self.piece_score(samples, marks[i][0] + 0.5, marks[i + 1][0]), which=marks[i][1])
                                        for i in range(len(marks) - 1)]}
            self._learn(res)
            return res
        finally:
            self._samples = None
            live, self._live = self._live, None
            if live:
                live.close()
            try:
                m.cmd("STOP")
                m.cmd("IMU 50")
            except Exception:
                pass

    def show(self, secs=None, routine=None):
        """The trick as the owner wants to show it: kick up, balance, land, with the saved settings. Starts in the
        background (the Start question on the touchscreen comes first) -> (ok, message)."""
        r = self.r
        why = "motors are locked (unlock them in the web app, Live tab)" if not r.cfg["motors_enabled"] else self.missing()
        if not why and self.busy:
            why = "busy: " + self.busy
        if why:
            r.toast("Wheelie: " + why, 4000)
            return False, why
        over = {"balms": max(1.0, min(120.0, float(secs))) * 1000} if secs else {}
        if self.params()["balms"] < 1 and not over:
            over = {"balms": 6000}
        if routine in ("stay", ""):
            routine = None
        moves = ROUTINES.get(str(routine or "").lower()) if not isinstance(routine, list) else routine
        if routine and moves is None:
            return False, "routines: " + ", ".join(ROUTINES)
        if moves:
            if r.motor.version < (0, 4):
                return False, "moves while balancing need motor firmware 0.4"
            need = sum(mv[0] for mv in moves) + 1.0
            if (over.get("balms") or self.params()["balms"]) / 1000 < need:
                over["balms"] = need * 1000

        def go():
            res = self.run("kick", ask=True, moves=moves, **over)
            if not res.get("ok"):
                if res.get("msg") != "nobody tapped Start":
                    r.toast("Wheelie: " + str(res.get("msg")), 4000)
                return
            self.announce(res)
        threading.Thread(target=go, daemon=True, name="wheelie-show").start()
        return True, "wheelie: tap Start on my screen when the floor is clear"

    def announce(self, res, speak=True):
        """After a show run: the face and the line that says how it went (also used by robotd/scripts.py)."""
        r = self.r
        held = res.get("balance_ms", 0) / 1000
        end = res.get("end", "").split(" ")[0]
        if end == "landed":
            line, mood = f"I balanced on two wheels for {held:.0f} seconds!", "happy"
        elif held >= 1:
            line, mood = f"I held it for {held:.1f} seconds, then lost it.", "surprised"
        else:
            line, mood = "That one did not work. I did not get up on two wheels.", "sad"
        r.face(mood, line, hold=4000)
        if speak:
            r.voice.speak(line)
        r.store.event("wheelie", f"{line} (run {res.get('name')}, end: {res.get('end')})")
        return line

    @staticmethod
    def routine(x):
        """A routine name or a list of [seconds, speed, turn] steps -> the list (None = just stay)."""
        if isinstance(x, list):
            return [[float(a), float(b), float(c)] for a, b, c in x][:40]
        return ROUTINES.get(str(x or "").lower()) or None

    def stop(self):
        self.r.motor.cmd("STOP")
        return {"ok": True, "msg": "stopped"}

    # ------------------------------------------------------------------ results
    def _save(self, name, kind, p, rows, end, v0, v1):
        data = []
        for line in rows:
            try:
                f = [int(x) for x in line.split()]
            except ValueError:
                continue
            if len(f) == 11:                           # firmware before 0.4: no heading column
                f.append(0)
            if len(f) == 12:
                data.append(f)
        with open(DIR / (name + ".csv"), "w") as fh:
            fh.write("t_ms,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,pitch_deg,left,right,mode,heading_deg\n")
            for f in data:
                fh.write("%.1f,%.3f,%.3f,%.3f,%.1f,%.1f,%.1f,%.2f,%d,%d,%d,%.1f\n" % (
                    f[0] / 1000, f[1] / ACC_LSB, f[2] / ACC_LSB, f[3] / ACC_LSB, f[4] / GYRO_LSB, f[5] / GYRO_LSB,
                    f[6] / GYRO_LSB, f[7] / 100, f[8], f[9], f[10], f[11] / 10))
        s = {"ok": True, "name": name, "kind": kind, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "end": end or "-",
             "samples": len(data), "params": p, "volts_before": v0, "volts_after": v1}
        if data:
            t = [f[0] / 1000 for f in data]
            pit = [f[7] / 100 for f in data]
            pw = [f[8] for f in data]
            i_max = max(range(len(pit)), key=lambda i: pit[i])
            s.update({"secs": round(t[-1] / 1000, 2), "max_pitch": round(pit[i_max], 1), "max_pitch_ms": round(t[i_max]),
                      "start_pitch": round(pit[0], 1), "end_pitch": round(pit[-1], 1)})
            for deg in (10, 30, 45, 60, 90):
                hit = next((t[i] for i in range(len(pit)) if pit[i] >= deg), None)
                if hit is not None:
                    s[f"ms_to_{deg}"] = round(hit)
            bal = [t[i] for i, f in enumerate(data) if f[10] == 3]
            if bal:
                s["balance_ms"] = round(bal[-1] - bal[0])
            s["metrics"] = self.analyse(data, p)
            on = [t[i] for i in range(len(pw)) if pw[i]]
            if on:
                s["power_on_ms"] = [round(on[0]), round(on[-1])]
            # a short trace (every 20 ms) for a quick look without the CSV: ms, pitch, pitch rate, power, mode
            step = max(1, len(data) // 400)
            R = self.r.imu._R                              # robot axes in sensor coordinates: rate = -gyro . left
            bias = self.r.cfg["imu"]["gyro_bias"]
            s["trace"] = [[round(t[i]), round(pit[i], 1),
                           round(-sum((data[i][4 + k] / GYRO_LSB - bias[k]) * R[1][k] for k in range(3))), pw[i], data[i][10]]
                          for i in range(0, len(data), step)]
        (DIR / (name + ".json")).write_text(json.dumps(s))
        self.r.log(f"wheelie {kind}: end '{s['end']}', highest {s.get('max_pitch', '?')} deg, {len(data)} samples -> {name}.csv")
        return s

    # ------------------------------------------------------------------ how good was it
    def _rate(self, g):
        """Pitch rate (deg/s, + = nose going up) from a gyro reading in deg/s, sensor axes."""
        R, bias = self.r.imu._R, self.r.cfg["imu"]["gyro_bias"]
        return -sum((g[k] - bias[k]) * R[1][k] for k in range(3))

    def _yaw_rate(self, g, pitch):
        """Turn rate around the vertical (deg/s, + = left): right also with the nose up."""
        R, bias = self.r.imu._R, self.r.cfg["imu"]["gyro_bias"]
        gz = sum((g[k] - bias[k]) * R[2][k] for k in range(3))
        gx = sum((g[k] - bias[k]) * R[0][k] for k in range(3))
        return gz * math.cos(math.radians(pitch)) + gx * math.sin(math.radians(pitch))

    @staticmethod
    def _score(rock, sway, effort, turn):
        """One number, lower = better: rocking (deg/s), pitch spread (deg), wheel effort, turning (deg/s)."""
        return round(rock + 8 * sway + 0.15 * effort + 2 * abs(turn), 1)

    def piece_score(self, samples, t0, t1):
        """Score of the IMU lines between two times (practice compares pieces of one balance)."""
        w = [v for t, v in samples or [] if t0 <= t < t1 and len(v) >= 11 and v[9] == 3]
        if len(w) < 40:
            return {"n": len(w), "score": None}
        pit = [v[7] / 100 for v in w]
        rate = [self._rate([v[3] / 10, v[4] / 10, v[5] / 10]) for v in w]
        mean = sum(pit) / len(pit)
        rock = math.sqrt(sum(x * x for x in rate) / len(rate))
        sway = math.sqrt(sum((x - mean) ** 2 for x in pit) / len(pit))
        effort = sum(abs(v[8]) for v in w) / len(w)
        turn = (w[-1][10] - w[0][10]) / 10 / max(0.5, t1 - t0)
        return {"n": len(w), "rock": round(rock, 1), "sway": round(sway, 2), "effort": round(effort), "turn": round(turn, 1),
                "pitch": round(mean, 1), "score": self._score(rock, sway, effort, turn)}

    def analyse(self, data, p):
        """Numbers for a whole recording (rows of the firmware's DUMP): how steady the balance was, how far it turned
        and (roughly) how far it rolled."""
        t = [f[0] / 1e6 for f in data]
        pit = [f[7] / 100 for f in data]
        pw = [(f[8] + f[9]) / 2 for f in data]
        g = [[f[4] / GYRO_LSB, f[5] / GYRO_LSB, f[6] / GYRO_LSB] for f in data]
        out = {}
        head = dist = v = 0.0
        for i in range(1, len(data)):
            dt = min(0.05, t[i] - t[i - 1])
            head += self._yaw_rate(g[i], pit[i]) * dt
            pe = math.copysign(max(0.0, abs(pw[i]) - P0), pw[i])    # power that really turns the wheels
            v += (pe - v) * min(1.0, dt / TLAG)
            dist += v * dt
        out["heading"] = round(head, 1)                # degrees turned, + = left
        out["dist"] = round(dist, 1)                   # power x seconds, + = ended in front of the start (a rough guess)
        bal = [i for i, f in enumerate(data) if f[10] == 3]
        if bal:
            st = [i for i in bal if t[i] - t[bal[0]] > 0.8] or bal
            mean = sum(pit[i] for i in st) / len(st)
            rate = [self._rate(g[i]) for i in st]
            rock = math.sqrt(sum(x * x for x in rate) / len(rate))
            sway = math.sqrt(sum((pit[i] - mean) ** 2 for i in st) / len(st))
            effort = sum(abs(pw[i]) for i in st) / len(st)
            secs = max(0.5, t[st[-1]] - t[st[0]])
            turn = sum(self._yaw_rate(g[i], pit[i]) * min(0.05, t[i] - t[i - 1]) for i in st[1:]) / secs
            out.update({"held": round(t[bal[-1]] - t[bal[0]], 1), "rock": round(rock, 1), "sway": round(sway, 2),
                        "effort": round(effort), "push": round(sum(pw[i] for i in st) / len(st), 1), "pitch": round(mean, 1),
                        "turn": round(turn, 1), "overshoot": round(max(pit[i] for i in bal[:300]) - p["tgt"], 1),
                        "score": self._score(rock, sway, effort, turn)})
        return out

    def _learn(self, res):
        """Every run goes into the learning log: settings, numbers, how it ended."""
        try:
            row = {k: res.get(k) for k in ("name", "kind", "time", "end", "max_pitch", "balance_ms", "volts_after", "metrics", "ab")}
            row["params"] = res.get("params")
            with open(LEARN, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            self.r.log(f"wheelie learning log: {e}", "warn")

    def learned(self, n=200):
        rows = []
        try:
            for line in LEARN.read_text().splitlines()[-n:]:
                rows.append(json.loads(line))
        except (OSError, ValueError):
            pass
        return rows

    # ------------------------------------------------------------------ back to the start
    def go_home(self, metrics):
        """Undo the turn and (roughly) the roll of the last run, so the next one starts in the same place. The turn is
        measured (gyro); the distance is a guess from the wheel power (no wheel encoders)."""
        r = self.r
        if abs(r.imu.pitch) > 20 or abs(r.imu.roll) > 20:
            return "not flat"
        head, dist = metrics.get("heading", 0), metrics.get("dist", 0)
        r.drive.cancel.clear()
        did = []
        try:
            if abs(head) > 5:
                got = r.drive.turn(-max(-170, min(170, head)))
                did.append(f"turned {got:+.0f} deg")
            if abs(dist) > 4:
                secs = min(1.5, abs(dist) / (90 - P0))
                r.drive.straight(secs, 90, back=dist > 0)
                did.append(f"{'back' if dist > 0 else 'forward'} {secs:.2f} s")
        except (RuntimeError, PermissionError) as e:
            did.append(f"stopped: {e}")
        return ", ".join(did) or "stayed"

    # ------------------------------------------------------------------ practice
    def _candidate(self, p):
        """The next setting to try: {"name", "a": saved value, "b": changed value}."""
        st = self.cfg().setdefault("tuner", {"i": 0, "dir": 1})
        for _ in range(2 * len(TUNE)):
            name, how, step, lo, hi = TUNE[st["i"] % len(TUNE)]
            cur = p[name]
            new = cur * step ** st["dir"] if how == "mul" else cur + step * st["dir"]
            new = round(max(lo, min(hi, new)), 3)
            if abs(new - cur) > 1e-6:
                return {"name": name, "a": cur, "b": new}
            self._advance(st)
        return None

    @staticmethod
    def _advance(st):
        if st["dir"] == 1:
            st["dir"] = -1
        else:
            st["i"], st["dir"] = st["i"] + 1, 1

    def practice(self, n=5, secs=20, ask=True):
        """n attempts by itself (owner, 2026-11-14: "it can even repeat several attempts on its own to gather more and
        more data ... it should always be learning"). One Start tap for all of them; Stop on the screen ends it."""
        r, m = self.r, self.r.motor
        if not r.cfg["motors_enabled"]:
            return {"ok": False, "msg": "motors are locked (owner: unlock them first)"}
        miss = self.missing() or ("practice needs motor firmware 0.4" if m.version < (0, 4) else "")
        if miss:
            return {"ok": False, "msg": miss}
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "msg": "busy: " + self.busy}
        n, secs = max(1, min(12, int(n))), max(8.0, min(60.0, float(secs)))
        self.busy, self._stop = "practice", False
        log, why, misses = [], "done", 0
        try:
            if ask:
                ans = r.api.ask_owner("Wheelie practice", f"{n} wheelies in a row, {secs:.0f} s each. I drive back to my start after each "
                                      "one, but only roughly: keep about 1.5 m clear all around. Tap Start, then hands off.",
                                      ["Start", "Cancel"], 180)
                if ans != "Start":
                    if ans is None:
                        r.cyd.send({"t": "ask", "id": "", "title": "Wheelie practice", "opts": ["OK"],
                                    "q": "Nobody tapped Start in 3 minutes, so I cancelled it. Nothing will move."})
                    return {"ok": False, "msg": "nobody tapped Start"}
                self.ok_until = time.time() + 600
                time.sleep(1.5)
            for k in range(n):
                if self._stop:
                    why = "Stop tapped"
                    break
                if r.battery.ok and r.battery.state in ("low", "crit", "external"):
                    why = "battery " + r.battery.state
                    break
                p = self.params()
                cand = self._candidate(p)
                r.cyd.send({"t": "ask", "id": "wheelie:stop", "title": f"Wheelie practice {k + 1}/{n}", "opts": ["Stop"],
                            "q": (f"Trying {cand['name']} {cand['b']:g} against {cand['a']:g}." if cand else "Same settings.")
                                 + " Tap Stop to end the practice."})
                res = self._run("kick", False, 0, {"balms": secs * 1000}, ab=cand)
                if not res.get("ok"):
                    why = res.get("msg", "failed")
                    break
                mt = res.get("metrics", {})
                row = {"n": k + 1, "run": res["name"], "end": res["end"], "held": mt.get("held"), "score": mt.get("score"),
                       "rock": mt.get("rock")}
                if cand:
                    try:
                        row.update(self._judge(cand, res))
                    except Exception as e:               # judging must never end the session half way
                        r.log(f"wheelie practice: judging failed: {e!r}", "warn")
                log.append(row)
                r.log(f"wheelie practice {k + 1}/{n}: {row}")
                end = res["end"].split(" ")[0]
                time.sleep(1.0)
                if abs(r.imu.pitch) > 20 or abs(r.imu.roll) > 20:      # on its back or its side: it needs a hand
                    why = "I am not flat on the floor after that one"
                    break
                if end in ("imu", "failsafe"):
                    why = "run ended with: " + end
                    break
                misses = misses + 1 if mt.get("held") is None else 0
                if misses >= 2:        # 2026-11-14: a wheel had worked loose; six kicks in a row lifted the nose only 2-8 degrees
                    why = "the kick did not get me up twice in a row (is a wheel loose or slipping?)"
                    break
                row["home"] = self.go_home(mt)
                time.sleep(0.8)
            r.cfg.save()
            good = [x for x in log if x.get("score")]
            line = (f"Practice over: {len(log)} wheelies. " + (f"My best score was {min(x['score'] for x in good):.0f}, "
                    f"the first was {good[0]['score']:.0f} (lower is steadier)." if good else "")
                    + ("" if why == "done" else f" I stopped because: {why}."))
            kept = [f"{x['name']} {x['b']:g}" for x in log if x.get("kept")]
            r.cyd.send({"t": "ask", "id": "", "title": "Wheelie practice", "opts": ["OK"],
                        "q": line + (" Kept: " + ", ".join(kept) + "." if kept else " No setting changed.")})
            r.voice.speak(line)
            r.store.event("wheelie", line, log=log)
            return {"ok": True, "msg": line, "why": why, "attempts": log, "params": self.params()}
        finally:
            self.busy, self._stop = "", False
            self.lock.release()

    def _judge(self, cand, res):
        """Did the changed setting (b) beat the saved one (a) in this run? Keep it if it clearly did."""
        st = self.cfg().setdefault("tuner", {"i": 0, "dir": 1})
        every = (res.get("ab") or {}).get("pieces") or []
        pieces = [x for x in every if x.get("score") is not None]
        a = [x["score"] for x in pieces if x["which"] == "a"]
        b = [x["score"] for x in pieces if x["which"] == "b"]
        out = {"name": cand["name"], "a": cand["a"], "b": cand["b"], "kept": False}
        fell = not res["end"].startswith("landed")
        if a and b:
            out["score_a"], out["score_b"] = round(sum(a) / len(a), 1), round(sum(b) / len(b), 1)
        last = every[-1].get("which") if every else None
        if not b and not (fell and last == "b"):         # the changed value was never tried (the kick failed, or it
            out["judged"] = False                        # fell before the first switch): try the same one again
        elif a and len(b) >= 2 and not (fell and last == "b") and out["score_b"] < 0.93 * out["score_a"]:
            out["kept"] = True
            self.set({cand["name"]: cand["b"]})          # same setting, same direction again next time
        else:
            self._advance(st)
        return out

    def runs(self, n=10):
        out = []
        for f in sorted(DIR.glob("*.json"), reverse=True)[:n]:
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            d.pop("trace", None)
            d.pop("params", None)
            out.append(d)
        return out

    def get_run(self, name):
        f = (DIR / (name + ".json")).resolve()
        if DIR.resolve() not in f.parents or not f.is_file():
            return None
        return json.loads(f.read_text())

    def status(self):
        r = self.r
        return {"ready": not self.missing() and bool(r.cfg["motors_enabled"]), "missing": self.missing(), "busy": self.busy,
                "motors_enabled": bool(r.cfg["motors_enabled"]), "firmware": r.motor.fw, "imu_source": r.imu.source,
                "pitch": round(r.imu.pitch, 1), "board_pitch": r.imu.rp_pitch,
                "params": self.params(), "help": {k: v[3] for k, v in PARAMS.items()}, "runs": self.runs(),
                "routines": list(ROUTINES), "tuner": self.cfg().get("tuner"),
                "scores": [[x.get("name"), (x.get("metrics") or {}).get("score")] for x in self.learned(30)
                           if (x.get("metrics") or {}).get("score") is not None]}
