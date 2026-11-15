"""RoboDev on RoboOS: chat with the robot's coding agent on the touchscreen (robotd/agent.py does the work).
Type (on-screen or USB keyboard), talk (mic button: speech to text into the input line), attach a camera photo or the
newest photo, switch permission mode (Manual / Auto / FULL) and model, Stop, start a New chat.
Risky steps pop up an Allow / Deny dialog. Tap a tool line to see its output."""
import threading
import time

from .core import PHOTOS
from roboos.desktop import (App, DARK, F_BOLD, F_MONO, F_UI, FACE, LIGHT, NAVY, RED, SBW, SHADOW, Scroll, bevel, button, draw_icon,
                      fit_text, inside)
from roboos.media import Keyboard, LineEdit

LINE_H = 12
TEAL_TXT = (0, 110, 110)
GRAY = (95, 95, 95)
AMBER = (255, 240, 170)
GREEN_TXT = (0, 130, 40)


def wrap(d, text, font, width):
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            cand = (line + " " + word) if line else word
            if d.textlength(cand, font=font) <= width:
                line = cand
                continue
            if line:
                out.append(line)
            while d.textlength(word, font=font) > width and len(word) > 1:     # a very long word: break it
                n = len(word)
                while n > 1 and d.textlength(word[:n], font=font) > width:
                    n -= 1
                out.append(word[:n])
                word = word[n:]
            line = word
        out.append(line)
    return out


