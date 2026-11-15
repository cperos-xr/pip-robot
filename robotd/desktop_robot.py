"""RoboOS apps that only make sense on the robot: Camera (privacy + eye-follow switches) and Settings (the robot's one
settings list). RoboOS itself (roboos/) is shared with robot-less screens; these are added by RobotHost.apps()."""
import json
import time

from roboos.desktop import (App, DARK, F_BIG, F_BOLD, F_UI, FACE, GREEN, LIGHT, ListApp, NAVY, RED, SBW, SHADOW,
                            bevel, button, draw_icon, fit_text, inside)


class CameraApp(App):
    name, title, icon = "camera", "Camera", "camera"

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        cam = self.r.cam
        off = cam.privacy
        draw_icon(d, "camera", x0 + 10, y0 + 8, 24, off=off)
        d.text((x0 + 42, y0 + 8), "Camera is OFF (privacy)" if off else "Camera is allowed", font=F_BIG, fill=RED if off else GREEN)
        users = sorted(cam.live_owners) if cam.live_proc else []
        d.text((x0 + 42, y0 + 24), ("In use by: " + ", ".join(users)) if users else "Not streaming right now", font=F_UI, fill=DARK)
        self.big = (x0 + 10, y0 + 42, x1 - 10, y0 + 74)
        button(d, self.big, "Turn camera ON" if off else "Turn camera OFF", font=F_BIG, color=GREEN if off else RED)
        vc = self.r.vision.cfg()
        self.opts = []
        for i, (key, label) in enumerate((("follow", "Eyes follow faces"), ("gaze_mirror", "Eyes copy where you look"),
                                          ("reactions", "React to smiles and QR codes"))):
            b = (x0 + 10, y0 + 84 + i * 22, x0 + 24, y0 + 98 + i * 22)
            bevel(d, b, raised=False, fill=LIGHT)
            if vc.get(key):
                d.line([(b[0] + 3, b[1] + 7), (b[0] + 6, b[3] - 3), (b[2] - 3, b[1] + 3)], fill=DARK, width=2)
            d.text((b[2] + 6, b[1] + 1), label, font=F_UI, fill=DARK)
            self.opts.append(((b[0], b[1], x1 - 10, b[3]), key))
        d.text((x0 + 10, y1 - 14), "Eye options keep the camera on while the face shows.", font=F_UI, fill=SHADOW)

    def click(self, x, y):
        if inside(self.big, x, y):
            self.d.set_privacy(not self.r.cam.privacy)
            return
        for b, key in self.opts:
            if inside(b, x, y):
                vc = self.r.vision.cfg()
                vc[key] = not vc.get(key)
                self.r.cfg.save()
                return


class SettingsApp(ListApp):
    """Settings: the robot's one settings list (robotd/settings.py), the same list the touchscreen's Settings page and
    the web app's Settings tab show. Group tabs on top, a row per setting; tap the value to change it."""
    name, title, icon = "settings", "Settings", "settings"
    row_h = 24
    TAB_H = 20

    def __init__(self, desk):
        super().__init__(desk)
        self.group, self.note, self.cache, self.t = 0, "", None, 0

    def data(self):
        if self.cache is None or time.time() - self.t > 1.5:
            self.cache, self.t = self.r.settings.all(), time.time()
        return self.cache

    def rows(self):
        g = self.data()
        self.group = max(0, min(self.group, len(g) - 1))
        return g[self.group]["rows"] if g else []

    def tick(self):
        if time.time() - self.t > 2:
            old = json.dumps(self.cache, default=str)
            self.cache = None
            return json.dumps(self.data(), default=str) != old
        return False

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        groups = self.data()
        self.tabs = []
        tw = (x1 - x0 + 1) / max(1, len(groups))
        for i, g in enumerate(groups):
            b = (int(x0 + i * tw), y0 + 1, int(x0 + (i + 1) * tw) - 1, y0 + self.TAB_H)
            button(d, b, fit_text(d, g["name"], b[2] - b[0] - 4), pressed=i == self.group, fill=LIGHT if i == self.group else FACE)
            self.tabs.append(b)
        foot = 14 if self.note else 0
        self.draw_list(d, (x0, y0 + self.TAB_H + 3, x1, y1 - foot))
        if self.note:
            d.text((x0 + 4, y1 - 13), fit_text(d, self.note, x1 - x0 - 8), font=F_UI, fill=NAVY)

    def ctrl(self, box, row):
        """Where the control of a row sits: list of (box, op)."""
        x0, y0, x1, y1 = box
        b = (x1 - 4, y0 + 2, x1 - 4, y1 - 2)
        k = row["kind"]
        if k == "range":
            return [((x1 - 116, y0 + 2, x1 - 88, y1 - 2), "-"), ((x1 - 32, y0 + 2, x1 - 4, y1 - 2), "+")]
        if k == "toggle":
            return [((x1 - 100, b[1], b[2], b[3]), "toggle")]
        if k in ("choice", "action"):
            return [((x1 - 160, b[1], b[2], b[3]), "next" if k == "choice" else "run")]
        return []

    def draw_row(self, d, box, row, i):
        x0, y0, x1, y1 = box
        d.text((x0 + 5, y0 + 6), fit_text(d, row["label"], 128), font=F_BOLD, fill=DARK)
        k, text = row["kind"], row["text"]
        cs = self.ctrl(box, row)
        if k == "range":
            for b, op in cs:
                button(d, b, op, font=F_BOLD)
            mid = (x1 - 88, y0, x1 - 32, y1)
            tw = d.textlength(text, font=F_BOLD)
            d.text(((mid[0] + mid[2] - tw) / 2, y0 + 6), text, font=F_BOLD, fill=NAVY)
        elif k == "toggle":
            on = bool(row["value"])
            button(d, cs[0][0], fit_text(d, text, 90), pressed=on, fill=(170, 220, 170) if on else FACE)
        elif k in ("choice", "action"):
            b = cs[0][0]
            button(d, b, fit_text(d, text, b[2] - b[0] - 8))
        else:
            d.text((x1 - 160, y0 + 6), fit_text(d, text, 156), font=F_UI, fill=SHADOW)

    def click(self, x, y):
        for i, b in enumerate(getattr(self, "tabs", [])):
            if inside(b, x, y):
                self.group, self.top, self.note = i, 0, ""
                return
        i = self.list_click(x, y)
        rows = self.rows()
        if i is None or i >= len(rows):
            return
        row = rows[i]
        rb = (self.box[0], self.box[1] + (i - self.top) * self.row_h, self.box[2] - SBW - 1, self.box[1] + (i - self.top + 1) * self.row_h - 1)
        self.note = row.get("note", "")
        for b, op in self.ctrl(rb, row):
            if inside(b, x, y):
                self.change(row, op)
                return

    def change(self, row, op, confirmed=False):
        ok, msg = self.r.settings.apply(row["id"], op, confirmed=confirmed, source="desktop")
        if msg == "confirm":
            self.d.confirm(row["label"] + "?", row.get("confirm", "Are you sure?"), lambda: self.change(row, op, True))
        elif not ok:
            self.d.toast(msg)
        self.cache = None
