"""RoboOS app: Battery (robotd/battery.py, INA219). Level, voltage, current, time left, and a graph of the last
1 / 6 / 24 hours (voltage line + current line). Settings > Battery has the same numbers and the options."""
import time

from roboos.desktop import App, DARK, F_BOLD, F_UI, FACE, GREEN, LIGHT, NAVY, RED, SHADOW, YELLOW, _font, bevel, button, fit_text, inside

F_HUGE = _font("DejaVuSans-Bold.ttf", 26)
AMP = (200, 60, 30)
RANGES = [("1 h", 3600), ("6 h", 6 * 3600), ("24 h", 24 * 3600)]


def batt_color(pct, state):
    if state == "charging":
        return (40, 130, 230)
    if state == "crit" or (pct is not None and pct <= 10):
        return RED
    if state == "low":
        return YELLOW
    return GREEN


def draw_battery(d, box, pct, state):
    """A battery outline with its fill; box = (x0, y0, x1, y1) including the + nub."""
    x0, y0, x1, y1 = box
    nub = max(3, (x1 - x0) // 14)
    d.rectangle((x0, y0, x1 - nub, y1), fill=LIGHT, outline=DARK, width=2)
    d.rectangle((x1 - nub + 1, y0 + (y1 - y0) // 3, x1, y1 - (y1 - y0) // 3), fill=DARK)
    if pct is not None:
        w = int((x1 - nub - x0 - 6) * max(0, min(100, pct)) / 100)
        if w > 0:
            d.rectangle((x0 + 3, y0 + 3, x0 + 3 + w, y1 - 3), fill=batt_color(pct, state))


class BatteryApp(App):
    name, title, icon = "battery", "Battery", "battery"
    short = "Battery"

    def __init__(self, desk):
        super().__init__(desk)
        self.range = 3600
        self.btns = {}
        self._seen = None
        self._t = 0.0

    @property
    def b(self):
        return self.r.battery

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        b = self.b
        top = (x0 + 4, y0 + 3, x1 - 4, y0 + 52)
        bevel(d, top, False, LIGHT)
        if not b.ok:
            d.text((top[0] + 8, top[1] + 8), "No battery sensor", font=F_BOLD, fill=RED)
            d.text((top[0] + 8, top[1] + 24), fit_text(d, b.err or "INA219 not found at 0x40", top[2] - top[0] - 16), font=F_UI, fill=DARK)
        else:
            draw_battery(d, (top[0] + 6, top[1] + 9, top[0] + 62, top[1] + 39), b.pct, b.state)
            big = "USB" if b.state == "external" else "--" if b.pct is None else f"{b.pct:.0f}%"
            d.text((top[0] + 70, top[1] + 4), big, font=F_HUGE, fill=DARK)
            from .battery import pack_label
            d.text((top[0] + 70, top[1] + 34), fit_text(d, pack_label(b.pack()), 76), font=F_UI, fill=SHADOW)
            tx = top[0] + 150
            word = {"ok": "", "low": "LOW", "crit": "EMPTY", "charging": "charging", "external": "no battery", "mismatch": "which pack?"}.get(b.state, "")
            if b.state == "external":
                d.text((tx, top[1] + 4), "USB power", font=F_BOLD, fill=NAVY)
                d.text((tx, top[1] + 18), "No battery connected,", font=F_UI, fill=DARK)
                d.text((tx, top[1] + 32), "so the wheels have no power.", font=F_UI, fill=DARK)
                self.draw_graph(d, (x0 + 4, y0 + 56, x1 - 4, y0 + 150))
                self._buttons(d, box)
                return
            d.text((tx, top[1] + 4), f"{b.v:.2f} V  ({b.cell_v():.2f} V/cell)", font=F_UI, fill=DARK)
            d.text((tx, top[1] + 18), f"{b.i:.2f} A   {b.v * b.i:.1f} W", font=F_UI, fill=AMP)
            tl = b.time_left()
            from .battery import fmt_dur
            line = word if word and not tl else (f"about {fmt_dur(tl)} left" + (f" - {word}" if word else "") if tl else "")
            d.text((tx, top[1] + 32), fit_text(d, line, top[2] - tx - 4, F_BOLD), font=F_BOLD,
                   fill=RED if b.state in ("low", "crit") else NAVY)
        self.draw_graph(d, (x0 + 4, y0 + 56, x1 - 4, y0 + 150))
        self._buttons(d, box)

    def _buttons(self, d, box):
        x0, y0, x1, y1 = box
        b = self.b
        self.btns = {}
        bw = 46
        for i, (lab, sec) in enumerate(RANGES):
            bb = (x0 + 4 + i * (bw + 4), y0 + 155, x0 + 4 + i * (bw + 4) + bw, y0 + 175)
            self.btns[lab] = bb
            button(d, bb, lab, pressed=self.range == sec, font=F_BOLD, color=NAVY if self.range == sec else DARK)
        sb = (x1 - 74, y0 + 155, x1 - 4, y0 + 175)
        self.btns["Settings"] = sb
        button(d, sb, "Settings", font=F_BOLD)
        tb = (sb[0] - 70, y0 + 155, sb[0] - 4, y0 + 175)
        self.btns["Test"] = tb
        button(d, tb, "Stop test" if b.test else "Test", font=F_BOLD, color=RED if b.test else DARK)

    def draw_graph(self, d, g):
        bevel(d, g, False, LIGHT)
        now = time.time()
        pts = [p for p in self.b.hist if p[0] >= now - self.range]
        gx0, gy0, gx1, gy1 = g[0] + 30, g[1] + 6, g[2] - 34, g[3] - 14
        d.rectangle((gx0, gy0, gx1, gy1), outline=(210, 210, 210))
        label = {3600: "last hour", 21600: "last 6 hours", 86400: "last 24 hours"}[self.range]
        ts = self.b.test_status()
        if ts:
            label = f"TEST {ts['mah']} mAh, {ts['running_s'] // 60} min, stops at {ts['stop_cell_v']:.2f} V/cell"
        elif self.b.last_test:
            lt = self.b.last_test
            label = f"last test: {lt['mah_delivered']} mAh" + (f", ~{lt['estimated_capacity_mah']} real" if lt.get("estimated_capacity_mah") else "")
        d.text((gx0 + 2, gy1 + 1), fit_text(d, label, gx1 - gx0 - 26), font=F_UI, fill=RED if ts else SHADOW)
        d.text((gx1 - 18, gy1 + 1), "now", font=F_UI, fill=SHADOW)
        if len(pts) < 2:
            d.text((gx0 + 10, gy0 + 30), "collecting... (a point every 10 s)", font=F_UI, fill=SHADOW)
            return
        vs = [p[1] for p in pts if p[1] > 5] or [p[1] for p in pts]
        ma = [p[2] for p in pts]
        vlo, vhi = min(vs), max(vs)
        if vhi - vlo < 0.2:
            mid = (vhi + vlo) / 2
            vlo, vhi = mid - 0.1, mid + 0.1
        alo, ahi = 0, max(100, max(ma))
        t0 = now - self.range
        X = lambda ts: gx0 + (gx1 - gx0) * (ts - t0) / self.range
        n = max(1, len(pts) // (gx1 - gx0))          # at most one point per pixel
        pts = pts[::n]
        amp_line = [(X(p[0]), gy1 - (gy1 - gy0) * (p[2] - alo) / (ahi - alo)) for p in pts]
        volt_line = [(X(p[0]), gy1 - (gy1 - gy0) * (p[1] - vlo) / (vhi - vlo)) for p in pts if p[1] > 5]
        if len(amp_line) > 1:
            d.line(amp_line, fill=AMP, width=1)
        if len(volt_line) > 1:
            d.line(volt_line, fill=NAVY, width=2)
        d.text((g[0] + 2, gy0 - 2), f"{vhi:.1f}", font=F_UI, fill=NAVY)
        d.text((g[0] + 2, gy1 - 10), f"{vlo:.1f}", font=F_UI, fill=NAVY)
        d.text((g[0] + 6, (gy0 + gy1) // 2 - 6), "V", font=F_BOLD, fill=NAVY)
        d.text((gx1 + 3, gy0 - 2), f"{ahi / 1000:.1f}", font=F_UI, fill=AMP)
        d.text((gx1 + 3, gy1 - 10), "0", font=F_UI, fill=AMP)
        d.text((gx1 + 6, (gy0 + gy1) // 2 - 6), "A", font=F_BOLD, fill=AMP)

    def click(self, x, y):
        for lab, bx in self.btns.items():
            if inside(bx, x, y):
                if lab == "Settings":
                    self.d.open_app("settings")
                elif lab == "Test":
                    if self.b.test:
                        self.b.stop_test()
                    else:
                        res = self.b.start_test()
                        if not res["ok"]:
                            self.d.toast(res["msg"][:60])
                else:
                    self.range = dict(RANGES)[lab]
                self.d.dirty = True
                return

    def tick(self):
        if time.time() - self._t < 2:
            return False
        self._t = time.time()
        b = self.b
        st = (b.ok, b.state, round(b.v, 2), round(b.i, 2), round(b.pct or 0), len(b.hist), b.hist[-1][0] if b.hist else 0,
              bool(b.test), (b.test_status() or {}).get("mah"), bool(b.last_test))
        if st != self._seen:
            self._seen = st
            return True
        return False
