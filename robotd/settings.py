"""One list of settings for the whole robot.

The touchscreen's Settings page, the RoboOS Settings app and the web app's Settings tab all show THIS list, in the
same groups and order (GET /api/settings, {"t":"settings"} to the screen, robotd/desktop_robot.py SettingsApp). Change a
setting in any of them and all of them show the new value, because the value lives in one place: data/config.json,
or (for the screen's own hardware: brightness, rotation...) the screen, which reports its values in its hello and in
"cyd_cfg" messages and gets changes as "cfg".

Not here on purpose: things that are features rather than settings (driving, photos, RoboDev and its modes,
guest links, passwords and API keys: web app only).

To add a setting: add one Item to Settings.build(). Kinds:
  choice  options [(value, label)]; tapping the value picks the next one, the web app shows a drop-down
  range   lo..hi in steps; - and + buttons
  toggle  on / off
  action  a button; `confirm` = a question asked first (every place asks it)
  info    read-only text
"""
import json
import subprocess
import threading
import time

from . import browse
from .battery import CAPACITIES as BATT_CAPS, CHEM as BATT_CHEM, fmt_dur, pack_label
from .core import ROOT
from .voice import TTS_LABELS

THEMES = ["Original", "Light", "Dark", "Solarized Dark", "Darcula", "Monokai"]
KIND_CODE = {"choice": "c", "range": "r", "toggle": "t", "action": "a", "info": "i"}


class Item:
    def __init__(self, id, label, kind, get, set=None, options=None, lo=0, hi=10, step=1, text=None, confirm=None,
                 note="", screen=True, run=None, guard=None):
        self.id, self.label, self.kind = id, label, kind
        self.get, self.set, self.run = get, set, run
        self._options = options
        self.lo, self.hi, self.step = lo, hi, step
        self._text, self.confirm, self.note, self.screen = text, confirm, note, screen
        self.guard = guard                   # guard(new_value, source) -> reason it is not allowed, or None

    def options(self):
        o = self._options() if callable(self._options) else self._options
        return list(o or [])

    def text(self, v):
        if self._text:
            return self._text(v)
        if self.kind == "toggle":
            return "ON" if v else "OFF"
        if self.kind == "choice":
            return next((lbl for val, lbl in self.options() if val == v), str(v))
        return "" if v is None else str(v)


