"""robotd: the Orange Pi supervisor. Wires hardware services to the CYD dashboard and keeps memory."""
import os
import random
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

from . import health, tests
from .api import Api
from .camera import Camera, fit_jpeg
from .core import DATA, PHOTOS, ROOT, Bus, Config, RobotLog, Store, ensure_dirs, load_secrets
from .links import CydLink, MotorLink
from .sensors import I2CBus, Imu, Pcf8574
from .voice import Voice
from .brain import Brain
from .journal import Journal
from .agent import Agent
from .social import Social
from .catalog import Catalog
from .bench import Bench
from .vision import Vision
from roboos.desktop import Desktop

from .desktop_host import RobotHost
from .settings import Settings

MEDIA_ROOT = DATA
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
SERVICE = "/etc/systemd/system/robotd.service"


class Robot:
    def __init__(self):
        ensure_dirs()
        load_secrets()
        self.t0 = time.time()
        self.cfg = Config()
        os.environ["TZ"] = self.cfg["tz"]
        time.tzset()
        self.store = Store()
        self.bus = Bus()
        self.log = RobotLog(self.store, self.bus)
        self.sys = health.SysInfo()
        self.i2c = I2CBus(int(self.cfg.get("i2c_bus", 0)))   # the brain board's own I2C bus (Orange Pi 0, Raspberry Pi 1)
        self.pcf = Pcf8574(self.i2c)
        self.imu = Imu(self.i2c, self.cfg, self.bus, self.log)
        from .sensors import Pir
        self.pir = Pir(self.bus, self.log)       # white-dome motion sensor on the Pi's pin 7 (owner, 2026-11-08)
        self.cam = Camera(self.cfg, self.log)
        self.cyd = CydLink(self.bus, self.log)
        self.motor = MotorLink(self.cfg, self.log)
        self.devices_seen = {}
        self.pi_i2c = []                         # addresses found on the Pi's own I2C bus 0 at start (empty since 2026-11-06)
        self.test_lock = threading.Lock()
        self.last_reaction = 0
        self.streams = {}
        self.voice = Voice(self)
        self.journal = Journal(self)
        self.brain = Brain(self)
        self.social = Social(self)
        self.catalog = Catalog(self)
        self.bench = Bench(self)
        self.vision = Vision(self)
        self.api = Api(self)
        self.desktop = Desktop(RobotHost(self))    # RoboOS (roboos/), with the robot as its host
        self.agent = Agent(self)                 # RoboDev: the robot's own coding agent (RoboOS app, web Dev tab, voice, MCP)
        from .battery import Battery
        self.battery = Battery(self)             # INA219 on Pi I2C bus 0: level, warnings, clean shutdown, black box
        self.settings = Settings(self)           # the one settings list (screen, RoboOS and web app all show it)
        from .screencap import ScreenCap
        self.screencap = ScreenCap(self)
        from .drive import Drive
        self.drive = Drive(self)                 # onboard steering + remote control (needs motors_enabled)
        from .wheelie import Wheelie
        self.wheelie = Wheelie(self)             # kick + balance on two wheels; the fast loop is in the motor board
        from .motion_modes import MotionModes
        self.motion_modes = MotionModes(self)    # off / wake / greet / security, reacting to the PIR sensor
        from .blinkers import Blinkers
        self.blinkers = Blinkers(self)           # salvaged BLINKERS LED display on the PCF8574 (manual: blinkers.md)
        from .weather import Weather
        self.weather = Weather(self)             # Open-Meteo weather for the RoboOS Weather app and the BLINKERS panel
        from .reminders import Reminders
        self.reminders = Reminders(self)         # owner reminders as touchscreen prompts (on motion or at a time)
        from .mail import Mail
        self.mail = Mail(self)                   # Pip's own email (AgentMail); every send needs the owner's OK in the web app
        from .wifi_keep import WifiKeep
        self.wifi_keep = WifiKeep(self)          # the home Wi-Fi drops broadcasts: tell the laptop our address by unicast
        self.convo_lock = threading.Lock()
        self._wire()

    # ------------------------------------------------------------------ helpers
    def service_enabled(self):
        return subprocess.run(["systemctl", "is-enabled", "robotd"], capture_output=True, text=True).stdout.strip() == "enabled"

    def face(self, mood=None, say=None, lasting=False, **kw):
        """Show a mood and/or words on the face. A mood is TEMPORARY: it snaps back to the base mood after `hold` ms
        (default: the words' `sec`, else 15 s). lasting=True changes the base mood itself, which only the owner's manual
        pick (web app / API) and shutdown do. Mood "auto" gives the face back to the robot (base mood idle)."""
        m = {"t": "face"}
        if mood == "auto":
            mood, lasting = "idle", True
        if mood:
            m["mood"] = mood
            if lasting:
                self.face_base = mood
            elif not kw.get("hold"):
                kw["hold"] = int(float(kw.get("sec") or 15) * 1000)
        if say:
            m["say"] = say
        m.update(kw)
        self.cyd.send(m)

    def _on_motion(self, pir):
        """Something moved in front of the motion sensor. Logged for the feed at most once a minute."""
        now = time.time()
        if now - getattr(self, "_motion_logged", 0) > 60:
            self._motion_logged = now
            self.store.event("motion", "something moved in front of me (motion sensor)")

    def toast(self, msg, ms=2500):
        self.cyd.send({"t": "toast", "msg": msg, "ms": ms})

    def status_msg(self):
        s = self.sys.status()
        s.update({"t": "status", "robotd_up": int(time.time() - self.t0),
                  "rp2040": ("ok - " + self.motor.fw[5:40]) if self.motor.up else "not answering",
                  "ai": self._ai_line()})
        e = self.i2c_err()
        if e is not None:
            s["i2c_err"] = e
        return s

    def i2c_err(self):
        """Error rate of the I2C bus the sensors are on: the Pi bus if anything was found on it at start, else the
        CYD CN1 bus (where the IMU and PCF8574 live since 2026-11-06). None if there is no sensor bus at all."""
        if self.pi_i2c:
            return round(self.i2c.err_pct(), 1)
        if self.cyd_bus():
            return round(self.imu.cyd_err, 1) if self.imu.source == "cyd" else 0.0
        return None

    def _ai_line(self):
        b = self.brain.summary()
        st = {s["model"]: s for s in self.brain.stats()}.get(b["active"])
        extra = f", {st['calls']} calls, {st['avg_latency_s']}s avg" if st else ""
        return f"{b['label']} ({'ready' if b['online'] else 'no key'}){extra}"

    def safe_media_path(self, rel):
        p = (MEDIA_ROOT / (rel or "")).resolve()
        root = MEDIA_ROOT.resolve()
        if p != root and root not in p.parents:
            raise ValueError("path outside media root")
        return p

    # ------------------------------------------------------------------ CYD message handlers
    def _wire(self):
        b = self.bus
        b.on("cyd_hello", self.on_hello)
        b.on("cyd_sound", self.voice.on_cyd_sound)     # the screen's beeps follow the chosen sound output
        b.on("page", self.on_page)
        b.on("cyd:get", self.on_get)
        b.on("cyd:run_test", lambda m: self.run_test_async(m.get("id")))
        b.on("cyd:test_local", self.on_test_local)
        b.on("cyd:snap", lambda m: threading.Thread(target=self.snap, daemon=True).start())
        b.on("cyd:ls", self.on_ls)
        b.on("cyd:get_img", lambda m: threading.Thread(target=self.on_get_img, args=(m,), daemon=True).start())
        b.on("cyd:touch", self.on_touch)
        b.on("cyd:imu_level", lambda m: self._imu_action(self.imu.level_here, "LEVEL"))
        b.on("cyd:imu_gyro_cal", lambda m: threading.Thread(target=self._imu_action, args=(self.imu.gyro_cal, "GYRO 0"), daemon=True).start())
        b.on("cyd:answer", self.on_answer)
        b.on("cyd:power", self.on_power)
        b.on("cyd:i2c", self.on_cyd_i2c)
        b.on("cyd:sleep", lambda m: self.log("screen put to sleep by owner", persist=False))
        b.on("cyd:listen", lambda m: threading.Thread(target=self.conversation_turn, daemon=True).start())
        b.on("cyd:listen_stop", lambda m: self.voice.stop_listening.set())
        b.on("cyd:read_text", lambda m: threading.Thread(target=self._button_action, args=(self.brain.read_text_action, "Read"), daemon=True).start())
        b.on("cyd:describe", lambda m: threading.Thread(target=self._button_action, args=(self.brain.describe_action, "I see"), daemon=True).start())
        b.on("cyd:pair_request", lambda m: self.api.pairing_qr())
        b.on("cyd:vision_next", lambda m: self.vision.next_mode())
        b.on("cyd:brain_next", lambda m: self.brain.select_next())
        b.on("cyd:dt", lambda m: self.desktop.on_touch(m))
        b.on("cyd:desk_redraw", lambda m: self.desktop.force_full())
        b.on("cyd:open_app", lambda m: self.desktop.open_app(str(m.get("name", ""))))   # menu tiles: Games, Bluetooth
        b.on("cyd:rate", lambda m: (self.brain.rate_last(bool(m.get("good"))), self.toast("Thanks for the feedback!")))
        b.on("brain_changed", lambda: (self.cyd.send(self.brain.summary()), self.toast("AI: " + self.brain.summary()["label"])))
        b.on("log", self.on_log_line)
        b.on("imu", self.on_imu)

    # ------------------------------------------------------------------ talking
    def conversation_turn(self):
        """TALK button: listen, understand, think, answer out loud."""
        if not self.convo_lock.acquire(blocking=False):
            return
        try:
            self.face("curious", "Listening...", sec=8)
            heard = self.voice.listen(max_s=20, silence_s=1.4, wait_s=7,     # long enough for a whole request
                                      on_state=lambda on: self.cyd.send({"t": "listen_state", "on": on}))
            if not heard:
                self.voice.speak("I didn't hear anything. Tap talk and try again.", "sleepy")
                return
            self.face("thinking", f"You said: {heard}", sec=6)
            reply = self.brain.ask(heard, src="voice")
            self.voice.speak(reply, "happy")
        except Exception as e:
            self.log(f"conversation failed: {e}", "warn")
            self.face("sad", "Something went wrong while listening.")
        finally:
            self.cyd.send({"t": "listen_state", "on": False})
            self.convo_lock.release()

    def _button_action(self, fn, title):
        try:
            reply = fn()
        except Exception as e:
            reply = f"That didn't work: {e}"
        self.cyd.send({"t": "ask", "id": "", "title": title, "q": reply[:600], "opts": ["OK"]})
        self.voice.speak(reply)

    def on_hello(self, m):
        self.cyd.send({"t": "time", "hm": time.strftime("%H:%M")})
        self.settings.on_cyd_cfg(m)                    # firmware 0.8+: the screen's own settings ("set")
        self.voice._applied.pop("cyd", None)           # the screen may have restarted: send its volume again
        self.voice.apply_audio()
        self.cyd.send(self.status_msg())
        self.cyd.send(self.brain.summary())
        self.cyd.send({"t": "vision_mode", "mode": self.vision.mode})
        self.battery.on_hello()
        self.face("happy", "Hello! My brain is online.", chime=0)
        self._start_cyd_imu((m.get("i2c") or {}).get("found") or [])
        self.on_page(self.cyd.page)
        threading.Thread(target=self.social.push_stats, daemon=True).start()     # Bluesky numbers for the face corner

    def on_cyd_i2c(self, m):
        info = m.get("i2c") or {}
        self.cyd.info["i2c"] = info
        self.log(f"CYD CN1 I2C scan: {info}")
        self._start_cyd_imu(info.get("found") or [])

    def _start_cyd_imu(self, found):
        if "0x68" in found and self.imu.source != "pi":
            self.cyd.send({"t": "imu_stream", "on": True, "hz": 50})
            self.log("IMU on CYD CN1 port: streaming 50 Hz from the display board")

    def cyd_bus(self):
        return (self.cyd.info.get("i2c") or {}).get("found") or []

    def pcf_read(self):
        """Read the PCF8574 wherever it is attached (CYD CN1 bus preferred, else Pi bus)."""
        if "0x20" in self.cyd_bus() or any(a.startswith("0x2") or a.startswith("0x3") for a in self.cyd_bus() if a != "0x68"):
            r = self.cyd.request({"t": "pcf_read"}, "pcf", 1.5)
            if not r or r.get("err"):
                raise OSError("PCF8574 on CYD did not answer")
            self.pcf.last = r["v"]
            return r["v"]
        return self.pcf.read()

    def on_page(self, name):
        self.log(f"screen page -> {name}", persist=False)
        if name == "camera":
            self.start_stream("camera", self.camera_loop)
        if name == "imu" and self.imu.source == "cyd":
            self.cyd.send({"t": "imu_stream", "on": True, "hz": 50})
        if name == "status":
            self.start_stream("status", self.status_loop)
        if name == "desktop":
            self.start_stream("desktop", self.desktop.run)
        if name == "settings":
            self.start_stream("settings", self.settings.screen_loop)
        if name == "face" and (self.vision.cfg()["follow"] or self.vision.cfg()["gaze_mirror"]):
            self.start_stream("follow", self.vision.follow_loop)

    def on_get(self, m):
        w = m.get("what")
        if w == "devices":
            threading.Thread(target=lambda: self.cyd.send({"t": "devices", "items": health.inventory(self)}), daemon=True).start()
        elif w == "tests":
            self.cyd.send({"t": "tests", "items": tests.tests_list(self)})
        elif w == "ideas":
            self.cyd.send({"t": "ideas", "items": health.ideas(self)})
        elif w == "status":
            self.cyd.send(self.status_msg())
        elif w == "social":
            threading.Thread(target=self.social.push_stats, args=(True,), daemon=True).start()
        elif w == "logs":
            self.cyd.send({"t": "logs", "src": "robot", "lines": list(self.log.lines)[-40:]})
        elif w == "syslog":
            out = subprocess.run(["journalctl", "-n", "40", "--no-pager", "-o", "short"], capture_output=True, text=True).stdout
            lines = [" ".join(l.split()[2:3] + l.split()[4:]) for l in out.splitlines() if l.strip()]
            self.cyd.send({"t": "logs", "src": "system", "lines": lines[-40:]})

    def on_log_line(self, line):
        if self.cyd.page == "logs":
            self.cyd.send({"t": "log", "src": "robot", "line": line})

    def run_test_async(self, tid):
        def go():
            if tid not in tests.TESTS:
                return
            with self.test_lock:
                res = tests.run_test(self, tid)
            self.cyd.send(res)
            self.cyd.send({"t": "tests", "items": tests.tests_list(self)})
        threading.Thread(target=go, daemon=True).start()

    def on_test_local(self, m):
        self.store.test(m.get("id"), bool(m.get("ok")), m.get("sum", ""), source="cyd")
        self.log(f"screen test {m.get('id')} {'PASS' if m.get('ok') else 'FAIL'}: {m.get('sum')}")

    def on_touch(self, m):
        self.store.event("touch", "owner touched the face", x=m.get("x"), y=m.get("y"))
        n = self.store.kv_get("touch_count", 0) + 1
        self.store.kv_set("touch_count", n)
        lines = ["Hi!", "Hee hee, that tickles.", "Hello, friend!", "I see you!", f"That's touch number {n}.", "Boop!"]
        self.face(None, random.choice(lines))

    def _imu_action(self, fn, label):
        ok, msg = fn()
        self.log(f"IMU {label}: {msg}")
        self.toast(("Done: " if ok else "Failed: ") + msg[:44], 3500)
        self.store.test("imu_" + label.lower().replace(" ", "_"), ok, msg)

    def on_answer(self, m):
        aid, choice = m.get("id", ""), m.get("choice", "")
        self.log(f"owner answered {aid}: {choice}")
        if aid == "idea:autostart" and choice == "Approve":
            self.enable_service()
        self.bus.emit("answer", aid, choice)

    def on_power(self, m):
        action = m.get("action")
        if action in ("reboot", "shutdown"):
            self.log(f"owner requested {action} from the touchscreen")
            self.face("sleepy", "Good night!" if action == "shutdown" else "Back in a minute!", lasting=True)
            time.sleep(1.5)
            subprocess.Popen(["systemctl", "poweroff" if action == "shutdown" else "reboot"])

    def enable_service(self):
        unit = f"""[Unit]
Description=Robot supervisor (face, dashboard, sensors, memory)
After=network.target

[Service]
WorkingDirectory={ROOT}
ExecStart={ROOT}/venv/bin/python -m robotd
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
        Path(SERVICE).write_text(unit)
        subprocess.run(["systemctl", "daemon-reload"])
        subprocess.run(["systemctl", "enable", "robotd"])
        self.log("owner approved autostart: robotd.service enabled")
        self.toast("I will start automatically at boot now")

    # ------------------------------------------------------------------ photos / images
    def on_ls(self, m):
        try:
            p = self.safe_media_path(m.get("path", "photos"))
            p.mkdir(parents=True, exist_ok=True) if p == PHOTOS else None
            entries = []
            if p != MEDIA_ROOT.resolve():
                entries.append({"n": "..", "d": 1})
            dirs = sorted([e for e in p.iterdir() if e.is_dir() and not e.name.startswith(".")], key=lambda e: e.name)
            files = sorted([e for e in p.iterdir() if e.is_file() and e.suffix.lower() in IMG_EXT], key=lambda e: e.stat().st_mtime, reverse=True)
            entries += [{"n": d.name, "d": 1} for d in dirs]
            page = int(m.get("page", 0))
            files_page = files[page * 50:(page + 1) * 50]
            for f in files_page:
                st = f.stat()
                entries.append({"n": f.name, "s": st.st_size, "m": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime))})
            rel = str(p.relative_to(MEDIA_ROOT.resolve())) if p != MEDIA_ROOT.resolve() else ""
            self.cyd.send({"t": "files", "path": rel, "items": entries, "total": len(files), "page": page})
        except Exception as e:
            self.cyd.send({"t": "files", "path": m.get("path", ""), "items": [], "err": str(e)})

    def on_get_img(self, m):
        try:
            p = self.safe_media_path(m.get("path"))
            img = Image.open(p)
            img.load()
            w, h = int(m.get("w", 320)), int(m.get("h", 220))
            info = self.cyd.info   # the screen reports its buffer sizes (smaller while Bluetooth is on)
            limit = min(15000, info.get("prev_max", 16384) - 512) if m.get("slot") == "preview" else min(40000, info.get("img_max", 49152) - 1024)
            jpg = fit_jpeg(img, w, h, limit, quality=80)
            self.cyd.send_image(jpg, x=m.get("x", 0), y=m.get("y", 20), w=w, h=h, slot=m.get("slot", "full"), tag=m.get("path"))
        except Exception as e:
            self.toast(f"cannot open: {e}"[:48])

    def snap(self):
        try:
            self.face("surprised", hold=800)
            path, img = self.cam.save_photo(PHOTOS)
            self.log(f"photo saved {path.name} ({img.width}x{img.height})")
            self.toast(f"Saved {path.name}")
        except Exception as e:
            self.log(f"photo failed: {e}", "warn")
            self.toast(f"photo failed: {e}"[:48])

    def show_image(self, img, caption=""):
        limit = min(40000, self.cyd.info.get("img_max", 49152) - 1024)
        return self.cyd.send_image(fit_jpeg(img, 320, 220, limit), x=0, y=20, w=320, h=220, slot="show", caption=caption)

    # ------------------------------------------------------------------ page-bound streams
    def start_stream(self, name, fn):
        th = self.streams.get(name)
        if th and th.is_alive():
            return
        th = threading.Thread(target=fn, daemon=True, name=name)
        self.streams[name] = th
        th.start()

    def camera_loop(self):
        if self.cam.privacy:
            self.toast("camera is OFF (privacy) - turn it on in Desktop > Camera or the web app")
            return
        if not self.cam.present:
            self.toast("no webcam found")
            return
        self.cam.start_live("screen")
        self.log("camera live view started", persist=False)
        q = self.cfg["camera"]["live_quality"]
        last_t = 0
        try:
            while self.cyd.page == "camera" and self.cyd.up:
                frame, last_t = self.cam.next_frame(last_t, timeout=1.5)
                if not frame:
                    continue
                try:
                    if self.vision.mode != "off":       # live OpenCV demo: annotated frames
                        ann, _ = self.vision.process(frame)
                    else:                                # plain view: high-quality downscale of the 640x480 stream
                        import cv2
                        import numpy as np
                        ann = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_REDUCED_COLOR_2)
                    jpg = self.vision.encode(ann, quality=q) if ann is not None else None
                    if not jpg:
                        continue
                    cap = self.cyd.info.get("img_max", 49152) - 1024
                    if len(jpg) > cap:                  # small screen buffer (Bluetooth on): drop quality for next frames
                        q = max(25, q - 10)
                        continue
                except Exception:
                    continue
                self.cyd.send_image(jpg, timeout=1.5, x=0, y=20, w=320, h=220, slot="cam")
        finally:
            self.cam.stop_live("screen")
            self.log("camera live view stopped", persist=False)

    def status_loop(self):
        while self.cyd.page == "status" and self.cyd.up:
            self.cyd.send(self.status_msg())
            time.sleep(1.5)

    # ------------------------------------------------------------------ IMU-driven behaviour
    def on_imu(self, imu):
        now = time.time()
        if self.cyd.page == "imu" and now - getattr(self, "_imu_tx", 0) > 0.04:
            self._imu_tx = now
            m = imu.snapshot()
            m["t"] = "imu"
            self.cyd.send(m)
        if not self.cfg["reactions"] or now - self.last_reaction < 6:
            return
        mag = sum(v * v for v in imu.acc) ** 0.5
        tilt = max(abs(imu.pitch), abs(imu.roll))
        if abs(mag - 1) > 0.7:
            self.last_reaction = now
            self.face("surprised", random.choice(["Whoa!", "Bump!", "Ooh, what was that?"]), hold=2500)
            self.store.event("bump", f"felt a bump of {abs(mag - 1):.2f} g")
        elif tilt > 35 and self.cfg["imu"]["leveled"]:
            self.last_reaction = now
            self.face("warning", f"I'm tilted {tilt:.0f} degrees!", hold=3000)
            self.store.event("tilt", f"tilted {tilt:.0f} deg")
        elif self.cyd.page == "face" and tilt > 8 and self.cfg["imu"]["leveled"] and now - getattr(self, "_look_tx", 0) > 0.3:
            self._look_tx = now
            self.cyd.send({"t": "face", "look": [max(-1, min(1, imu.roll / 30)), max(-1, min(1, -imu.pitch / 30))], "look_ms": 600})

    # ------------------------------------------------------------------ background housekeeping
    def housekeeping(self):
        n = 0
        while True:
            time.sleep(5)
            n += 1
            if n % 3 == 0:
                self.cyd.send({"t": "time", "hm": time.strftime("%H:%M")})
            if n % 6 == 1:
                try:
                    v = self.pcf_read()
                    self.devices_seen[0x20] = True
                    if getattr(self, "_pcf_last", None) not in (None, v):
                        self.log(f"PCF8574 inputs changed -> {v:08b}")
                    self._pcf_last = v
                except OSError:
                    self.devices_seen[0x20] = False
            if n == 12:   # healthy for a minute: remember this code version as known-good (used by safe rollback)
                try:
                    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
                    (DATA / "last_good_commit").write_text(head)
                except Exception:
                    pass
            if n % 12 == 0:
                threading.Thread(target=self.social.tick, daemon=True).start()
                threading.Thread(target=self.brain.maybe_sync_pack, daemon=True).start()   # every 6 h
            lt = time.localtime()
            if lt.tm_hour == 19 and lt.tm_min < 1 and getattr(self, "_diary_day", None) != lt.tm_yday and self.cfg.get("social", {}).get("daily_diary", True):
                self._diary_day = lt.tm_yday
                threading.Thread(target=self.social.daily_diary, daemon=True).start()
            if n % 120 == 0:
                st = self.sys.status()
                self.store.event("health", "periodic", temp=st["temp"], mem=st["mem_used"], i2c_err=self.i2c_err())

    def start(self):
        self.log(f"robotd starting (pid {os.getpid()})")
        try:
            self.pi_i2c = self.i2c.scan()
            self.devices_seen = {a: True for a in self.pi_i2c}
            self.log(f"I2C bus {self.i2c.num}: " + (" ".join(f"0x{a:02x}" for a in self.pi_i2c)
                     or (f"not available ({self.i2c.err})" if self.i2c.err else "nothing found (sensors are on the CYD CN1 port)")))
        except Exception as e:
            self.log(f"I2C scan failed: {e}", "warn")
        for th in (self.cyd, self.motor, self.imu, self.pir):
            th.start()
        self.battery.start()
        self.bus.on("motion", self._on_motion)
        threading.Thread(target=self.housekeeping, daemon=True).start()
        self.wifi_keep.start()
        self.mail.start()
        self.api.start()
        def _report():
            time.sleep(45)
            self.log("AI brain: " + (self.brain.summary()["label"] + " online" if self.brain.online else "offline - add a key to data/secrets.env"))
            self.cyd.send(self.brain.summary())
        threading.Thread(target=_report, daemon=True).start()
        while True:
            time.sleep(3600)


def main():
    Robot().start()