class RoboDev(App):
    name, title, icon = "robodev", "RoboDev", "robodev"

    def __init__(self, desk):
        super().__init__(desk)
        self.a = desk.r.agent
        self.edit = LineEdit()
        self.kb = Keyboard()
        self.osk = False
        self.back = 0                     # lines scrolled up from the newest
        self.bar = Scroll(step=3)
        self._sbgrab = False
        self.expanded = set()             # tool items whose output is shown
        self.attach = []
        self.listening = False
        self.hits, self.line_hits = [], []
        self._seen = None
        self._dialog_for = None
        self._rows = 8
        self._total = 0
        self.tbox = (0, 0, 1, 1)
        self.ebox = (0, 0, 1, 1)

    # ------------------------------------------------------------------ drawing
    def _lines(self, d, width):
        lines = []                                    # (text, font, color, bg, item)
        for it in self.a.since(0)[-120:]:
            k = it["kind"]
            if k == "user":
                files = f"  [+{len(it.get('files') or [])} file]" if it.get("files") else ""
                for t in wrap(d, "> " + it.get("text", "") + files, F_BOLD, width):
                    lines.append((t, F_BOLD, NAVY, None, it))
            elif k == "assistant":
                for t in wrap(d, it.get("text", ""), F_UI, width):
                    lines.append((t, F_UI, DARK, None, it))
            elif k == "tool":
                mark = {None: "…", True: "ok", False: "FAIL"}[it.get("ok")]
                open_ = it.get("key") in self.expanded      # seq changes when the item updates; key does not
                head = f"{'v' if open_ else '>'} {it.get('tool')}: {it.get('args', '')}"
                head = fit_text(d, head, width - 30, F_MONO)
                lines.append((f"{head}  {mark}", F_MONO, RED if it.get("ok") is False else GRAY, None, it))
                if open_:
                    for t in wrap(d, it.get("out") or "(no output yet)", F_MONO, width - 8)[:60]:
                        lines.append(("  " + t, F_MONO, (60, 60, 60), (236, 236, 236), it))
            elif k == "approval":
                st = {"pending": "WAITING FOR YOU", "allowed": "allowed", "denied": "denied"}.get(it.get("state"), it.get("state"))
                for t in wrap(d, f"! {it.get('title')} - {it.get('why')} [{st}]", F_UI, width):
                    lines.append((t, F_UI, DARK, AMBER, it))
            elif k in ("info", "error"):
                for t in wrap(d, it.get("text", ""), F_UI, width):
                    lines.append((t, F_UI, RED if k == "error" else TEAL_TXT, None, it))
        return lines

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        st = self.a.status()
        self.hits = []
        # toolbar
        d.rectangle((x0, y0, x1, y0 + 16), fill=FACE)
        mode = st["mode"]
        mlabel = {"manual": "Manual", "auto": "Auto", "full": "FULL"}.get(mode, mode)
        tools = [(mlabel, "mode", 44), (st["model_label"], "model", 92), (f"Attach{' ' + str(len(self.attach)) if self.attach else ''}", "attach", 46),
                 ("Stop" if st["state"] != "idle" else "Help", "stop" if st["state"] != "idle" else "help", 34), ("New", "new", 30)]
        bx = x0 + 2
        for label, act, w in tools:
            b = (bx, y0 + 1, bx + w, y0 + 15)
            button(d, b, fit_text(d, label, w - 6), color=RED if act == "mode" and mode == "full" else DARK,
                   font=F_BOLD if act in ("mode", "stop") else F_UI)
            self.hits.append((b, act))
            bx += w + 2
        cost = f"${st['spend_today']:.2f}"
        d.text((x1 - d.textlength(cost, font=F_UI) - 3, y0 + 3), cost, font=F_UI, fill=GRAY)
        # input row (+ keyboard)
        usb_kb = "keyboard" in self.d.inputs.kinds
        if self.osk and not usb_kb:
            kb_top = y1 - Keyboard.HEIGHT + 1
            self.kb.draw(img, (x0, kb_top, x1, y1))
            iy = kb_top - 19
        else:
            self.kb.keys = []
            iy = y1 - 18
        d.rectangle((x0, iy, x1, iy + 18), fill=FACE)
        self.ebox = (x0 + 2, iy + 1, x1 - 62, iy + 17)
        self.edit.draw(d, self.ebox, focused=self.osk or usb_kb)
        if not self.edit.text and not (self.osk or usb_kb):
            d.text((self.ebox[0] + 4, iy + 3), "Tap to type, or use the mic", font=F_UI, fill=SHADOW)
        mb = (x1 - 59, iy + 1, x1 - 38, iy + 17)
        bevel(d, mb, raised=not self.listening, fill=(255, 200, 200) if self.listening else FACE)
        draw_icon(d, "mic", mb[0] + 3, mb[1], s=16)
        sb = (x1 - 36, iy + 1, x1 - 2, iy + 17)
        button(d, sb, "Send", font=F_BOLD)
        self.hits += [(mb, "mic"), (sb, "send")]
        # transcript
        tb = (x0, y0 + 17, x1, iy - 1)
        self.tbox = tb
        d.rectangle((tb[0], tb[1], tb[2] - SBW, tb[3]), fill=LIGHT)
        width = tb[2] - SBW - tb[0] - 8
        lines = self._lines(d, width)
        status = None
        if self.listening:
            status = "Listening... speak, then pause (tap the mic to finish)"
        elif st["pending"]:
            status = "Waiting for you: Allow or Deny"
        elif st["state"] != "idle":
            status = f"{st['current'] or 'working'}  ({st['busy_s']} s)"
        if status:
            lines.append((fit_text(d, status, width), F_UI, TEAL_TXT, (225, 240, 240), None))
        if not lines:
            lines = [(t, F_UI, GRAY, None, None) for t in wrap(d, "Ask RoboDev to change the robot: new RoboOS apps, screen or motor "
                     "firmware, web app, settings. Mode: Auto asks before risky steps, Manual asks before every step, FULL never asks. "
                     "Tip: Help shows what it can do.", F_UI, width)]
        rows = max(1, (tb[3] - tb[1] - 2) // LINE_H)
        self._rows, self._total = rows, len(lines)
        self.back = max(0, min(self.back, len(lines) - rows))
        end = len(lines) - self.back
        start = max(0, end - rows)
        self.line_hits = []
        for i, (t, font, color, bg, it) in enumerate(lines[start:end]):
            y = tb[1] + 1 + i * LINE_H
            if bg:
                d.rectangle((tb[0], y, tb[2] - SBW, y + LINE_H - 1), fill=bg)
            d.text((tb[0] + 4, y), t, font=font, fill=color)
            self.line_hits.append(((tb[0], y, tb[2] - SBW, y + LINE_H - 1), it))
        self.bar.draw(d, (tb[2] - SBW + 1, tb[1], tb[2], tb[3]), start, len(lines), rows)

    # ------------------------------------------------------------------ input
    def click(self, x, y):
        for b, act in self.hits:
            if inside(b, x, y):
                return self.act(act)
        for b, k in self.kb.keys:
            if inside(b, x, y):
                k = self.kb.click(x, y)
                if k:
                    self.key(k)
                return
        if inside(self.ebox, x, y):
            self.osk = "keyboard" not in self.d.inputs.kinds
            return
        if self.bar.hit(x, y):
            self._set_top(self.bar.click(x, y))
            return
        for b, it in self.line_hits:
            if inside(b, x, y) and it:
                if it["kind"] == "tool":
                    ident = it.get("key")
                    if ident in self.expanded:
                        self.expanded.discard(ident)
                    else:
                        self.expanded.add(ident)
                elif it["kind"] == "approval" and it.get("state") == "pending":
                    self._show_dialog(force=True)
                return
        if self.osk and inside(self.tbox, x, y):
            self.osk = False

    def act(self, act):
        a, desk = self.a, self.d
        if act == "send":
            return self.send()
        if act == "mic":
            if self.listening:
                self.r.voice.stop_listening.set()
            else:
                threading.Thread(target=self._listen, daemon=True).start()
            return
        if act == "mode":
            nxt = {"manual": "auto", "auto": "full", "full": "manual"}[a.cfg()["mode"] if a.cfg()["mode"] in ("manual", "auto", "full") else "auto"]
            if nxt == "full":
                mins = a.cfg()["full_minutes"]
                desk.confirm("Full access?", "RoboDev runs anything without\nasking" + (f", for {mins} minutes." if mins else "."),
                             lambda: a.settings(mode="full"))
            else:
                a.settings(mode=nxt)
                desk.toast({"manual": "Manual: every step asks", "auto": "Auto: risky steps ask"}[nxt])
            return
        if act == "model":
            ms = a.models()
            if not ms:
                return desk.toast("no OpenRouter models")
            ids = [m["id"] for m in ms]
            cur = a.model()
            m = ms[(ids.index(cur) + 1) % len(ms)] if cur in ids else ms[0]
            a.settings(model=m["id"])
            desk.toast(f"{m['label']} {m.get('price', '')}"[:40])
            return
        if act == "attach":
            desk.dialog = {"title": "Attach", "text": "Send a picture with your\nnext message:",
                           "buttons": [("Camera", self._attach_camera), ("Photo", self._attach_photo), ("Clear", lambda: self.attach.clear())]}
            return
        if act == "stop":
            a.stop()
            desk.toast("stopping")
            return
        if act == "help":
            self.edit = LineEdit("What can you do, and how should I ask you for changes?")
            return
        if act == "new":
            desk.confirm("New chat?", "Start a new conversation\n(the old one is kept).", lambda: desk.toast(a.new_session()[1]))

    def send(self):
        text = self.edit.text.strip()
        if not text and not self.attach:
            return
        ok, msg = self.a.send(text, list(self.attach), source="desktop")
        if ok:
            self.edit, self.attach, self.osk, self.back = LineEdit(), [], False, 0
        self.d.toast(msg[:44])

    def _listen(self):
        self.listening = True
        self.d.dirty = True
        try:
            text = self.r.voice.listen(max_s=40, silence_s=1.8, wait_s=8)
        finally:
            self.listening = False
        if text:
            self.edit = LineEdit((self.edit.text + " " + text).strip())
        else:
            self.d.toast("didn't hear anything")
        self.d.dirty = True

    def _attach_camera(self):
        if self.r.cam.privacy:
            return self.d.toast("camera is off (privacy)")

        def go():
            try:
                self.attach.append({"image": self.r.cam.still(1280, 720), "name": "camera.jpg"})
                self.d.toast("camera photo attached")
            except Exception as e:
                self.d.toast(f"camera: {e}"[:44])
            self.d.dirty = True
        threading.Thread(target=go, daemon=True).start()

    def _attach_photo(self):
        files = sorted((f for f in PHOTOS.glob("*") if f.suffix.lower() in (".jpg", ".jpeg", ".png")), key=lambda f: f.stat().st_mtime)
        if not files:
            return self.d.toast("no photos yet")
        self.attach.append({"path": str(files[-1]), "name": files[-1].name})
        self.d.toast(f"attached {files[-1].name}"[:44])

    def key(self, k):
        if k == "ENTER":
            return self.send()
        if k == "ESC":
            self.osk = False
            return
        if k in ("PGUP", "UP") and not self.edit.text:
            self.back += self._rows // 2
            return
        if k in ("PGDN", "DOWN") and not self.edit.text:
            self.back = max(0, self.back - self._rows // 2)
            return
        self.edit.key(k)

    def _set_top(self, top):
        self.back = max(0, self._total - self._rows - top)

    def press(self, x, y):
        self._sbgrab = self.bar.hit(x, y) and self.bar.grab(x, y)

    def release(self, x, y):
        self._sbgrab = False
        self.bar.release()

    def hold(self, x, y):
        if self.bar.hit(x, y) and self.bar.on_arrow(x, y):
            self._set_top(self.bar.click(x, y))
            return True
        return False

    def drag(self, x, y, dx, dy):
        if self._sbgrab:
            self._set_top(self.bar.drag(y))
            return True
        if not inside(self.tbox, x, y):
            return False
        self.d._drag_acc += dy
        steps = int(self.d._drag_acc / LINE_H)
        if steps:
            self.back = max(0, self.back + steps)
            self.d._drag_acc -= steps * LINE_H
        return True

    def scroll(self, n):
        self.back = max(0, self.back - n)

    def _show_dialog(self, force=False):
        p = self.a.pending
        if not p or (self.d.dialog and not force):
            return
        aid = p["id"]
        from .agent import _wrap
        self.d.dialog = {"title": "RoboDev asks", "text": _wrap(f"{p['title']}\n{p['why']}", 36, 2), "agent": aid,
                         "buttons": [("Allow", lambda: self.a.answer(aid, True)), ("Deny", lambda: self.a.answer(aid, False))]}
        self._dialog_for = aid
        self.d.dirty = True

    def tick(self):
        st = self.a.status()
        p = st["pending"]
        if p and p["id"] != self._dialog_for and not self.d.dialog:
            self._show_dialog()
        key = (st["seq"], st["state"], st["current"], st["busy_s"] if st["state"] != "idle" else 0, bool(p), self.listening,
               len(self.attach), int(time.time() * 2) % 2 if (self.osk or "keyboard" in self.d.inputs.kinds) else 0)
        changed = key != self._seen
        self._seen = key
        return changed