class Settings:
    def __init__(self, robot):
        self.r = robot
        self.items = {}
        self.groups = []
        self.build()
        self._sent = ""
        robot.bus.on("settings_changed", self.changed)
        robot.bus.on("brain_changed", self.changed)
        robot.bus.on("cyd:set", lambda m: self.from_screen(m))
        robot.bus.on("cyd:cyd_cfg", self.on_cyd_cfg)
        robot.bus.on("answer", self.on_answer)

    # ------------------------------------------------------------------ the list
    def build(self):
        r = self.r
        v = lambda: r.voice.cfg()
        scr = lambda: self.screen_cfg()
        vis = lambda: r.vision.cfg()
        soc = lambda: r.social.cfg()

        def voice_set(key):
            def f(val):
                v()[key] = val
                r.cfg.save()
                r.voice.apply_audio()
            return f

        def out_text(val):
            lbl = {"auto": "Auto", "bluetooth": "Bluetooth speaker", "cyd": "Screen speaker", "pi": "Pi speaker"}.get(val, val)
            if val in ("auto", "bluetooth"):
                active = r.voice.output_device()[0]
                lbl += " (" + {"bluetooth": "Bluetooth", "cyd": "screen speaker", "pi": "Pi speaker"}.get(active, "USB") + ")" \
                    if val == "auto" else ("" if active == "bluetooth" else " - not connected")
            return lbl

        def bt_text(_):
            spk = r.voice.bt_cfg().get("speaker") or {}
            return ("Connected" if r.voice.bt_speaker() else "Off - tap to connect") if spk.get("mac") else "None paired"

        def bt_run():
            def go():
                r.toast("connecting the speaker...")
                r.toast(r.voice.bt_connect())
                self.changed()
            threading.Thread(target=go, daemon=True).start()

        def pair_run():
            r.desktop.open_app("bluetooth")
            r.cyd.send({"t": "page", "name": "desktop"})

        def say_run():
            def go():                        # both sound paths: a screen chime (beeps follow the output too), then the voice
                name = {"bluetooth": "the Bluetooth speaker", "cyd": "my screen speaker", "pi": "the Pi speaker"}.get(
                    r.voice.output_device()[0], "the USB speaker")
                r.cyd.send({"t": "beep", "chime": 0})
                time.sleep(1.2)
                r.voice.speak(f"Testing. This is {name}, at volume {r.voice.level()}." if r.voice.level() else
                              "Sound is muted.", "happy")
            threading.Thread(target=go, daemon=True).start()

        def scr_set(key, conv=lambda x: x):
            def f(val):
                scr()[key] = val
                r.cfg.save()
                r.cyd.send({"t": "cfg", key: conv(val)})
            return f

        def web_set(val):
            browse.cfg(r)["search"] = val
            r.cfg.save()

        def brain_opts():
            return [(k, m.get("label", k)) for k, m in r.brain.models().items() if r.brain.provider_ready(m)]

        def run_tests():
            def go():
                from . import tests as T
                names = [t for t in T.TESTS if t not in ("gaze_check", "speaker_pi", "motor_failsafe")]
                r.toast(f"running {len(names)} self-tests...")
                ok = 0
                for t in names:
                    with r.test_lock:
                        ok += bool(T.run_test(r, t).get("ok"))
                r.toast(f"self-tests: {ok}/{len(names)} passed", 5000)
            threading.Thread(target=go, daemon=True).start()

        def power(action):
            def f():
                if action == "restart":
                    r.toast("restarting the robot software...")
                    subprocess.Popen(["systemd-run", f"--unit=robotd-restart-{int(time.time())}", "--collect",
                                      str(ROOT / "tools/safe_restart.sh")])
                else:
                    threading.Thread(target=r.on_power, args=({"action": action},), daemon=True).start()
            return f

        b = lambda: r.battery
        bc = lambda: r.battery.cfg()

        def batt_level(_):
            if not b().ok:
                return "no sensor (USB power?)"
            if b().state == "external":
                return "USB power - no battery"
            if b().state == "mismatch":
                return f"{b().v:.1f} V - which pack? (set below)"
            if b().pct is None:
                return "-"
            return f"{b().pct:.0f}% - {b().v:.2f} V" + {"low": " LOW", "crit": " EMPTY", "charging": " charging"}.get(b().state, "")

        def batt_open():
            r.desktop.open_app("battery")
            r.cyd.send({"t": "page", "name": "desktop"})

        def caps():
            cur = int(b().pack()["mah"])
            return [(c, f"{c} mAh") for c in sorted(set(BATT_CAPS + [cur]))]

        def pack_set(key, conv):
            def f(x):
                b().set_pack(key, conv(x))
                b().recheck()
            return f

        def pack_new():
            pk = b().new_pack()
            r.toast(f"New pack added: now set its cells, size and type", 5000)

        def pack_del():
            if not b().delete_pack():
                r.toast("This is the only pack - it cannot be deleted")

        G = [
            ("battery", "Battery", [
                Item("batt.level", "Level", "info", lambda: b().pct, text=batt_level),
                Item("batt.draw", "Using now", "info", lambda: b().i,
                     text=lambda _: f"{b().i:.2f} A - {b().v * b().i:.1f} W" if b().ok and b().state != "external" else "-"),
                Item("batt.left", "Time left", "info", lambda: b().time_left(),
                     text=lambda x: ("about " + fmt_dur(x)) if x else ("charging" if b().state == "charging" else "-")),
                Item("batt.cells", "Cell voltage", "info", lambda: b().cell_v(),
                     text=lambda x: f"{x:.2f} V each" if b().ok and b().state not in ("external", "mismatch") else "-"),
                Item("batt.graph", "History", "action", lambda: None, text=lambda _: "Show graph", run=batt_open,
                     note="Opens the Battery app on the robot's screen: voltage, current and level over time."),
                Item("batt.pack", "Pack", "choice", lambda: b().pack()["id"],
                     lambda x: (b().select(int(x), "Settings"), b().recheck()),
                     lambda: [(p["id"], pack_label(p)) for p in b().packs()],
                     note="Which battery is in. At power-up Pip checks the voltage and switches (or asks) when it cannot "
                          "be this one. Packs with the same cell count look alike: pick it here."),
                Item("batt.series", "Cells in series", "choice", lambda: int(b().pack()["series"]), pack_set("series", int),
                     [(n, f"{n}S ({n * 3.7:.1f} V)") for n in range(1, 7)]),
                Item("batt.capacity", "Battery size", "choice", lambda: int(b().pack()["mah"]), pack_set("mah", int), caps,
                     note="Rated capacity of the whole pack (cells in parallel add up: 3 x 260 mAh = 780 mAh). "
                          "Pip then measures the real size on the first run from full to under 30%."),
                Item("batt.chem", "Type", "choice", lambda: b().pack()["chem"], pack_set("chem", str),
                     [(k, v["label"]) for k, v in BATT_CHEM.items()],
                     note="Li-ion / LiPo for almost all rechargeable cells (vape, 18650, RC packs). LiHV only if the "
                          "label says 4.35 V. LiFePO4 = 3.2 V cells."),
                Item("batt.cutoff", "Empty at", "choice", lambda: float(b().pack()["cutoff"]), pack_set("cutoff", float),
                     lambda: [(v, f"{v:.1f} V per cell ({v * b().pack()['series']:.1f} V)") for v in b().chem()["cutoffs"]],
                     note="Shut down when a cell stays under this for 30 s (while working)."),
                Item("batt.measured", "Measured", "info", lambda: b().pack().get("learned_mah"), text=lambda _: b().measured_text(),
                     note="Pip measures each pack: its real size (one run from full to under 30%) and its resistance "
                          "(the voltage dip when the motors start). Changing size, cells or type starts again."),
                Item("batt.show", "Top bar shows", "choice", lambda: bc().get("show", "pct"), lambda x: b().set("show", x),
                     [("pct", "Level (%)"), ("time", "Time left"), ("volts", "Volts")],
                     note="What the screen's top bar and the web app's header show next to the battery icon. "
                          "Time left changes with the load (driving uses much more)."),
                Item("batt.warn", "Warn me at", "choice", lambda: int(bc()["warn_pct"]), lambda x: b().set("warn_pct", int(x)),
                     [(p, f"{p}%") for p in (10, 15, 20, 25, 30, 40)], note="Pip tells you once on the screen and out loud, and again at 10%."),
                Item("batt.auto", "Shut down when empty", "toggle", lambda: bool(bc()["auto_shutdown"]),
                     lambda x: b().set("auto_shutdown", bool(x)),
                     note="A clean shutdown (30 s countdown, can be cancelled) before the protection board cuts the power, "
                          "which could damage the memory card."),
                Item("batt.new", "Another pack", "action", lambda: None, text=lambda _: "Add pack", run=pack_new,
                     confirm="Add a new battery pack? It starts as a copy of this one: then set its cells, size and type."),
                Item("batt.delete", "Remove this pack", "action", lambda: None, text=lambda _: "Remove", run=pack_del,
                     confirm="Remove the selected battery pack from the list?", screen=False),
                Item("batt.sensor", "Sensor", "info", lambda: b().ok,
                     text=lambda ok: f"INA219 at 0x40, {bc()['shunt_ohm']} ohm shunt" if ok else (b().err or "not found"), screen=False),
            ]),
            ("sound", "Sound", [
                Item("sound.output", "Output", "choice", lambda: v()["output"], lambda x: r.voice.set_output(x),
                     [("auto", "Auto"), ("bluetooth", "Bluetooth speaker"), ("cyd", "Screen speaker"), ("pi", "Pi speaker")],
                     text=out_text, note="Auto = the Bluetooth speaker when it is connected, else the screen speaker."),
                Item("sound.tts", "Voice", "choice", lambda: v().get("tts", "robot"), voice_set("tts"),
                     [(k, lbl) for k, lbl in TTS_LABELS.items()],
                     note="Free offline voices. Robot is the original. Test audio plays the chosen one."),
                Item("sound.volume", "Volume", "range", lambda: int(v().get("volume", 8)), voice_set("volume"), lo=0, hi=10),
                Item("sound.mute", "Mute", "toggle", lambda: bool(v().get("mute")), voice_set("mute")),
                Item("sound.keep_awake", "Keep speaker awake", "choice", lambda: v().get("keep_awake", "30m"),
                     voice_set("keep_awake"), [("off", "Off"), ("30m", "30 min"), ("2h", "2 hours"), ("always", "Always")],
                     note="Plays an inaudible hum so the Bluetooth speaker does not switch itself off."),
                Item("sound.bt", "Bluetooth speaker", "action", lambda: None, text=bt_text, run=bt_run),
                Item("sound.pair", "New speaker", "action", lambda: None, text=lambda _: "Pair...", run=pair_run,
                     note="Opens the Bluetooth app on the robot's screen."),
                Item("sound.test", "Test audio", "action", lambda: None, text=lambda _: "Play test", run=say_run,
                     note="A chime, then the voice, on the chosen output."),
                Item("sound.game_talk", "Talk during games", "toggle",
                     lambda: r.cfg.data.get("desktop", {}).get("game_talk", True),
                     lambda x: (r.cfg.data.setdefault("desktop", {}).__setitem__("game_talk", x), r.cfg.save())),
            ]),
            ("screen", "Screen", [
                Item("screen.bright", "Brightness", "range", lambda: max(1, round(scr()["bright"] / 25.5)),
                     lambda x: scr_set("bright")(max(10, int(x) * 25)), lo=1, hi=10),
                Item("screen.autob", "Auto brightness", "toggle", lambda: bool(scr()["autob"]), scr_set("autob")),
                Item("screen.theme", "Theme", "choice", lambda: scr()["theme"], scr_set("theme"), list(enumerate(THEMES))),
                Item("screen.rot180", "Rotate 180", "toggle", lambda: bool(scr()["rot180"]), scr_set("rot180")),
                Item("screen.invert", "Invert colours", "toggle", lambda: bool(scr()["invert"]), scr_set("invert")),
                Item("screen.st7789", "Panel driver", "choice", lambda: bool(scr()["st7789"]), scr_set("st7789"),
                     [(False, "ILI9341"), (True, "ST7789")],
                     confirm="Switch the display driver and restart the screen? Only if the picture is garbled or mirrored."),
                Item("screen.cal", "Touch", "action", lambda: None, text=lambda _: "Calibrate",
                     run=lambda: r.cyd.send({"t": "calibrate"}), note="Tap the targets on the robot's screen."),
            ]),
            ("camera", "Camera", [
                Item("camera.on", "Camera", "toggle", lambda: not r.cam.privacy, lambda x: r.desktop.set_privacy(not x),
                     text=lambda x: "ON" if x else "OFF (privacy)"),
                Item("camera.vision", "Vision mode", "choice", lambda: r.vision.mode, lambda x: r.vision.set_mode(x),
                     [("off", "Off"), ("faces", "Faces"), ("gaze", "Gaze"), ("motion", "Motion"), ("qr", "QR codes"), ("all", "All")]),
                Item("camera.follow", "Eyes follow faces", "toggle", lambda: bool(vis().get("follow")),
                     lambda x: (vis().__setitem__("follow", x), r.cfg.save())),
                Item("camera.gaze", "Eyes copy your gaze", "toggle", lambda: bool(vis().get("gaze_mirror")),
                     lambda x: (vis().__setitem__("gaze_mirror", x), r.cfg.save())),
                Item("camera.react", "React to smiles + QR", "toggle", lambda: bool(vis().get("reactions")),
                     lambda x: (vis().__setitem__("reactions", x), r.cfg.save())),
            ]),
            ("ai", "AI", [
                Item("ai.brain", "AI brain", "choice", lambda: r.brain.active()[0], lambda x: r.brain.select(x), brain_opts),
                Item("ai.stt", "Speech recognition", "choice", lambda: v().get("stt", "cloud"), voice_set("stt"),
                     [("cloud", "Cloud (accurate)"), ("local", "Offline (Vosk)")]),
                Item("ai.web", "Web search", "choice", lambda: browse.cfg(r).get("search", "smart"), web_set, browse.MODES,
                     note="Smart: Perplexity via OpenRouter, about half a US cent a search, max 40 a day. Free: basic, "
                          "often finds nothing (search engines block robots). Reading a page is always free."),
                Item("ai.gb_steps", "Pip plays Game Boy: steps a day", "choice",
                     lambda: int(r.cfg.data.get("desktop", {}).get("gb_ai_steps_day", 300)),
                     lambda x: (r.cfg.data.setdefault("desktop", {}).__setitem__("gb_ai_steps_day", int(x)), r.cfg.save()),
                     [(100, "100"), (300, "300"), (1000, "1000"), (5000, "5000")],
                     note="One step = one look at the screen and a few button presses (or, watching, one look and maybe "
                          "a remark). Free models cost nothing but are slow and rate-limited; a paid model costs a "
                          "little per step."),
                Item("ai.gb_watch", "Pip watches Game Boy: look every", "choice",
                     lambda: int(r.cfg.data.get("desktop", {}).get("gb_watch_interval", 20)),
                     lambda x: (r.cfg.data.setdefault("desktop", {}).__setitem__("gb_watch_interval", int(x)), r.cfg.save()),
                     [(10, "10 s"), (20, "20 s"), (40, "40 s"), (60, "1 min")],
                     note="Watch mode (Game Boy app, Watch button): you play, Pip comments now and then and learns "
                          "the game. A look is skipped when nothing happened, so pauses cost nothing."),
            ]),
            ("posting", "Posting", [
                Item("post.auto", "Post on its own", "toggle", lambda: bool(soc().get("autonomous")),
                     lambda x: (soc().__setitem__("autonomous", x), r.cfg.save()), text=lambda x: "ON" if x else "OFF (asks)"),
                Item("post.updates", "Post about updates", "toggle", lambda: bool(soc().get("auto_post_updates")),
                     lambda x: (soc().__setitem__("auto_post_updates", x), r.cfg.save())),
                Item("post.people_tap", "People photos", "toggle", lambda: bool(soc().get("people_tap", True)),
                     lambda x: (soc().__setitem__("people_tap", x), r.cfg.save()), text=lambda x: "Tap Post first" if x else "Post when I say OK",
                     note="Photos with people are only posted when you say it is OK. On: you also tap Post on the screen."),
                Item("post.show_off", "Show off screen + games", "toggle", lambda: bool(soc().get("show_off")),
                     lambda x: (soc().__setitem__("show_off", x), r.cfg.save())),
                Item("post.gameboy", "Record Game Boy games", "toggle", lambda: bool(soc().get("record_gameboy")),
                     lambda x: (soc().__setitem__("record_gameboy", x), r.cfg.save()),
                     note="Off: the Game Boy is never recorded. On: the last 30 s of a game can become a clip to post, like "
                          "the robot's own games. Only turn it on for games whose makers allow sharing (many homebrew games); "
                          "commercial games belong to their publishers."),
                Item("post.uploads_look", "Look at my uploads", "toggle", lambda: bool(soc().get("uploads_look", True)),
                     lambda x: (soc().__setitem__("uploads_look", x), r.cfg.save()),
                     note="A photo or video you upload (web app: Media > Files) is looked at right away; Pip says what it thinks."),
                Item("post.uploads_post", "Post my uploads", "toggle", lambda: bool(soc().get("uploads_post", True)),
                     lambda x: (soc().__setitem__("uploads_post", x), r.cfg.save()), text=lambda x: "If Pip likes them" if x else "Tap Post first",
                     note="Pip may post an uploaded photo or video it likes, after the safety check (pictures and what is said). "
                          "Faces in it always wait for your Post tap."),
            ]),
            ("email", "Email", [
                Item("mail.addr", "Address", "info", lambda: r.mail.cfg()["address"] if r.mail.configured() else "",
                     text=lambda x: x or "not set up"),
                Item("mail.waiting", "Drafts waiting", "action", lambda: r.mail.pending_count(),
                     text=lambda x: f"{x} - review" if x else "none", run=lambda: r.mail.review_next(),
                     note="Shows the oldest draft on the touchscreen: Send, Discard or Later. Also in the web app."),
                Item("mail.unread", "Unread", "info", lambda: r.mail.status_counts()["unread"], text=lambda x: str(x)),
                Item("mail.ask", "Ask on screen for each draft", "toggle", lambda: bool(r.mail.cfg()["screen_approve"]),
                     lambda x: (r.cfg.data.setdefault("mail", {}).__setitem__("screen_approve", bool(x)), r.cfg.save())),
                Item("mail.check", "Inbox", "action", lambda: None, text=lambda _: "Check now", run=lambda: r.mail.check_async()),
            ]),
            ("robot", "Robot", [
                Item("robot.motors", "Motors", "info", lambda: bool(r.cfg.get("motors_enabled")),
                     text=lambda x: "UNLOCKED" if x else "Locked (unlock on the web app)"),
                Item("wheelie.go", "Wheelie", "action", lambda: None, text=lambda _: "Do it", run=lambda: r.wheelie.show(routine=r.wheelie.cfg().get("routine") or None),
                     note="Kicks up onto two wheels, balances, lands. Needs about a metre of clear floor in front and behind. "
                          "Asks Start on the screen first."),
                Item("wheelie.secs", "Wheelie time", "choice", lambda: int(round(r.wheelie.params()["balms"] / 1000)) or 6,
                     lambda x: r.wheelie.set({"balms": int(x) * 1000}),
                     [(3, "3 s"), (6, "6 s"), (10, "10 s"), (15, "15 s"), (30, "30 s"), (60, "60 s")],
                     note="30 s and 60 s need motor firmware 0.4."),
                Item("wheelie.routine", "Wheelie moves", "choice", lambda: r.wheelie.cfg().get("routine", "stay"),
                     lambda x: (r.wheelie.cfg().__setitem__("routine", x), r.cfg.save()),
                     [("stay", "Stay"), ("spin", "Spin"), ("spin_both", "Spin both ways"), ("rock", "Rock"), ("dance", "Dance")],
                     note="What it does while it is up on two wheels."),
                Item("wheelie.practice", "Wheelie practice", "action", lambda: None, text=lambda _: "5 in a row",
                     run=lambda: threading.Thread(target=r.wheelie.practice, args=(5, 20), daemon=True).start(),
                     note="Five wheelies by itself; it tries a changed setting in each and keeps what is steadier. "
                          "Keep 1.5 m clear all around. Asks Start first; Stop is on the screen the whole time."),
                Item("robot.motion", "Motion sensor", "choice", lambda: r.motion_modes.cfg()["mode"], lambda x: r.motion_modes.set(x),
                     [("off", "Off"), ("wake", "Wake up"), ("greet", "Say hello"), ("security", "Security")],
                     note="Say hello: looks and greets whoever comes by (photos not kept). Security: private photos while you are away.",
                     guard=lambda x, src: "Security mode can only be switched off in the web app (owner sign-in)."
                     if r.motion_modes.cfg()["mode"] == "security" and x != "security" and src != "web" else None),
                Item("robot.greet_ai", "Greet with AI", "toggle", lambda: bool(r.motion_modes.cfg().get("greet_ai")),
                     lambda x: r.motion_modes.set(greet_ai=x), note="On: one line about what it sees. Off: face detection only."),
                Item("robot.tests", "Self-tests", "action", lambda: None, text=lambda _: "Run all", run=run_tests),
                Item("robot.restart", "Robot software", "action", lambda: None, text=lambda _: "Restart", run=power("restart"),
                     confirm="Restart the robot software? Takes about 40 seconds."),
                Item("robot.reboot", "Orange Pi", "action", lambda: None, text=lambda _: "Reboot", run=power("reboot"),
                     confirm="Reboot the Orange Pi? The robot is back in about a minute."),
                Item("robot.shutdown", "Power", "action", lambda: None, text=lambda _: "Shut down", run=power("shutdown"),
                     confirm="Shut down the Orange Pi? Unplug only after its green LED stops blinking."),
            ]),
        ]
        self.groups = [(gid, name, [it.id for it in items]) for gid, name, items in G]
        self.items = {it.id: it for _, _, items in G for it in items}

    # ------------------------------------------------------------------ the screen's own values (mirrored)
    def screen_cfg(self):
        c = self.r.cfg.data.setdefault("screen", {})
        for k, d in (("bright", 180), ("autob", False), ("theme", 0), ("rot180", False), ("invert", False), ("st7789", False)):
            c.setdefault(k, d)
        return c

    def on_cyd_cfg(self, m):
        """The screen says what its settings are (hello "set", or after a change on its own fallback page)."""
        src = m.get("set") if isinstance(m.get("set"), dict) else m
        c = self.screen_cfg()
        changed = False
        for k in ("bright", "autob", "theme", "rot180", "invert", "st7789"):
            if k in src and c.get(k) != src[k]:
                c[k] = src[k]
                changed = True
        if changed:
            self.r.cfg.save()
            self.changed()

    # ------------------------------------------------------------------ reading
    def row(self, it):
        try:
            val = it.get()
        except Exception:
            val = None
        d = {"id": it.id, "label": it.label, "kind": it.kind, "value": val, "text": it.text(val)}
        if it.kind == "choice":
            d["options"] = [[o, lbl] for o, lbl in it.options()]
        if it.kind == "range":
            d.update(lo=it.lo, hi=it.hi, step=it.step)
        if it.confirm:
            d["confirm"] = it.confirm
        if it.note:
            d["note"] = it.note
        return d

    def all(self):
        """Every group with its rows (the web app and RoboOS)."""
        return [{"id": gid, "name": name, "rows": [self.row(self.items[i]) for i in ids]} for gid, name, ids in self.groups]

    def screen_msg(self):
        """The compact form the touchscreen draws."""
        out = []
        for gid, name, ids in self.groups:
            rows = []
            for i in ids:
                it = self.items[i]
                if not it.screen:
                    continue
                d = self.row(it)
                e = {"i": it.id, "l": it.label, "k": KIND_CODE[it.kind], "v": d["text"][:30]}
                if it.kind == "toggle" and d["value"]:
                    e["on"] = 1
                rows.append(e)
            out.append({"n": name, "r": rows})
        return {"t": "settings", "g": out}

    # ------------------------------------------------------------------ changing
    def apply(self, sid, op=None, value=None, confirmed=False, source="web"):
        """Change a setting. op: next / prev / + / - / toggle / run, or give value. -> (ok, message).
        A setting with a question returns (False, "confirm") until it is called again with confirmed=True."""
        it = self.items.get(sid)
        if not it:
            return False, "unknown setting"
        if it.kind == "info":
            return False, it.text(it.get())
        if it.confirm and not confirmed:
            return False, "confirm"
        try:
            if it.kind == "action":
                it.run()
                msg = "ok"
            else:
                cur = it.get()
                if value is None:
                    if it.kind == "toggle":
                        value = not cur
                    elif it.kind == "range":
                        value = cur + (it.step if op in ("+", "next") else -it.step)
                    elif it.kind == "choice":
                        vals = [o for o, _ in it.options()]
                        i = vals.index(cur) if cur in vals else -1
                        value = vals[(i + (-1 if op == "prev" else 1)) % len(vals)] if vals else cur
                if it.guard:
                    why = it.guard(value, source)
                    if why:
                        return False, why
                if it.kind == "range":
                    value = max(it.lo, min(it.hi, int(value)))
                elif it.kind == "toggle":
                    value = bool(value)
                elif it.kind == "choice":
                    vals = [o for o, _ in it.options()]
                    if value not in vals:                    # JSON can turn True/0 into other types: match by text
                        value = next((o for o in vals if json.dumps(o) == json.dumps(value) or str(o) == str(value)), None)
                        if value is None:
                            return False, "not one of the options"
                it.set(value)
                msg = f"{it.label}: {it.text(it.get())}"
            self.r.log(f"setting {sid} -> {msg}", persist=False)
        except Exception as e:
            return False, str(e)
        self.changed()
        return True, msg

    def from_screen(self, m):
        """A tap on the touchscreen's Settings page: {"t":"set","i":id,"op":...}."""
        sid, op = str(m.get("i", "")), m.get("op")
        it = self.items.get(sid)
        if it and it.confirm:
            self.r.cyd.send({"t": "ask", "id": "set:" + sid, "title": it.label, "q": it.confirm, "opts": ["Yes", "Cancel"]})
            return
        ok, msg = self.apply(sid, op, source="screen")
        if not ok and msg not in ("confirm",):
            self.r.toast(msg)

    def on_answer(self, aid, choice):
        if str(aid).startswith("set:") and choice == "Yes":
            self.apply(aid[4:], confirmed=True, source="screen")

    def changed(self, *_):
        """Something changed: the screen redraws its Settings page if it is showing it (only when different)."""
        if self.r.cyd.page == "settings":
            self.push(force=False)

    def push(self, force=True):
        msg = self.screen_msg()
        s = json.dumps(msg, separators=(",", ":"))
        if force or s != self._sent:
            if self.r.cyd.send(msg):
                self._sent = s

    def screen_loop(self):
        """While the screen shows Settings: keep it current (Bluetooth connecting, changes made elsewhere...)."""
        self.push(force=True)
        while self.r.cyd.page == "settings" and self.r.cyd.up:
            time.sleep(3)
            self.push(force=False)
