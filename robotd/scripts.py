"""Robot Scripts: play a demo from start to end, hands-free (manual: knowledge/manual/scripts.md).

A script is a JSON file of steps: spoken lines, face moods, pauses for the presenter (with the presenter's line as a
cue for the phone's teleprompter), small drives, a wheelie, and a journal entry. Shipped examples live in
routines/scripts/, the owner's own in data/scripts/ (private: never committed; same name -> the owner's wins).

Safety: the owner approves ONCE when pressing Play (web app confirm, or the RoboOS app's question), after checking the
floor. That approval covers this one playback only, including a wheelie later in it, so no Start question pops up
on the screen mid-show. The motors are never unlocked here: the owner does that as always. Stop (web app) cancels
the rest, halts the wheels and lands a running wheelie."""
import json
import threading
import time

from .core import DATA, ROOT

SHIPPED = ROOT / "routines" / "scripts"
OWN = DATA / "scripts"
ACTIONS = ("say", "wait", "face", "drive", "wheelie", "journal", "page")
MOVES = ("turn", "forward", "back", "arc", "wiggle", "stop")
MOODS = ("idle", "happy", "curious", "thinking", "warning", "sleepy", "surprised", "sad", "love", "auto")
MAX_STEPS, MAX_WAIT, MAX_MOVE_S, MAX_SAY = 100, 30.0, 5.0, 300


def _action(st):
    acts = [k for k in ACTIONS if k in st]
    if not acts and "cue" in st:
        return "wait"                      # a bare cue: show it and go on (with "wait": pause for the presenter)
    return acts[0] if len(acts) == 1 else None


def check_steps(steps, where="steps", inner=False):
    """Problems with a list of steps ([] = fine). inner: a wheelie's fallback (no wheelie or journal inside)."""
    from .wheelie import ROUTINES
    errs = []
    if not isinstance(steps, list) or not steps:
        return [f"{where}: a non-empty list of steps"]
    if len(steps) > MAX_STEPS:
        errs.append(f"{where}: at most {MAX_STEPS} steps")
    for i, st in enumerate(steps, 1):
        at = f"{where} {i}"
        if not isinstance(st, dict):
            errs.append(f"{at}: each step is an object")
            continue
        a = _action(st)
        if a is None:
            errs.append(f"{at}: one of {', '.join(ACTIONS)} (or a cue)")
            continue
        if inner and a in ("wheelie", "journal"):
            errs.append(f"{at}: no {a} inside a fallback")
        try:
            if a == "say" and not (isinstance(st["say"], str) and 0 < len(st["say"]) <= MAX_SAY):
                errs.append(f"{at}: say is text of 1-{MAX_SAY} characters")
            if a == "wait" and not 0 <= float(st.get("wait", 0)) <= MAX_WAIT:
                errs.append(f"{at}: wait is 0-{MAX_WAIT:g} seconds")
            mood = st.get("mood") if a == "say" else st.get("face") if a == "face" else None
            if mood is not None and mood not in MOODS:
                errs.append(f"{at}: mood is one of {', '.join(MOODS)}")
            if a == "drive":
                m = st["drive"]
                if not isinstance(m, dict) or str(m.get("cmd", "")).lower() not in MOVES:
                    errs.append(f"{at}: drive is {{\"cmd\": {' | '.join(MOVES)}, ...}}")
                elif float(m.get("secs", 0)) > MAX_MOVE_S or abs(float(m.get("deg", 0))) > 720:
                    errs.append(f"{at}: a move is at most {MAX_MOVE_S:g} s (turns at most 720 degrees)")
            if a == "wheelie":
                w = st["wheelie"] if isinstance(st["wheelie"], dict) else {}
                rt = w.get("routine")
                if rt is not None and not isinstance(rt, list) and str(rt).lower() not in ROUTINES:
                    errs.append(f"{at}: routine is one of {', '.join(ROUTINES)} (or a list of moves)")
                if not 1 <= float(w.get("secs", 8)) <= 30:
                    errs.append(f"{at}: wheelie secs is 1-30")
                if "fallback" in st:
                    errs += check_steps(st["fallback"], f"{at} fallback", inner=True)
            if a == "journal" and not (isinstance(st["journal"], str) and 0 < len(st["journal"]) <= MAX_SAY):
                errs.append(f"{at}: journal is text of 1-{MAX_SAY} characters")
        except (TypeError, ValueError) as e:
            errs.append(f"{at}: {e}")
    return errs


def estimate_s(steps):
    t = 0.0
    for st in steps:
        a = _action(st)
        t += float(st.get("wait", 0)) if a == "wait" else 0
        t += len(st.get("say", "")) / 13 + 0.8 if a == "say" else 0
        t += float((st.get("wheelie") or {}).get("secs", 8)) + 6 if a == "wheelie" else 0
        t += float((st.get("drive") or {}).get("secs", 1.5)) if a == "drive" else 0
    return round(t)


