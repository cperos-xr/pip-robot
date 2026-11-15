"""RoboOS app: control the salvaged BLINKERS LED display (robotd/blinkers.py, manual blinkers.md).

A picture of the display at the top that looks like the real one (robotd/blinkers_picture.py): lit parts glow in their
group's colour, picked ones are cyan. Which LED lights which part of the face and the letters comes from the camera test.
Tap a part to pick its LED, then Show. Buttons:
  Show, Clear, Off, Test (the camera test: finds the display, learns how it is wired, maps every LED; about 1 minute)
  Full (battery), 777, Batt -/+ (battery bars in 25 % steps)
  Shimmer (the head twinkles), Scan (every cathode in turn), Chase (the three digits), Pulse (battery blinks)."""

from roboos.desktop import App, DARK, F_BOLD, F_UI, FACE, GREEN, NAVY, RED, SHADOW, _font, bevel, button, fit_text, inside
from . import blinkers_picture as pic


PANEL_BG = (12, 12, 14)
F_LETTER = _font("DejaVuSans-Bold.ttf", 11)


class BlinkersApp(App):
    name, title, icon = "blinkers", "Blinkers LED display", "blinkers"
    short = "Blinkers"

    def __init__(self, desk):
        super().__init__(desk)
        self.picked = set()
        self.geom = {}              # LED name -> box on the screen (whole-desktop coordinates)
        self.btns = {}
        self.note = "Tap LEDs, then Show"
        self.pct = 100
        self.busy = ""
        self._seen = None
        self._was_running = False

    @property
    def b(self):
        return self.r.blinkers

    def _bg(self, label, fn):
        import threading
        if self.busy:
            return
        self.busy = label

        def go():
            try:
                self.note = fn() or ""
            except Exception as e:
                self.note = f"error: {e}"
            self.busy = ""
            self.d.dirty = True
        threading.Thread(target=go, daemon=True).start()

    def _show(self, res):
        if not res.get("ok"):
            return res.get("msg", "could not show that")
        if res.get("frames"):
            return f"{len(res['frames'])} frames, {res['frame_ms']} ms each"
        bits = [f"{len(res['patterns'])} pattern(s)" + (" (flickers)" if res.get("multiplexed") else "")]
        if res.get("extra"):
            bits.append("extra: " + ",".join(res["extra"]))
        if res.get("missing"):
            bits.append("can't: " + ",".join(res["missing"]))
        return "; ".join(bits)

    # ---------------------------------------------------------------- the picture
    def _assignment(self):
        """Which LED lights which part of the picture (head and letters from the camera test), cached per layout."""
        lay = self.b.layout()
        key = (lay or {}).get("t")
        if getattr(self, "_assign_key", "x") != key:
            self._assign_key = key
            self._assign, self._assign_info = pic.assign(lay)
        return self._assign, self._assign_info

    # ---------------------------------------------------------------- drawing
    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        reach, on = set(self.b.reachable()), set(self.b.on)
        panel = (x0 + 4, y0 + 3, x1 - 4, y0 + 79)
        bevel(d, panel, False, PANEL_BG)
        leds_of, info = self._assignment()
        self.geom = pic.draw(d, (panel[0] + 2, panel[1] + 2, panel[2] - 2, panel[3] - 2), leds_of, on, self.picked, reach, F_LETTER)
        tag = "camera map" if info.get("source") == "camera" else "no camera map yet"
        d.text((panel[0] + 4, panel[3] - 12), tag, font=F_UI, fill=(90, 90, 100))
        labels = [["Show", "Clear", "Off", "Test"], ["Full", "777", "Batt -", "Batt +"], ["Shimmer", "Scan", "Chase", "Pulse"]]
        bw = (x1 - x0 - 8 - 3 * 4) / 4
        self.btns = {}
        for r_i, row in enumerate(labels):
            for c_i, lab in enumerate(row):
                bx0 = x0 + 4 + c_i * (bw + 4)
                bb = (int(bx0), y0 + 82 + r_i * 21, int(bx0 + bw), y0 + 82 + r_i * 21 + 19)
                self.btns[lab] = bb
                button(d, bb, lab, font=F_BOLD, color=RED if lab == "Off" else NAVY if lab in ("Test", "Shimmer", "Scan", "Chase", "Pulse") else DARK)
        ly = y0 + 147
        p = self.b.progress
        if p.get("running"):
            n = max(1, p.get("n") or 1)
            msg = f"Test {p.get('i', 0)}/{n}: {p.get('msg', '')}"
            d.rectangle((x0 + 6, ly, x1 - 6, ly + 8), outline=SHADOW)
            d.rectangle((x0 + 7, ly + 1, x0 + 7 + int((x1 - x0 - 14) * min(1.0, p.get("i", 0) / n)), ly + 7), fill=NAVY)
            d.text((x0 + 6, ly + 10), fit_text(d, msg, x1 - x0 - 12), font=F_UI, fill=NAVY)
        else:
            d.text((x0 + 6, ly), fit_text(d, ("Busy: " + self.busy) if self.busy else self.note, x1 - x0 - 12), font=F_UI,
                   fill=NAVY if self.busy else DARK)
            on_txt = ("On: " + (self.b.showing or "")) if self.b.showing else "Off"
            d.text((x0 + 6, ly + 11), fit_text(d, on_txt, x1 - x0 - 12), font=F_UI, fill=GREEN if self.b.showing else SHADOW)
            e = self.b.last_exp
            if e:
                s = e["summary"]
                txt = (f"Test: {s['leds_located']}+{s.get('leds_approx', 0)}/{s['leds_total']} LEDs mapped, controls {'dark' if s['controls_dark'] else 'NOT dark'}"
                       if s.get("leds_total") else
                       f"Test: {s['patterns_seen_lit']}/{s['patterns_tested']} lit, controls {'dark' if s['controls_dark'] else 'NOT dark'}")
                d.text((x0 + 6, ly + 22), fit_text(d, txt, x1 - x0 - 12), font=F_UI, fill=DARK)

    # ---------------------------------------------------------------- input
    def click(self, x, y):
        for lab, bx in self.btns.items():
            if inside(bx, x, y):
                self.press_btn(lab)
                self.d.dirty = True
                return
        best, bd = None, 12 ** 2        # the LED whose part of the picture is nearest the tap (within 12 px)
        for name, pts in self.geom.items():
            for px, py in pts:
                dd = (px - x) ** 2 + (py - y) ** 2
                if dd < bd:
                    best, bd = name, dd
        if best:
            if best in self.b.reachable():
                self.picked ^= {best}
                self.note = ", ".join(sorted(self.picked)) or "nothing picked"
            else:
                self.note = f"{best}: not wired"
            self.d.dirty = True

    def press_btn(self, lab):
        b = self.b
        if lab == "Show":
            if not self.picked:
                self.note = "pick some LEDs first"
                return
            self._bg("lighting", lambda: self._show(b.show(sorted(self.picked), None, 60, True)))
        elif lab == "Clear":
            self.picked = set()
            self.note = "picks cleared"
        elif lab == "Off":
            if getattr(self.r, "weather", None) and self.r.weather.panel_running():
                self.r.weather.panel_off()                  # the Weather app's panel cycle stops too
            self._bg("off", b.off)
        elif lab == "Full":
            self.pct = 100
            self._bg("lighting", lambda: self._show(b.show_battery(100, 60)))
        elif lab == "777":
            self._bg("lighting", lambda: self._show(b.show_number("777", 30)))
        elif lab in ("Batt -", "Batt +"):
            self.pct = max(0, min(100, self.pct + (25 if lab == "Batt +" else -25)))
            if self.pct == 0:
                self._bg("off", lambda: b.off() and "battery 0%: all bars off")
            else:
                self._bg("lighting", lambda: self._show(b.show_battery(self.pct, 60)))
        elif lab in ("Shimmer", "Scan", "Chase", "Pulse"):
            name = lab.lower()
            self._bg("starting", lambda: self._show(b.animate(b.named_frames(name), 80, 30, label=f"animation {name}")))
        elif lab == "Test":
            res = b.start_experiment("map")
            self.note = res.get("msg", "") if not res.get("ok") else "camera test started (about 1 minute)"

    def tick(self):
        p = self.b.progress
        if self._was_running and not p.get("running"):          # a camera test just finished: say how it went
            e = self.b.last_exp
            if p.get("error"):
                self.note = f"test failed: {p['error']}"
            elif e:
                s = e["summary"]
                if not s["display_found"]:
                    self.note = "display not found: dim the room and check the view"
                elif s.get("moved"):
                    self.note = "something moved during the test: keep still and run it again"
                else:
                    self.note = (("learned the wiring, " if s.get("wiring_learned") else "") +
                                 f"mapped {s['leds_located']}/{s['leds_total']} LEDs" + (", layout ok" if s.get("layout_ok") else ", layout?"))
        self._was_running = bool(p.get("running"))
        st = (self.b.value, self.b.showing, tuple(sorted(self.b.on)), self.busy, id(self.b.last_exp), p.get("running"), p.get("i"),
              p.get("msg"), self.b.layout() is not None)
        if st != self._seen:
            self._seen = st
            return True
        return False
