"""Reminders for the owner, shown as a prompt on the touchscreen (owner, 2026-11-08: "remind me in the morning or have
the robot tell me on the screen with a prompt reminding me to test that feature").

A reminder waits until `not_before`, then shows on the first motion-sensor detection (the owner is up and about), or at
`fallback_at` if nothing moved. The prompt is silent (screen only, a soft beep) so it never wakes anyone. Buttons:
the first option runs `action` (if any), "Later" snoozes 2 hours; after `max_shows` unanswered prompts it gives up.
Stored in kv "reminders". POST /api/reminders adds one, GET lists them, POST /api/reminders/clear removes them.
"""
import threading
import time
import uuid
from datetime import datetime

ACTIONS = ("security_test",)


def to_ts(v):
    """Epoch seconds from a number or an ISO time ("2026-11-08T10:00:00-10:00"; no zone = robot local time)."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    d = datetime.fromisoformat(str(v))
    return d.timestamp() if d.tzinfo else time.mktime(d.timetuple())


class Reminders:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.Lock()
        self.showing = False
        robot.bus.on("motion", lambda pir: self.check(motion=True))
        threading.Thread(target=self._loop, daemon=True, name="reminders").start()

    def items(self):
        return self.r.store.kv_get("reminders", []) or []

    def _save(self, items):
        self.r.store.kv_set("reminders", items[-30:])

    def add(self, text, title="Reminder", not_before=None, fallback_at=None, action=None, opts=None, on_motion=True):
        if action and action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")
        it = {"id": uuid.uuid4().hex[:8], "title": str(title)[:40], "text": str(text)[:300], "action": action,
              "opts": (opts or (["Test now", "Later"] if action else ["OK", "Later"]))[:3],
              "not_before": to_ts(not_before) or time.time(), "fallback_at": to_ts(fallback_at), "on_motion": bool(on_motion),
              "snooze_until": 0, "shown": 0, "max_shows": 3, "done": False, "created": time.time()}
        with self.lock:
            items = self.items()
            items.append(it)
            self._save(items)
        self.r.log(f"reminder added for {time.strftime('%a %H:%M', time.localtime(it['not_before']))}: {it['text'][:60]}")
        return it

    def clear(self):
        with self.lock:
            self._save([])

    def _loop(self):
        while True:
            time.sleep(60)
            try:
                self.check(motion=False)
            except Exception as e:
                self.r.log(f"reminders: {e}", "warn")

    def check(self, motion):
        if self.showing:
            return
        now = time.time()
        with self.lock:
            for it in self.items():
                if it["done"] or now < it["not_before"] or now < it.get("snooze_until", 0):
                    continue
                if (motion and it["on_motion"]) or (it.get("fallback_at") and now >= it["fallback_at"]) or not it["on_motion"]:
                    self.showing = True
                    threading.Thread(target=self._show, args=(it["id"],), daemon=True).start()
                    return

    def _update(self, rid, **kw):
        with self.lock:
            items = self.items()
            for it in items:
                if it["id"] == rid:
                    it.update(kw)
            self._save(items)

    def _show(self, rid):
        try:
            it = next((x for x in self.items() if x["id"] == rid), None)
            if not it:
                return
            self._update(rid, shown=it["shown"] + 1)
            self.r.cyd.send({"t": "beep", "hz": 880, "ms": 60})
            self.r.log(f"reminder shown: {it['text'][:60]}")
            ans = self.r.api.ask_owner(it["title"], it["text"], it["opts"], 2 * 3600)
            if ans == it["opts"][0]:
                self._update(rid, done=True, answered=ans)
                self._act(it.get("action"))
            elif ans == "Later":
                self._update(rid, snooze_until=time.time() + 2 * 3600)
            elif it["shown"] + 1 >= it["max_shows"]:
                self._update(rid, done=True, answered=None)
            else:
                self._update(rid, snooze_until=time.time() + 30 * 60)    # no answer: ask again later
        finally:
            self.showing = False

    def _act(self, action):
        if action == "security_test":
            self.r.motion_modes.set("security", arm_delay=60)
            self.r.voice.speak("Okay! Security mode turns on in one minute. Leave the room, wait a minute, then walk back in. "
                               "Afterwards, switch it off in the web app, Body tab, and look at the photo I took.", "happy")