class ScriptPlayer:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.Lock()                 # one playback at a time
        self.cancel = threading.Event()
        self.next_ev = threading.Event()
        self.state = {"busy": False}

    # ------------------------------------------------------------------ files
    def files(self):
        out = {}
        for folder, own in ((SHIPPED, False), (OWN, True)):
            if folder.is_dir():
                for f in sorted(folder.glob("*.json")):
                    out[f.stem] = (f, own)
        return out

    def load(self, name):
        f, own = self.files().get(str(name), (None, False))
        if not f:
            raise KeyError(f"no script called {name!r}")
        doc = json.loads(f.read_text())
        errs = check_steps(doc.get("steps")) if isinstance(doc, dict) else ["the file is a JSON object with \"steps\""]
        return doc, own, errs

    @staticmethod
    def uses(steps):
        acts = [_action(s) for s in steps]
        fb = [_action(x) for s in steps for x in (s.get("fallback") or [])]
        return {"wheelie": "wheelie" in acts, "drive": "drive" in acts or "drive" in fb}

    def readiness(self, steps, wheelie_ok=True):
        """(problems that stop Play, notes) for this robot right now."""
        r, u = self.r, self.uses(steps)
        bad, notes = [], []
        if self.state.get("busy"):
            bad.append("a script is already playing")
        if u["drive"] or (u["wheelie"] and wheelie_ok):
            if not r.cfg["motors_enabled"]:
                bad.append("the motors are locked: unlock them on the Live tab first")
            elif not (r.cfg.data.get("wheels") or {}).get("left"):
                bad.append("run the wheels test first (Tests > Wheel setup)")
        if u["wheelie"]:
            why = "" if wheelie_ok else "you chose no wheelie"
            why = why or r.wheelie.missing() or ("busy: " + r.wheelie.busy if r.wheelie.busy else "")
            notes.append(f"wheelie: {why}, so it does the fallback instead" if why else "wheelie: ready")
        try:
            if not r.voice.level():
                notes.append("the sound is muted: Pip's lines will only show on its face")
        except Exception:
            pass
        return bad, notes

    def list(self):
        out = []
        for name, (f, own) in self.files().items():
            item = {"name": name, "own": own}
            try:
                doc, _, errs = self.load(name)
                steps = doc.get("steps") or []
                bad, notes = self.readiness(steps) if not errs else ([], [])
                item.update(title=doc.get("title") or name, about=doc.get("about", ""), steps=len(steps),
                            secs=estimate_s(steps), errors=errs, problems=bad, notes=notes, **self.uses(steps),
                            wheelie_step=next((i + 1 for i, s in enumerate(steps) if _action(s) == "wheelie"), None))
            except Exception as e:
                item.update(title=name, errors=[str(e)])
            out.append(item)
        return out

    def status(self):
        return dict(self.state)

    # ------------------------------------------------------------------ play
    def play(self, name, wheelie_ok=True, who="web"):
        """Start a playback in the background -> (ok, message). Pressing Play is the owner's approval."""
        doc, _, errs = self.load(name)
        if errs:
            return False, "the script has problems: " + "; ".join(errs[:3])
        steps = doc["steps"]
        bad, notes = self.readiness(steps, wheelie_ok)
        if bad:
            return False, "; ".join(bad)
        if not self.lock.acquire(blocking=False):
            return False, "a script is already playing"
        self.cancel.clear()
        self.next_ev.clear()
        self.state = {"busy": True, "name": name, "title": doc.get("title") or name, "i": 0, "n": len(steps),
                      "doing": "", "line": "", "cue": "", "next_cue": self._next_cue(steps, 0), "error": "",
                      "notes": notes, "log": [], "t0": time.time(), "wheelie_ok": bool(wheelie_ok)}
        self.r.log(f"script {name!r} started ({who}; owner approved{'' if wheelie_ok else ', no wheelie'})")
        threading.Thread(target=self._play, args=(steps, bool(wheelie_ok)), daemon=True, name="script-play").start()
        return True, f"playing {self.state['title']}"

    def next(self):
        """The presenter is done: end the current (or the next) pause now."""
        self.next_ev.set()
        return {"ok": True}

    def stop(self):
        if not self.state.get("busy"):
            return {"ok": True, "msg": "nothing is playing"}
        self.cancel.set()
        self.next_ev.set()
        self.r.drive.stop()
        if self.r.wheelie.busy == "kick":
            self.r.wheelie._stop = True                # lands it if it is up (wheelie.py, the run loop)
        self.r.log("script stopped by the owner")
        return {"ok": True, "msg": "stopped"}

    @staticmethod
    def _next_cue(steps, i):
        return next((s["cue"] for s in steps[i:] if s.get("cue")), "")

    def _play(self, steps, wheelie_ok):
        r = self.r
        mm = getattr(r, "motion_modes", None)
        quiet = bool(mm and mm.busy.acquire(blocking=False))   # no motion-sensor greetings during the show
        r.last_reaction = time.time() + 3600                    # no bump / tilt faces either (a wheelie tilts 60 deg)
        try:
            r.cyd.send({"t": "page", "name": "face"})          # the face, never the desktop, for the whole show
            for i, st in enumerate(steps):
                if self.cancel.is_set():
                    self._log("stopped")
                    break
                self.state.update(i=i + 1, cue=st.get("cue", ""), next_cue=self._next_cue(steps, i + 1))
                self._step(st, wheelie_ok)
        except Exception as e:
            self.state["error"] = str(e)
            r.log(f"script: {e}", "warn")
        finally:
            r.drive.halt()
            r.last_reaction = time.time()
            if quiet:
                mm.busy.release()
            done = "stopped" if self.cancel.is_set() else "error" if self.state.get("error") else "done"
            r.store.event("script", f"script {self.state.get('name')}: {done} after step {self.state.get('i')}/{len(steps)}")
            self.state.update(busy=False, doing=done, cue="", next_cue="")
            self.lock.release()

    def _log(self, text):
        self.state["log"] = (self.state["log"] + [text])[-30:]

    def _step(self, st, wheelie_ok):
        r, a = self.r, _action(st)
        if a == "say":
            self.state.update(doing="say", line=st["say"])
            r.voice.speak(st["say"], st.get("mood"))           # blocks until the line is spoken: lines never overlap
            self._log("said: " + st["say"][:60])
        elif a == "wait":
            secs = float(st.get("wait", 0))
            self.state.update(doing="wait", line="")
            end = time.time() + secs
            while time.time() < end and not self.cancel.is_set() and not self.next_ev.is_set():
                time.sleep(0.05)
            if self.next_ev.is_set() and not self.cancel.is_set():
                self._log(f"pause cut short by Next ({secs - max(0, end - time.time()):.1f} of {secs:g} s)")
            self.next_ev.clear()
        elif a == "face":
            self.state["doing"] = "face"
            r.face(st["face"], st.get("say"), hold=int(st.get("hold", 3000)))
        elif a == "page":
            r.cyd.send({"t": "page", "name": str(st["page"])})
        elif a == "journal":
            r.journal.write("script", "(note to self)", st["journal"])
            self._log("journal: " + st["journal"][:60])
        elif a == "drive":
            self.state["doing"] = "drive " + str(st["drive"].get("cmd"))
            self._log(self._drive(st["drive"]))
        elif a == "wheelie":
            self._wheelie(st, wheelie_ok)

    def _drive(self, m):
        d = self.r.drive
        d.wheels()
        if not self.r.cfg["motors_enabled"]:
            raise PermissionError("the motors were locked")
        if not d.lock.acquire(timeout=3):
            raise RuntimeError("the wheels are busy with another drive")
        try:
            d.cancel.clear()
            d.rc_on = False
            return d.run_step(m)
        finally:
            d.halt()
            d.lock.release()

    def _wheelie(self, st, wheelie_ok):
        r, w = self.r, self.r.wheelie
        spec = st["wheelie"] if isinstance(st["wheelie"], dict) else {}
        why = "" if wheelie_ok else "no wheelie chosen at Play"
        why = why or ("" if r.cfg["motors_enabled"] else "motors locked") or w.missing() or ("busy" if w.busy else "")
        res = None
        if not why:
            moves = w.routine(spec.get("routine"))
            if moves and r.motor.version < (0, 4):
                moves = None                               # moves while balancing need motor firmware 0.4
            need = sum(mv[0] for mv in moves) + 1.0 if moves else 0
            balms = max(1.0, min(30.0, max(float(spec.get("secs", 8)), need))) * 1000
            self.state.update(doing="wheelie", line="")
            w._stop = False
            prev = w.ok_until
            w.ok_until = max(prev, time.time() + 5)        # the owner approved the whole script at Play
            try:
                res = w.run("kick", ask=False, moves=moves, balms=balms)
            finally:
                w.ok_until = prev
                w._stop = False
            if not res.get("ok") and not res.get("end"):
                why = str(res.get("msg") or "it did not start")
                res = None
        if res is not None:
            line = w.announce(res, speak=False)
            self._log(f"wheelie: {res.get('end')} ({res.get('balance_ms', 0) / 1000:.1f} s)")
            if not self.cancel.is_set():
                self.state["line"] = line
                r.voice.speak(line)
            return
        self._log(f"wheelie skipped ({why}): fallback")
        for fb in st.get("fallback") or []:
            if self.cancel.is_set():
                break
            self._step(fb, wheelie_ok)
