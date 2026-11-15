"""What the robot does when its PIR motion sensor fires (owner, 2026-11-08: "make a couple motion sensor modes like the
security mode ... look at what/who set it off and say hello").

Modes (config "motion": {"mode": ...}; web app Body > Motion sensor, POST /api/motion, or ask the robot):
  off       count and log only (the feed shows motion at most once a minute)
  wake      the face perks up; the camera is never used
  greet     look with the camera and say hello (cooldown greet_cooldown s). With greet_ai the photo goes to the AI model
            for a one-line greeting about what it sees; without it (or with no AI) only local face detection is used.
            Camera privacy mode is respected: then it says hello without looking. Greeting photos are never saved.
  security  armed arm_delay s after switching on (time to leave). Each detection (at most every 20 s) saves an HD photo to
            data/security/ (owner only: never in Photos, never posted) and logs a "security" event; security_warn says a
            warning out loud. Switching it off tells the owner how many times something moved.
"""
import io
import threading
import time

from .core import DATA

SECURITY = DATA / "security"
MODES = ("off", "wake", "greet", "security")
KEEP_PHOTOS = 300


class MotionModes:
    def __init__(self, robot):
        self.r = robot
        self.busy = threading.Lock()                     # one reaction at a time
        self.armed_at = 0.0
        self.last_react = 0.0
        self.last_greeting = ""
        self.armed_count = 0
        c = self.cfg()
        if c["mode"] == "security":                      # robotd restarted while armed: stay armed, no exit delay
            self.armed_at = time.time()
        robot.bus.on("motion", self.on_motion)

    def cfg(self):
        c = self.r.cfg.data.setdefault("motion", {})
        c.setdefault("mode", "off")
        c.setdefault("greet_ai", True)                   # owner asked for it to look at what/who moved
        c.setdefault("greet_cooldown", 180)
        c.setdefault("security_warn", False)
        c.setdefault("arm_delay", 60)
        return c

    # ------------------------------------------------------------------ switching
    def set(self, mode=None, **opts):
        c = self.cfg()
        for k in ("greet_ai", "security_warn"):
            if k in opts and opts[k] is not None:
                c[k] = bool(opts[k])
        for k in ("greet_cooldown", "arm_delay"):
            if k in opts and opts[k] is not None:
                c[k] = int(max(0, min(3600, int(opts[k]))))
        msg = ""
        if mode is not None:
            mode = str(mode).lower()
            if mode not in MODES:
                raise ValueError(f"mode must be one of {', '.join(MODES)}")
            old = c["mode"]
            c["mode"] = mode
            if mode == "security" and old != "security":
                self.armed_at, self.armed_count = time.time() + c["arm_delay"], 0
                msg = f"Security mode on. I start watching in {c['arm_delay']} seconds."
                self.r.voice.speak(msg, "warning")
            elif old == "security" and mode != "security":
                n = self.armed_count
                msg = ("Security mode off. Nothing moved while I was watching." if not n else
                       f"Security mode off. Something moved {n} time{'s' if n != 1 else ''} while I was watching. "
                       "The photos are in the web app, Body tab.")
                self.r.voice.speak(msg, "happy" if not n else "curious")
            else:
                msg = {"off": "Motion sensor: just counting.", "wake": "Motion sensor: I will perk up when someone comes by.",
                       "greet": "Motion sensor: I will say hello when someone comes by."}.get(mode, "")
                self.r.toast(msg)
            self.r.log(f"motion mode {old} -> {mode}")
            self.r.store.event("motion_mode", f"motion mode: {mode}")      # not in the guest-visible feed (security = away)
        self.r.cfg.save()
        return msg or "saved"

    # ------------------------------------------------------------------ reacting
    def on_motion(self, pir):
        mode = self.cfg()["mode"]
        if mode == "off" or not self.busy.acquire(blocking=False):
            return
        threading.Thread(target=self._react, args=(mode,), daemon=True, name="motion-react").start()

    def _react(self, mode):
        try:
            now = time.time()
            if mode == "wake":
                if now - self.last_react > 30:
                    self.last_react = now
                    self.r.face("surprised", hold=600)
                    time.sleep(0.8)
                    self.r.face("curious", look=[0, -0.2], look_ms=1500, hold=4000)   # hold: never a lasting mood
            elif mode == "greet":
                if now - self.last_react > self.cfg()["greet_cooldown"]:
                    self.last_react = now
                    self.greet()
            elif mode == "security":
                if now >= self.armed_at and now - self.last_react > 20:
                    self.last_react = now
                    self.guard()
        except Exception as e:
            self.r.log(f"motion reaction ({mode}) failed: {e}", "warn")
        finally:
            self.busy.release()

    def greet(self):
        r = self.r
        r.face("curious", look=[0, -0.2], look_ms=1200, hold=4000)
        if r.cam.privacy:
            text = "Hello! Is someone there? My camera is off, so I can only feel you moving."
        else:
            img = r.cam.still(640, 480)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=80)
            faces = 0
            try:
                faces = int((r.vision.process(buf.getvalue(), mode="faces", draw=False)[1] or {}).get("faces", 0))
            except Exception:
                pass
            text = ""
            if self.cfg()["greet_ai"] and r.social._ai_available():
                try:
                    text = (r.social._ai_json(
                        "You are a small, friendly home robot on the floor. Your motion sensor just noticed movement and "
                        "this is your camera's view. Greet whoever or whatever moved in ONE short, warm, playful sentence "
                        "(under 20 words): a person, a pet, or nothing visible. Never guess names. Do not describe "
                        "people's bodies or clothes. You are staying where you are: never say you are coming, rolling "
                        "or moving over. Plain text only.", img) or "").strip().strip('"')[:160]
                except Exception as e:
                    r.log(f"greeting AI failed: {e}", "warn")
            if not text:
                text = ("Hello there! Nice to see you." if faces else
                        "Hmm, something moved, but I cannot see anyone. Hello anyway!")
        self.last_greeting = text
        r.store.event("motion", f"greeted: {text}")
        r.voice.speak(text, "happy")

    def guard(self):
        r = self.r
        self.armed_count += 1
        name = ""
        if not r.cam.privacy:
            try:
                img = r.cam.still()
                SECURITY.mkdir(parents=True, exist_ok=True)
                name = time.strftime("sec_%Y%m%d_%H%M%S.jpg")
                img.convert("RGB").save(SECURITY / name, "JPEG", quality=85)
                old = sorted(SECURITY.glob("sec_*.jpg"))[:-KEEP_PHOTOS]
                for f in old:
                    f.unlink(missing_ok=True)
            except Exception as e:
                r.log(f"security photo failed: {e}", "warn")
        r.store.event("security", "motion while armed" + (f" (photo {name})" if name else " (camera off: no photo)"))
        r.log(f"security: motion while armed #{self.armed_count}" + (f", photo {name}" if name else ""), "warn")
        if self.cfg()["security_warn"]:
            r.voice.speak("Hello? My human is not home right now. I am watching, and I took a picture.", "warning")

    # ------------------------------------------------------------------ reporting
    def photos(self, n=24):
        if not SECURITY.exists():
            return []
        return [f.name for f in sorted(SECURITY.glob("sec_*.jpg"), reverse=True)[:n]]

    def status(self, owner=False):
        c = self.cfg()
        now = time.time()
        st = {"mode": c["mode"], "greet_ai": c["greet_ai"], "security_warn": c["security_warn"],
              "greet_cooldown": c["greet_cooldown"], "arm_delay": c["arm_delay"],
              "armed": c["mode"] == "security" and now >= self.armed_at,
              "armed_in": max(0, round(self.armed_at - now)) if c["mode"] == "security" else 0,
              "seen_while_armed": self.armed_count if c["mode"] == "security" else 0}
        if owner:
            st["last_greeting"] = self.last_greeting
            st["security_photos"] = self.photos()
        return st
