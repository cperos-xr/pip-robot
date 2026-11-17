"""RoboOS Scripts app (robot only): pick a demo script and play it hands-free (robotd/scripts.py).

Tapping a script asks once on the screen (floor clear?), which is the owner's approval for the whole playback,
wheelie included; then the screen switches to the face for the show. The web app (Live > Scripts) does the same
from a phone, with a teleprompter, Next and Stop."""
import threading

from roboos.desktop import DARK, F_BOLD, F_UI, FACE, NAVY, RED, SHADOW, ListApp, fit_text


class ScriptsApp(ListApp):
    name, title, icon = "scripts", "Scripts", "notepad"
    row_h = 22

    def __init__(self, desk):
        super().__init__(desk)
        self.items, self.note = [], "Tap a script to play it. Pip asks once (floor clear?), then shows its face."
        self.reload()

    def reload(self):
        try:
            self.items = self.r.scripts.list()
        except Exception as e:
            self.items, self.note = [], str(e)

    def rows(self):
        return self.items

    def tick(self):
        return bool(self.r.scripts.state.get("busy"))

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        st = self.r.scripts.state
        top = f"Playing {st.get('title')}: step {st.get('i')}/{st.get('n')}" if st.get("busy") else self.note
        d.text((x0 + 6, y0 + 4), fit_text(d, top, x1 - x0 - 12), font=F_UI, fill=NAVY)
        self.draw_list(d, (x0, y0 + 20, x1, y1))

    def draw_row(self, d, box, row, i):
        bad = row.get("errors") or row.get("problems")
        d.text((box[0] + 6, box[1] + 1), fit_text(d, row.get("title", row["name"]), 200), font=F_BOLD, fill=RED if bad else DARK)
        sub = (bad or [f"{row.get('steps', 0)} steps, about {max(1, round(row.get('secs', 0) / 60))} min"
                       + (", wheelie" if row.get("wheelie") else "")])[0]
        d.text((box[0] + 6, box[1] + 11), fit_text(d, sub, box[2] - box[0] - 12), font=F_UI, fill=SHADOW)

    def click(self, x, y):
        i = self.list_click(x, y)
        if i is None or i >= len(self.items):
            return
        item = self.items[i]
        if item.get("errors") or item.get("problems"):
            self.d.toast((item.get("errors") or item.get("problems"))[0])
            return
        threading.Thread(target=self._ask_and_play, args=(item,), daemon=True, name="scripts-ask").start()

    def _ask_and_play(self, item):
        r = self.r
        if item.get("wheelie"):
            opts = ["Start", "No wheelie", "Cancel"]
            q = (f"{item['title']}: a wheelie at step {item.get('wheelie_step')}. Floor clear 1 m in front and behind, "
                 "on battery, someone ready to catch? This is the only question.")
        else:
            opts = ["Start", "Cancel"]
            q = f"Play {item['title']}? Floor around me clear?"
        ans = r.api.ask_owner("Scripts", q, opts, 120)
        if ans not in ("Start", "No wheelie"):
            return
        ok, msg = r.scripts.play(item["name"], wheelie_ok=ans == "Start", who="RoboOS")
        if not ok:
            self.note = msg
            r.toast("Scripts: " + msg, 4000)
        self.d.dirty = True
