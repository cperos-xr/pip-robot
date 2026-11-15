"""The robot's social life: drafts posts in its own voice, privacy-checks them, waits for the owner's OK,
then publishes to Bluesky (text + photo) and Pinterest (photo pins) and shows them on the public page.

Credentials: data/social.env (mode 600) with any of
  BLUESKY_HANDLE=robot.bsky.social  BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx  (Bluesky > Settings > App passwords)
  PINTEREST_TOKEN=pina_...  PINTEREST_BOARD_ID=1234567890                    (Pinterest API v5, scope pins:write)
Nothing is ever posted without passing the privacy check (faces are blurred first).

When to post (owner, 2026-11-06: "post more, at random times through the day when interesting things happen"):
- moment(): anything interesting (a game won or lost on the screen, a new feature, new firmware...) is remembered.
- Every day gets 3-5 random posting times between 8:30 and 21:30 (local time). At each one the robot posts its best fresh
  moment; with none it looks around with the camera, or shows off its own screen (a screenshot of its face, or a short
  video of it playing a RoboOS game against itself), or every few days draws a self-portrait (portrait.py). Big moments (score >= 3, e.g. a new feature) go out 5-30 min later.
- Limits: max_posts_per_day and min_gap_minutes in data/config.json "social".
- stats(): followers / likes from the public Bluesky API (shown in the face's top-left corner)."""
import base64
import io
import json
import os
import random
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from .core import DATA, PHOTOS, ROOT
from .social_track import SocialTracker

ENV = DATA / "social.env"
POST_IMG = PHOTOS / "posts"
# Owner (2026-11-10, made clearer 2026-11-13): the rule is about harm, not about people. Never post nudity or anything
# cruel. People in a photo are fine when the owner says so: the brain passes people_ok and the photo goes out unblurred,
# after one tap on Post (config social.people_tap; anyone near the robot can talk to it). Photos the robot takes on its
# own, with nobody asked, still get faces blurred automatically.
NUDITY_RULES = ("REJECT if there is ANY nudity, partial nudity, underwear, someone undressed or in a towel, a bathroom or shower scene, "
                "or anything sexual.")
PLACE_RULES = ("REJECT house numbers, street signs, addresses, license plates, mail, readable screens or documents, names, or "
               "anything that reveals where the owner lives. Pets, objects, the robot itself, scenery, light and weather are fine.")
OTHER_RULES = "REJECT any picture of a person that is bad, unflattering, embarrassing or unkind. " + PLACE_RULES
FACE_RULE = "REJECT if any human face is still recognisable (faces should already be blurred)."
PRIVACY_RULES = NUDITY_RULES + " " + FACE_RULE + " " + OTHER_RULES
UNBLURRED_RULES = (NUDITY_RULES + " " + PLACE_RULES + " People and their faces ARE allowed in this photo: the owner said it is OK "
                   "to post and approves it by hand. Do not reject it for showing people, ordinary poses or casual clothes; "
                   "reject a picture of a person only if it is clearly humiliating or cruel.")
SCREEN_RULES = ("This picture is a screenshot (or video frames) of the robot's OWN little touchscreen: its cartoon face, its menu, "
                "its games or its tiny desktop. Showing the robot's own interface and games is fine and is NOT a 'readable screen'. "
                "REJECT only if it shows personal details: IP addresses, Wi-Fi names, file names or paths, messages, people, "
                "or text with names or anything that reveals where the owner lives.")
SOCIAL_DIR = DATA / "social"
OFFERED = SOCIAL_DIR / "offered.json"   # media the owner gave the robot for posts: {name: {"about", "photo", "video"}}
REPEAT_STOP = set("this that with from have just today what when your they them then there their about into over very little "
                  "robot robots myself been were will would could".split())


def first_json(text, required=()):
    """The first JSON object in a model reply that has the required keys. Models sometimes echo the schema first
    ('{"type": "object", ...} Wait, I need to provide the actual answer: {...}') or add a note after the answer."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text or ""):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and all(k in obj for k in required) and not (obj.get("type") == "object" and "properties" in obj):
            return obj
    return None


def json_example(schema):
    """A plain example object for a schema (models copy an example better than they follow a schema)."""
    ex = {}
    for k, v in schema.get("properties", {}).items():
        ex[k] = {"boolean": True, "string": "...", "integer": 0, "number": 0}.get(v.get("type"), "...")
    return json.dumps(ex)


def load_env():
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            k, _, v = line.strip().partition("=")
            if k and v:
                os.environ[k] = v.strip().strip('"')


class Social:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.Lock()
        db = robot.store.db
        db.execute("CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT, image TEXT, alt TEXT, "
                   "status TEXT, privacy TEXT, bsky_uri TEXT, pin_id TEXT, error TEXT)")
        try:
            db.execute("ALTER TABLE posts ADD COLUMN video TEXT")
        except Exception:
            pass                                    # already there
        # a restart in the middle of an upload leaves a post "publishing": it may or may not be on Bluesky, so never retry it by itself
        db.execute("UPDATE posts SET status='failed', error='interrupted while posting (restart): check Bluesky before posting it again' "
                   "WHERE status='publishing'")
        db.commit()
        self.busy = threading.Lock()                # one scheduler run at a time (videos take a while)
        self._stats_ts = 0
        load_env()
        self.track = SocialTracker(robot)           # per-post likes / reposts / replies / followers over time
        robot.bus.on("answer", self._on_answer)

    # ------------------------------------------------------------------ config / state
    def cfg(self):
        c = self.r.cfg.data.setdefault("social", {})
        c.setdefault("autonomous", True)           # owner (2026-11-05): robot posts on its own after face-blur + safety check
        c.setdefault("auto_post_updates", True)
        c.setdefault("max_posts_per_day", 4)
        c.setdefault("min_gap_minutes", 90)
        c.setdefault("explore_every_hours", 3)     # look around and post if something fun is in view (0 = off)
        c.setdefault("hashtags", "#robot #buildinpublic")
        c.setdefault("posts_per_day", [3, 5])      # random posting times each day...
        c.setdefault("post_hours", [8.5, 21.5])    # ...between these local hours
        c.setdefault("show_off", True)             # screenshots / videos of the robot's own screen
        c.setdefault("people_tap", True)            # photos with people the owner OK'd: wait for one tap on Post (False: post at once)
        c.setdefault("uploads_post", True)         # owner (2026-11-14): Pip may post photos/videos from the shared folder on its own
        c.setdefault("uploads_look", True)         # ...and looks at each new one when it is uploaded and says what it thinks
        c.setdefault("self_portrait_days", 3)      # at most every N days a posting time may become a drawn self-portrait (0 = never)
        if not c.get("schedule_v2"):               # owner (2026-11-06): post more, at random times, show off features and games
            c["max_posts_per_day"] = max(int(c.get("max_posts_per_day", 4)), 6)
            c["min_gap_minutes"] = min(int(c.get("min_gap_minutes", 90)), 75)
            c["auto_post_updates"] = True
            c["schedule_v2"] = True
            self.r.cfg.save()
        return c

    # ------------------------------------------------------------------ face blurring (OpenCV, on the robot)
    def blur_faces(self, image):
        """Blur every face found (frontal + both profiles) before anything else sees the photo. Returns (image, count)."""
        import cv2
        import numpy as np
        if not hasattr(self, "_casc"):
            base = "/usr/share/opencv4/haarcascades/"
            self._casc = [cv2.CascadeClassifier(base + n) for n in
                          ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml", "haarcascade_profileface.xml")]
        img = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        h, w = img.shape[:2]
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        boxes = []
        for i, cc in enumerate(self._casc):
            for g, flip in ((gray, False), (cv2.flip(gray, 1), True)) if i == 2 else ((gray, False),):
                for (x, y, bw, bh) in cc.detectMultiScale(g, 1.1, 4, minSize=(max(24, w // 40), max(24, w // 40))):
                    boxes.append((w - x - bw if flip else x, y, bw, bh))
        for (x, y, bw, bh) in boxes:
            px, py = int(bw * 0.35), int(bh * 0.45)          # generous margin: hair, ears, chin
            x0, y0, x1, y1 = max(0, x - px), max(0, y - py), min(w, x + bw + px), min(h, y + bh + py)
            roi = img[y0:y1, x0:x1]
            if roi.size:
                small = cv2.resize(roi, (max(1, (x1 - x0) // 16), max(1, (y1 - y0) // 16)), interpolation=cv2.INTER_LINEAR)
                img[y0:y1, x0:x1] = cv2.GaussianBlur(cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST), (31, 31), 0)
        return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), len(boxes)

    # ------------------------------------------------------------------ rate limit
    def _allowed_now(self):
        c = self.cfg()
        with self.r.store.lock:
            rows = self.r.store.db.execute("SELECT ts FROM posts WHERE status='posted' AND ts > ? ORDER BY ts DESC", (time.time() - 86400,)).fetchall()
        if len(rows) >= c["max_posts_per_day"]:
            return False
        return not rows or time.time() - rows[0][0] > c["min_gap_minutes"] * 60

    def channels(self):
        return {"bluesky": bool(os.environ.get("BLUESKY_HANDLE") and os.environ.get("BLUESKY_APP_PASSWORD")),
                "pinterest": bool(os.environ.get("PINTEREST_TOKEN") and os.environ.get("PINTEREST_BOARD_ID"))}

    def set_creds(self, data):
        allowed = ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD", "PINTEREST_TOKEN", "PINTEREST_BOARD_ID")
        cur = {}
        if ENV.exists():
            for line in ENV.read_text().splitlines():
                k, _, v = line.partition("=")
                cur[k.strip()] = v.strip()
        cur.update({k: str(v).strip() for k, v in data.items() if k in allowed and str(v).strip()})
        ENV.write_text("".join(f"{k}={v}\n" for k, v in cur.items()))
        ENV.chmod(0o600)
        load_env()
        return self.channels()

    def _row(self, r):
        keys = ("id", "ts", "kind", "text", "image", "alt", "status", "privacy", "bsky_uri", "pin_id", "error", "video")
        return dict(zip(keys, r))

    def list(self, status=None, n=50):
        q = "SELECT id,ts,kind,text,image,alt,status,privacy,bsky_uri,pin_id,error,video FROM posts"
        args = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        with self.r.store.lock:
            rows = self.r.store.db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, n)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, pid):
        with self.r.store.lock:
            r = self.r.store.db.execute("SELECT id,ts,kind,text,image,alt,status,privacy,bsky_uri,pin_id,error,video FROM posts WHERE id=?", (pid,)).fetchone()
        return self._row(r) if r else None

    def _update(self, pid, **kw):
        with self.r.store.lock:
            self.r.store.db.execute(f"UPDATE posts SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), pid))
            self.r.store.db.commit()

    # ------------------------------------------------------------------ AI helpers (privacy + captions)
    def _ai_available(self):
        return bool(self.r.brain.online)

    def _ai_json(self, prompt, image=None, schema=None, max_tokens=None):
        b = self.r.brain
        k, m = b.active()
        if m.get("provider") == "openrouter" or not b.client:
            model = m.get("model") if m.get("provider") == "openrouter" else "anthropic/claude-haiku-5.5"
            if image is not None and m.get("vision") is False:
                model = "anthropic/claude-haiku-5.5"          # the chosen brain cannot see pictures: use one that can
            return self._ai_openrouter(prompt, image, schema, model, max_tokens or 3000)
        model = m.get("model") if m.get("provider") == "anthropic" else "claude-opus-5-5"
        content = [{"type": "text", "text": prompt}]
        if image is not None:
            from .brain import jpeg_b64
            content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": jpeg_b64(image, 1280)}})
        kw = dict(model=model, max_tokens=max_tokens or 4000, messages=[{"role": "user", "content": content}], output_config={"effort": "low"})
        if schema:
            kw["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        resp = b.client.messages.create(**kw)
        if resp.stop_reason == "refusal":
            return None
        text = "".join(x.text for x in resp.content if x.type == "text")
        return json.loads(text) if schema else text

    def _ai_openrouter(self, prompt, image, schema, model, max_tokens=3000):
        if not os.environ.get("OPENROUTER_API_KEY"):
            return None
        content = [{"type": "text", "text": prompt + (f"\nReply with ONLY one JSON object in this form: {json_example(schema)}" if schema else "")}]
        if image is not None:
            from .brain import jpeg_b64
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + jpeg_b64(image, 1280)}})
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", timeout=90 if max_tokens <= 3000 else 240,
                          headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "X-Title": "ai-multi-bot social"},
                          json={"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens,
                                "reasoning": {"effort": "low"}})   # thinking models used all of 800 tokens and returned no text
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if not schema:
            return text
        return first_json(text, schema.get("required", ()))

    def privacy_check(self, text, image=None, screen=False, faces_ok=False):
        """Returns (safe, reason). Photos are never posted without an AI check. screen=True: the picture is the robot's own screen.
        faces_ok=True: the owner approved an unblurred photo, so faces are allowed; nudity is still rejected."""
        if re.search(r"\b\d{1,5}\s+\w+\s+(street|st|ave|avenue|road|rd|lane|ln|drive|dr|blvd)\b", text, re.I):
            return False, "text looks like it contains an address"
        if not self._ai_available():
            return (image is None and not self.cfg()["autonomous"]), "no AI check available (photos and autonomous posts need it)"
        schema = {"type": "object", "properties": {"safe": {"type": "boolean"}, "reason": {"type": "string"}},
                  "required": ["safe", "reason"], "additionalProperties": False}
        rules = (UNBLURRED_RULES if faces_ok else PRIVACY_RULES) + (" " + SCREEN_RULES if screen else "")
        res = None
        for attempt in range(2):                   # one retry: a flaky reply should not block a good post
            try:
                res = self._ai_json(f"You are a {'fair' if faces_ok else 'strict'} privacy reviewer for a public social media post by a home robot. Rules: {rules}\n"
                                    f"Post text: {text!r}\nIs this post (and the attached picture, if any) safe to publish? "
                                    + ("Apply only the rules above." if faces_ok else "Be conservative."),
                                    image, schema)
            except Exception as e:
                res = None
                self.r.log(f"privacy check error: {e}", "warn")
            if res and "safe" in res:
                break
        if not res or "safe" not in res:
            return False, "privacy check unavailable"
        return bool(res["safe"]), str(res.get("reason", ""))

    def nudity_check(self, image):
        """Nudity only, for any photo the robot keeps (moments). Fails closed: no AI means no keep. Returns (safe, reason)."""
        if not self._ai_available():
            return False, "no AI check available"
        schema = {"type": "object", "properties": {"safe": {"type": "boolean"}, "reason": {"type": "string"}},
                  "required": ["safe", "reason"], "additionalProperties": False}
        try:
            res = self._ai_json("You check a photo for a home robot. " + NUDITY_RULES + " Otherwise it is safe. Be conservative.",
                                image, schema)
        except Exception as e:
            return False, f"nudity check error: {e}"
        if not res or "safe" not in res:
            return False, "nudity check unavailable"
        return bool(res["safe"]), str(res.get("reason", ""))

    def write_caption(self, note, image=None):
        if not self._ai_available():
            return note
        recent = "\n".join(f"- {t[:160]}" for _pid, _ts, t in self.recent_posted(14, 12))
        prompt = ("Write a social media post (max 260 characters) in the voice of a small, curious, quirky robot learning about its world, "
                  f"from its own point of view. End with 1-3 hashtags such as {self.cfg()['hashtags']}. No names, places or personal details. "
                  "Plain text only. Do not retell anything your recent posts (below) already told: write about what is new."
                  + (f"\nYour recent posts:\n{recent}" if recent else "") + f"\nWhat happened: {note}")
        text = ""
        for _ in range(3):                         # empty or too long: try again, then fit the text
            try:
                text = (self._ai_json(prompt, image) or "").strip()
            except Exception as e:
                self.r.log(f"caption failed: {e}", "warn")
                text = ""
            if text and len(text) > 280:           # Bluesky allows 300; models often overshoot - ask for a shorter one
                prompt = (f"Shorten this robot social media post to at most 230 characters. Keep the voice and 1-2 hashtags. "
                          f"Reply with only the post.\n\n{text}")
                continue
            if text:
                return text
        return self._fit(text or note, 290)

    @staticmethod
    def _fit(text, n):
        """Cut at a sentence (or word) boundary instead of mid-word."""
        if len(text) <= n:
            return text
        cut = text[:n]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        return cut[:end + 1] if end > n // 2 else cut[:cut.rfind(" ")].rstrip(",;:") + "…"

    # ------------------------------------------------------------------ drafting
    def recent_posted(self, days=14, n=15):
        with self.r.store.lock:
            return self.r.store.db.execute("SELECT id, ts, text FROM posts WHERE status='posted' AND ts>? ORDER BY id DESC LIMIT ?",
                                           (time.time() - days * 86400, n)).fetchall()

    @staticmethod
    def _words(text):
        t = re.sub(r"#\w+", "", (text or "").lower())
        return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 4 and w not in REPEAT_STOP}

    def repeat_reason(self, text):
        """'' if the post is new, else why it repeats one of the last 2 weeks' posts (the robot once posted its first drive 5 times).
        Word overlap catches near-copies; a small AI check catches the same news told in other words."""
        recent = self.recent_posted()
        a = self._words(text)
        if not recent or not a:
            return ""
        for pid, _ts, t in recent:
            b = self._words(t)
            if b and len(a & b) / min(len(a), len(b)) >= 0.5:
                return f"repeats post #{pid} (same words)"
        if not self._ai_available():
            return ""
        listing = "\n".join(f"#{pid} ({time.strftime('%b %d', time.localtime(ts))}): {t[:220]}" for pid, ts, t in recent)
        schema = {"type": "object", "properties": {"repeat": {"type": "boolean"}, "post": {"type": "integer"}, "reason": {"type": "string"}},
                  "required": ["repeat", "post", "reason"], "additionalProperties": False}
        try:
            res = self._ai_json("A small home robot wants to publish a new social media post. Is it mainly a re-telling of news it already "
                                "posted below (for example 'I rolled on my wheels for the first time' again, or the same software update "
                                "again)? A new event of a similar kind (another game, a different photo, a different new feature, a new "
                                "day's diary with new things) is NOT a repeat. post = the number of the repeated post, or 0.\n"
                                f"Recent posts:\n{listing}\n\nNew post: {text!r}", None, schema)
        except Exception as e:
            self.r.log(f"repeat check failed: {e}", "warn", persist=False)
            return ""
        if res and res.get("repeat"):
            return f"repeats post #{res.get('post') or '?'}: {str(res.get('reason', ''))[:160]}"
        return ""

    def _offered_raw(self):
        try:
            items = json.loads(OFFERED.read_text())
            return items if isinstance(items, dict) else {}
        except (OSError, ValueError):
            return {}

    def offered(self):
        """Photos/videos the owner gave me for posts (data/social/offered.json, set with POST /api/social/offer):
        {name: {"about", "photo": file in data/photos, "video": .mp4 in data/social}}. Returns them with full paths, skips
        missing files, and adds "posted": <post id> once a post with that video went out (so it is not posted twice)."""
        out = {}
        for name, v in self._offered_raw().items():
            m = {"about": str(v.get("about", ""))[:500]}
            ok = True
            for key, base in (("photo", PHOTOS), ("video", SOCIAL_DIR)):
                if v.get(key):
                    f = (base / str(v[key])).resolve()
                    ok = ok and base.resolve() in f.parents and f.is_file()
                    m[key] = str(f)
            if not ok or not (m.get("photo") or m.get("video")):
                continue
            if m.get("video"):
                with self.r.store.lock:
                    row = self.r.store.db.execute("SELECT id FROM posts WHERE status='posted' AND video=? ORDER BY id DESC LIMIT 1",
                                                  (m["video"],)).fetchone()
                if row:
                    m["posted"] = row[0]
            out[str(name)] = m
        return out

    def offer(self, name, about="", photo="", video="", remove=False):
        """Owner: add (or remove) media the brain may attach to a post by name. Files must already be in data/photos
        (photo) and data/social (.mp4 video)."""
        name = re.sub(r"[^a-z0-9_-]", "", str(name).lower())[:40]
        if not name:
            raise ValueError("name needed (letters, digits, - or _)")
        items = self._offered_raw()
        if remove:
            items.pop(name, None)
        else:
            if photo and not ((PHOTOS / photo).resolve().is_file() and PHOTOS.resolve() in (PHOTOS / photo).resolve().parents):
                raise ValueError("photo must be a file in data/photos")
            v = (SOCIAL_DIR / video).resolve() if video else None
            if v and not (v.is_file() and v.suffix.lower() == ".mp4" and SOCIAL_DIR.resolve() in v.parents):
                raise ValueError("video must be an .mp4 file in data/social")
            if not (photo or video):
                raise ValueError("give a photo, a video or both")
            items[name] = {"about": str(about)[:500], "photo": photo, "video": video}
        SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
        OFFERED.write_text(json.dumps(items, indent=1))
        return self.offered()

    def draft(self, note, image=None, kind="update", caption_ready=False, video=None, screen=False, owner_photo=False,
              check_repeat=True, people_ok=False, hold=False, speech=None):
        """Create a draft post. hold=True: never posted without the owner's Post tap. speech: what is said in the video
        (checked with the text). image: PIL image or None; video: path of an MP4 (the image is then its preview).
        screen=True: the picture/video is the robot's own touchscreen. owner_photo=True: a photo the owner chose (only from
        the owner's web/API request): the face blur is skipped because it mistakes the robot's screen face for a person; the
        AI privacy check still runs. people_ok=True: the owner said the people in this photo may be posted: no blur, the
        check allows people (never nudity), and it waits for the owner's Post tap (config people_tap). Returns the post dict."""
        faces = 0
        unblurred = 0
        people_ok = bool(people_ok and image is not None and not screen)
        if people_ok:
            unblurred = max(1, self.blur_faces(image)[1])   # counted only; the tap is needed even if the detector saw none
        elif image is not None and not screen and not owner_photo:
            image, faces = self.blur_faces(image)      # faces are blurred BEFORE the AI or anyone else sees the photo
        elif image is not None and owner_photo:
            unblurred = self.blur_faces(image)[1]      # owner's own photo: faces stay, so it needs the owner's Post tap
        text = note if caption_ready else self.write_caption(note, image)
        rep = self.repeat_reason(text) if check_repeat else ""
        if rep:                                        # not posted: same news as a recent post
            with self.r.store.lock:
                cur = self.r.store.db.execute("INSERT INTO posts(ts,kind,text,image,alt,status,privacy,video) VALUES(?,?,?,?,?,?,?,?)",
                                              (time.time(), kind, text, "", "", "repeat", rep, video or ""))
                self.r.store.db.commit()
            self.r.log(f"social draft #{cur.lastrowid} skipped, {rep}: {text[:60]}")
            return self.get(cur.lastrowid)
        check_img = image
        if video:
            check_img = self._video_sheet(video) or image   # check several moments of the video, not just one frame
            if owner_photo and not people_ok and check_img is not image:
                unblurred = max(unblurred, self.blur_faces(check_img.copy())[1])   # a face in ANY of the moments counts
        check_text = text + (f"\n[Said in the attached video (sound): {speech!r}]" if speech else "")
        safe, reason = self.privacy_check(check_text, check_img, screen=screen, faces_ok=bool(unblurred))
        if people_ok:
            reason = "UNBLURRED: the owner said the people in this photo are OK; " + reason
        elif unblurred:
            reason = f"UNBLURRED: {unblurred} face(s) left in; owner must tap Post; " + reason
        if faces:
            reason = f"{faces} face(s) blurred; " + reason
        elif owner_photo and image is not None:
            reason = "owner's photo (face blur skipped); " + reason
        if hold:
            reason = "from the shared folder: the owner taps Post; " + reason
        img_path, alt = "", ""
        if image is not None:
            POST_IMG.mkdir(parents=True, exist_ok=True)
            im = image.convert("RGB")
            im.thumbnail((1600, 1600))
            img_path = str(POST_IMG / time.strftime("post_%Y%m%d_%H%M%S.jpg"))
            im.save(img_path, "JPEG", quality=85)
            what = "screenshot of the robot's own screen" if screen else "photo taken by the robot"
            try:
                alt = (self._ai_json(f"Describe this {'screenshot' if screen else 'photo'} in one plain sentence for alt text "
                                     "(no names or personal details).", image) or what)[:300] if self._ai_available() else what
            except Exception:
                alt = what
        status = "draft" if safe else "blocked"
        with self.r.store.lock:
            cur = self.r.store.db.execute("INSERT INTO posts(ts,kind,text,image,alt,status,privacy,video) VALUES(?,?,?,?,?,?,?,?)",
                                          (time.time(), kind, text, img_path, alt, status, reason, video or ""))
            self.r.store.db.commit()
            pid = cur.lastrowid
        self.r.log(f"social draft #{pid} ({status}): {text[:80]}")
        if not safe:
            return self.get(pid)
        if people_ok and not self.cfg()["people_tap"]:      # the owner switched the extra tap off: their spoken OK is enough
            unblurred = 0
        if self.cfg()["autonomous"] and not unblurred and not hold:   # unblurred faces are never posted on their own
            if self._allowed_now():
                threading.Thread(target=self.publish, args=(pid,), daemon=True).start()
            else:
                self._update(pid, status="queued")      # posted later by tick() when the rate limit allows
        else:
            where = ", ".join(k for k, v in self.channels().items() if v) or "no accounts yet"
            self.r.cyd.send({"t": "ask", "id": f"social:{pid}", "title": "Share this?", "opts": ["Post", "Later", "Discard"],
                             "q": f"{text}\n{'(with a photo) ' if img_path else ''}-> {where}"[:600]})
        return self.get(pid)

    def _on_answer(self, aid, choice):
        if aid.startswith("social:"):
            pid = int(aid.split(":", 1)[1])
            if choice == "Post":
                threading.Thread(target=self.publish, args=(pid,), daemon=True).start()
            elif choice == "Discard":
                self._update(pid, status="discarded")

    # ------------------------------------------------------------------ publishing
    def publish(self, pid, text=None):
        """Post a draft. Only ONE caller can post a given draft: the status is claimed ("publishing") first. Before
        2026-11-13 the minute tick, a tap on Post and the web app could all pass the status check while a slow video upload
        was still running, and the same post went to Bluesky twice."""
        with self.r.store.lock:
            cur = self.r.store.db.execute("UPDATE posts SET status='publishing' WHERE id=? AND status IN ('draft','failed','queued')", (int(pid),))
            self.r.store.db.commit()
            claimed = cur.rowcount == 1
        if not claimed:
            return False, "not a publishable draft (or it is being posted right now)"
        try:
            return self._publish(pid, text)
        except Exception as e:                       # never leave it stuck in "publishing"
            self._update(pid, status="failed", error=f"posting crashed: {type(e).__name__}: {e}"[:300])
            self.r.log(f"social post #{pid} FAILED: {type(e).__name__}: {e}", "warn")
            return False, f"posting failed: {e}"

    def _publish(self, pid, text=None):
        p = self.get(pid)
        if text:
            unblurred = (p.get("privacy") or "").startswith("UNBLURRED")
            safe, reason = self.privacy_check(text, Image.open(p["image"]) if p["image"] else None, faces_ok=unblurred)
            if not safe:
                self._update(pid, status="blocked", privacy=reason)
                return False, f"edited text blocked: {reason}"
            self._update(pid, text=text)
            p["text"] = text
        ch = self.channels()
        errors, bsky, pin = [], None, None
        img_bytes = open(p["image"], "rb").read() if p["image"] else None
        if ch["bluesky"]:
            video = p.get("video") if p.get("video") and os.path.exists(p.get("video")) else None
            try:
                bsky = self._bluesky(p["text"], img_bytes, p["alt"], video)
            except Exception as e:
                if video:                           # e.g. the account email is not verified (needed for video): post the picture
                    self.r.log(f"bluesky video failed ({e}); posting the picture instead", "warn")
                    try:
                        bsky = self._bluesky(p["text"], img_bytes, p["alt"])
                        errors.append(f"video failed, posted the picture: {e}")
                    except Exception as e2:
                        errors.append(f"bluesky: {e2}")
                else:
                    errors.append(f"bluesky: {e}")
        if ch["pinterest"] and img_bytes:
            try:
                pin = self._pinterest(p["text"], img_bytes, p["alt"])
            except Exception as e:
                errors.append(f"pinterest: {e}")
        ok = bool(bsky or pin) or not any(ch.values())   # with no accounts, "posted" means: on the public page only
        if ok:
            threading.Thread(target=self.push_stats, args=(True,), daemon=True).start()
        self._update(pid, status="posted" if ok else "failed", bsky_uri=bsky or "", pin_id=pin or "", error="; ".join(errors))
        self.r.log(f"social post #{pid} {'published' if ok else 'FAILED'} {errors or ''}")
        self.r.toast(("Posted: " + p["text"][:30]) if ok else "Posting failed")
        return ok, "; ".join(errors) or "posted"

    def _bluesky(self, text, img, alt, video=None):
        pds = os.environ.get("BLUESKY_PDS", "https://bsky.social")
        s = requests.post(f"{pds}/xrpc/com.atproto.server.createSession", timeout=30,
                          json={"identifier": os.environ["BLUESKY_HANDLE"], "password": os.environ["BLUESKY_APP_PASSWORD"]})
        s.raise_for_status()
        sess = s.json()
        auth = {"Authorization": f"Bearer {sess['accessJwt']}"}
        rec = {"$type": "app.bsky.feed.post", "text": text[:300], "langs": ["en"],
               "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        facets = []
        for m in re.finditer(r"#(\w+)", rec["text"]):
            facets.append({"index": {"byteStart": len(rec["text"][:m.start()].encode()), "byteEnd": len(rec["text"][:m.end()].encode())},
                           "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)}]})
        if facets:
            rec["facets"] = facets
        if video:
            blob, (vw, vh) = self._bsky_video(sess, auth, pds, video)
            rec["embed"] = {"$type": "app.bsky.embed.video", "video": blob, "alt": alt[:1000] or "video of the robot's screen",
                            "aspectRatio": {"width": vw, "height": vh}}
            img = None
        if img:
            im = Image.open(io.BytesIO(img))
            q = 85
            while len(img) > 950_000 and q > 40:
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=q)
                img, q = buf.getvalue(), q - 10
            up = requests.post(f"{pds}/xrpc/com.atproto.repo.uploadBlob", data=img, timeout=60, headers={**auth, "Content-Type": "image/jpeg"})
            up.raise_for_status()
            rec["embed"] = {"$type": "app.bsky.embed.images", "images": [
                {"alt": alt, "image": up.json()["blob"], "aspectRatio": {"width": im.width, "height": im.height}}]}
        r = requests.post(f"{pds}/xrpc/com.atproto.repo.createRecord", headers=auth, timeout=30,
                          json={"repo": sess["did"], "collection": "app.bsky.feed.post", "record": rec})
        r.raise_for_status()
        return r.json()["uri"]

    def update_profile(self, display_name=None, description=None, avatar=None):
        """Change the Bluesky profile (only on the owner's request). Fields left as None stay as they are.
        avatar: PIL image, centre-cropped to a square. Returns the new profile record (without the avatar blob)."""
        pds = os.environ.get("BLUESKY_PDS", "https://bsky.social")
        s = requests.post(f"{pds}/xrpc/com.atproto.server.createSession", timeout=30,
                          json={"identifier": os.environ["BLUESKY_HANDLE"], "password": os.environ["BLUESKY_APP_PASSWORD"]})
        s.raise_for_status()
        sess = s.json()
        auth = {"Authorization": f"Bearer {sess['accessJwt']}"}
        cur = requests.get(f"{pds}/xrpc/com.atproto.repo.getRecord", headers=auth, timeout=30,
                           params={"repo": sess["did"], "collection": "app.bsky.actor.profile", "rkey": "self"})
        cur = cur.json() if cur.ok else {}
        rec = cur.get("value") or {"$type": "app.bsky.actor.profile"}   # everything not changed here is kept
        if display_name is not None:
            rec["displayName"] = str(display_name)[:64]
        if description is not None:
            rec["description"] = str(description)[:256]
        if avatar is not None:
            im = avatar.convert("RGB")
            side = min(im.size)
            im = im.crop(((im.width - side) // 2, (im.height - side) // 2, (im.width + side) // 2, (im.height + side) // 2))
            buf = io.BytesIO()
            im.resize((1000, 1000), Image.LANCZOS).save(buf, "JPEG", quality=88)   # avatars must be under 1 MB
            up = requests.post(f"{pds}/xrpc/com.atproto.repo.uploadBlob", data=buf.getvalue(), timeout=60,
                               headers={**auth, "Content-Type": "image/jpeg"})
            up.raise_for_status()
            rec["avatar"] = up.json()["blob"]
        body = {"repo": sess["did"], "collection": "app.bsky.actor.profile", "rkey": "self", "record": rec}
        if cur.get("cid"):
            body["swapRecord"] = cur["cid"]
        r = requests.post(f"{pds}/xrpc/com.atproto.repo.putRecord", headers=auth, json=body, timeout=30)
        r.raise_for_status()
        changed = [k for k, v in (("name", display_name), ("bio", description), ("picture", avatar)) if v is not None]
        self.r.log("social: Bluesky profile changed (" + ", ".join(changed) + ")")
        return {k: v for k, v in rec.items() if k in ("displayName", "description")}

    def set_avatar(self, image):
        return self.update_profile(avatar=image)

    def _pinterest(self, text, img, alt):
        r = requests.post("https://api.pinterest.com/v5/pins", timeout=60,
                          headers={"Authorization": f"Bearer {os.environ['PINTEREST_TOKEN']}"},
                          json={"board_id": os.environ["PINTEREST_BOARD_ID"], "title": re.sub(r"#\w+", "", text).strip()[:100],
                                "description": text[:500], "alt_text": alt[:500],
                                "media_source": {"source_type": "image_base64", "content_type": "image/jpeg",
                                                 "data": base64.b64encode(img).decode()}})
        r.raise_for_status()
        return r.json().get("id", "")

    # ------------------------------------------------------------------ autonomy: queue + exploring
    def tick(self):
        """Called every minute: publish a queued post when allowed; at the day's random times post a moment or something new."""
        if not self.busy.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self.busy.release()

    def _tick(self):
        c = self.cfg()
        if time.time() - self._stats_ts > 1800:
            self.push_stats(True)
        if not getattr(self, "_updates_checked", False):   # once per start: new commits = new features to show off
            self._updates_checked = True
            self.check_updates()
        if not c["autonomous"]:
            return
        if self._allowed_now():
            q = self.list("queued", 1)
            if q:
                self.publish(q[-1]["id"])
                return
        if not (self.channels()["bluesky"] and self._ai_available()):
            return
        now = time.time()
        urgent = [m for m in self._moments() if m.get("soon") and not m.get("done") and m["after"] <= now and now - m["ts"] < 6 * 3600]
        plan = self._plan()
        due = [t for t in plan["slots"] if t <= now and t not in plan["used"]]
        if not (urgent or due) or not self._allowed_now():
            return
        if due:
            plan["used"] += due                     # a missed time is skipped, not saved up
            self.r.store.kv_set("social_plan", plan)
        self.post_something(urgent[0] if urgent else None)

    # ------------------------------------------------------------------ moments + random posting times
    def _moments(self):
        return self.r.store.kv_get("social_moments", []) or []

    def moment(self, kind, note, image=None, video=None, score=1, key=None, soon=False):
        """Remember something worth sharing (kind: game, feature, firmware, screen...). The scheduler posts the best fresh
        moment at the next random posting time; score >= 3 or soon=True goes out about 5-30 minutes later instead."""
        if kind == "feature" and not self.cfg().get("auto_post_updates", True):
            return
        SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
        img_path = ""
        if image is not None:
            img_path = str(SOCIAL_DIR / time.strftime(f"moment_%Y%m%d_%H%M%S_{random.randint(0, 999)}.png"))
            image.convert("RGB").save(img_path)
        now, key = time.time(), key or kind
        soon = bool(soon or score >= 3)
        with self.lock:
            old = [m for m in self._moments() if m["key"] == key and not m.get("done")]
            if old and kind == "feature":                # several updates before the post went out: tell all of them
                note = old[-1]["note"] + " Also: " + note.split(": ", 1)[-1]
                img_path, video = img_path or old[-1]["image"], video or old[-1]["video"]
            ms = [m for m in self._moments() if not (m["key"] == key and not m.get("done")) and now - m["ts"] < 3 * 86400]
            ms.append({"ts": now, "kind": kind, "key": key, "note": note[:700], "image": img_path, "video": video or "",
                       "score": score, "soon": soon, "after": now + (random.uniform(300, 1800) if soon else 0)})
            self.r.store.kv_set("social_moments", ms[-40:])
        self.r.log(f"social moment ({kind}, score {score}{', soon' if soon else ''}): {note[:80]}", persist=False)

    def _mark_done(self, m):
        with self.lock:
            ms = self._moments()
            for x in ms:
                if x["ts"] == m["ts"] and x["key"] == m["key"]:
                    x["done"] = True
            self.r.store.kv_set("social_moments", ms)

    def _plan(self):
        """Today's random posting times (local time)."""
        c = self.cfg()
        day = time.strftime("%Y-%m-%d")
        plan = self.r.store.kv_get("social_plan") or {}
        if plan.get("day") != day:
            lo, hi = c.get("posts_per_day", [3, 5])
            n = random.randint(int(lo), int(hi))
            h0, h1 = c.get("post_hours", [8.5, 21.5])
            midnight = time.mktime(time.strptime(day, "%Y-%m-%d"))
            slots = []
            for _ in range(300):
                if len(slots) >= n:
                    break
                t = midnight + random.uniform(float(h0), float(h1)) * 3600
                if all(abs(t - x) > 80 * 60 for x in slots):
                    slots.append(t)
            plan = {"day": day, "slots": sorted(slots), "used": [x for x in slots if x < time.time()]}
            self.r.store.kv_set("social_plan", plan)
            self.r.log("social: today's posting times " + ", ".join(time.strftime("%H:%M", time.localtime(x)) for x in plan["slots"]),
                       persist=False)
        return plan

    def post_something(self, m=None):
        """Post the best fresh moment; with none, look around with the camera or show off the screen."""
        if m is None:
            fresh = [x for x in self._moments() if not x.get("done") and time.time() - x["ts"] < 36 * 3600]
            fresh.sort(key=lambda x: (x["score"], x["ts"]), reverse=True)
            m = fresh[0] if fresh else None
        if m:
            self._mark_done(m)
            img = Image.open(m["image"]) if m.get("image") and os.path.exists(m["image"]) else None
            return self.draft(m["note"], img, kind=m["kind"], video=m.get("video") or None,
                              screen=m["kind"] in ("game", "feature", "screen", "firmware", "art"))
        days = float(self.cfg().get("self_portrait_days", 3) or 0)
        if days and time.time() - float(self.r.store.kv_get("social_portrait_ts") or 0) > days * 86400 and random.random() < 0.35:
            res = self.self_portrait()
            if res and res.get("post"):
                return res["post"]
        if self.cfg().get("show_off", True) and random.random() < 0.4:
            res = self.show_off()
            if res:
                return res
        return self.explore()

    def show_off(self, app=None, video=True):
        """Post a screenshot of the robot's face, or a short video of it playing a RoboOS game against itself."""
        from .screencap import encode_mp4
        if app is None and self.r.cyd.page == "face" and random.random() < 0.4:
            img = self.r.screencap.public_screenshot()
            if img is not None:
                return self.draft("This is a screenshot of my own face on my little touchscreen right now. Share how you feel today "
                                  "or something you did.", img, kind="screen", screen=True)
        app = app or random.choice(["checkers", "tictactoe", "mines"])
        frames = self.r.desktop.demo_clip(app)
        last = frames[-1][1]
        out = None
        if video:
            SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
            out = encode_mp4(frames, SOCIAL_DIR / time.strftime(f"demo_{app}_%Y%m%d_%H%M%S.mp4"))
        note = {"checkers": "I played a game of checkers against myself on RoboOS, the tiny Windows-95-style desktop on my touchscreen.",
                "tictactoe": "I played tic-tac-toe against myself on RoboOS, my tiny desktop. Spoiler: it is hard to beat yourself.",
                "mines": "I played RoboMines (find the mines) on RoboOS, my tiny desktop. I may have peeked where the mines are."}[app]
        return self.draft(note + " Humans can play against me too.", last, kind="screen", video=str(out) if out else None, screen=True)

    def draw(self, subject=None, photo=None, post=True):
        """Draw anything in RoboOS Paint (recorded stroke by stroke): `subject` in words, and/or what a camera `photo` shows.
        Returns {"idea", "png", "mp4", "post"} or None. See portrait.py."""
        from . import portrait
        res = portrait.make(self.r, subject, photo)
        if not res:
            self.r.toast("The drawing did not work out", 3000)
            return None
        img = res.pop("image")
        from . import files
        where = files.rel(res["png"])
        self.r.toast("Drawing finished: " + where, 4000)
        self.r.log(f"drawing saved as {where} (web app: Media > Files)")
        try:
            self.r.journal.write("robot", "(note to self)", "I finished a drawing" + (f" of {subject[:80]}" if subject else " from a photo") + f": it is saved as {where}.")
        except Exception:
            pass
        if post:
            note = ("I drew a picture in Paint on RoboOS, my tiny desktop" +
                    (", from a photo I had just taken with my camera" if photo is not None else "") +
                    (f". The subject: {subject}. " if subject else ". ") + res["idea"])
            res["post"] = self.draft(note, img, kind="art", video=res["mp4"], screen=True)
        return res

    def self_portrait(self, post=True):
        """Draw what I think I look like right now in RoboOS Paint (recorded stroke by stroke) and post the video.
        Returns {"idea", "png", "mp4", "post"} or None. See portrait.py."""
        from . import portrait
        res = portrait.make(self.r)
        if not res:
            return None
        self.r.store.kv_set("social_portrait_ts", time.time())
        img = res.pop("image")
        if post:
            note = ("I drew a self-portrait in Paint on RoboOS, my tiny desktop: what I think I look like right now. I have never seen "
                    "myself (my only camera looks outwards), so I imagined it from my list of parts. " + res["idea"])
            res["post"] = self.draft(note, img, kind="art", video=res["mp4"], screen=True)
        return res

    def check_updates(self):
        """After a restart: new commits = new features. Tell the world (with a demo video when it is a game)."""
        try:
            head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return
        last = self.r.store.kv_get("social_seen_commit")
        if not head or head == last:
            return
        self.r.store.kv_set("social_seen_commit", head)
        if not last:
            return                                   # first run: nothing to compare with
        log = subprocess.run(["git", "-C", str(ROOT), "log", "--no-merges", "--format=%s", f"{last}..{head}"],
                             capture_output=True, text=True, timeout=10).stdout
        subjects = [re.sub(r"^\[robodev\]\s*", "", x.strip()) for x in log.splitlines()]   # RoboDev's own commits count too
        subjects = [x for x in subjects                       # bug fixes, docs, chores and RoboDev's restart commits are not news
                    if x and not re.match(r"(wip|fixup|merge|fix|fixes|fixed|docs?|chore|refactor|tests?)\b", x, re.I)][:12]
        if not subjects:
            return
        note = "My code was just updated. New things I can do now (from the change list): " + "; ".join(subjects)
        app = next((a for a, pat in (("checkers", r"checkers|games?\b"), ("mines", r"mines"), ("tictactoe", r"tic.?tac"))
                    if any(re.search(pat, x, re.I) for x in subjects)), None)

        def go():
            from .screencap import SHOTS, encode_mp4
            img, vid = None, None
            try:
                if app:
                    frames = self.r.desktop.demo_clip(app)
                    img = frames[-1][1]
                    SHOTS.mkdir(parents=True, exist_ok=True)
                    vid = encode_mp4(frames, SHOTS / time.strftime(f"feature_{app}_%Y%m%d_%H%M%S.mp4"))
                else:
                    img = self.r.screencap.public_screenshot()
            except Exception as e:
                self.r.log(f"feature demo failed: {e}", "warn")
            self.moment("feature", note, image=img, video=str(vid) if vid else None, score=3, key="feature")
        threading.Thread(target=go, daemon=True).start()

    # ------------------------------------------------------------------ video
    def upload_arrived(self, path):
        """The owner uploaded a file to the shared folder. A photo or video: Pip looks at it, says what it thinks, and
        posts it if it likes it (owner, 2026-11-14: "I wanted pip to be able to see the videos I was uploading to it and be
        able to choose to post them to bsky if it liked them"). One at a time, in the background."""
        from . import files
        try:
            if not self.cfg()["uploads_look"] or files.kind(files.find(path)) not in ("image", "video"):
                return
        except Exception:
            return
        if not hasattr(self, "_uploads"):
            self._uploads, self._uploads_lock = [], threading.Lock()
        with self._uploads_lock:
            if path in self._uploads or len(self._uploads) >= 5:   # many at once: the first five
                return
            self._uploads.append(path)
            if len(self._uploads) > 1:
                return                                 # the running worker takes it next
        threading.Thread(target=self._look_at_uploads, daemon=True, name="look-uploads").start()

    def _look_at_uploads(self):
        while True:
            with self._uploads_lock:
                if not self._uploads:
                    return
                path = self._uploads[0]
            time.sleep(3)                              # let a batch of uploads finish first
            try:
                may = ("If you really like it, you may post it to Bluesky yourself with draft_social_post (media = that path); "
                       "only if you like it, and say whether you did." if self.cfg()["uploads_post"] else
                       "If you would like to post it, you can prepare a post with draft_social_post (media = that path); the "
                       "owner then taps Post.")
                reply = self.r.brain.ask(f"(The owner just uploaded {path} to your shared folder.) Look at it with view_file, "
                                         f"then tell the owner in one or two sentences what you see and what you think. {may}",
                                         src="upload")
                self.r.face("curious", reply[:200], hold=8000)
                self.r.voice.speak(reply)
            except Exception as e:
                self.r.log(f"looking at the upload {path} failed: {e!r}", "warn")
            finally:
                with self._uploads_lock:
                    self._uploads.pop(0)

    def shared_media(self, path):
        """A photo or video the owner put in the shared folder (data/files), made ready for a post.
        -> {"photo": PIL image, "video": path of an .mp4 in data/social or None, "about": text}. Raises ValueError."""
        from . import files
        try:
            p = files.find(path)
        except files.FileError as e:
            raise ValueError(str(e))
        if files.SHARED.resolve() not in p.resolve().parents:
            raise ValueError("only files in the shared folder (data/files) can be posted this way")
        k = files.kind(p)
        if k == "image":
            return {"photo": Image.open(p).convert("RGB"), "video": None, "about": f"photo {p.name} from the owner"}
        if k != "video":
            raise ValueError("that is not a photo or a video")
        secs, w, h, vcodec, acodec = files.video_info(p)
        speech = ""
        if not secs or secs > 175:
            raise ValueError(f"the video is {secs:.0f} s long; Bluesky takes up to about 3 minutes")
        if vcodec != "h264" or acodec not in ("", "aac"):
            raise ValueError(f"the video is {vcodec}/{acodec or 'no sound'}; I can only post H.264 video (.mp4 or .mov) - "
                             "converting it would take me many minutes")
        keep_sound = bool(acodec)
        if acodec:                                 # what is said in it goes through the privacy check too
            try:
                speech = self.r.voice.transcribe_file(p)
            except Exception as e:
                self.r.log(f"social: could not listen to {p.name} ({e}); posting it without sound", "warn")
                keep_sound = False
        SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
        out = SOCIAL_DIR / (re.sub(r"[^A-Za-z0-9_-]", "_", p.stem)[:60] + ("" if keep_sound else "_nosound") + ".mp4")
        if not out.exists() or out.stat().st_mtime < p.stat().st_mtime:
            import subprocess                      # same pictures and sound, only repacked as .mp4 (fast, no quality loss)
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p), "-map", "0:v:0"] + (["-map", "0:a:0?"] if keep_sound else [])
                               + ["-c", "copy", "-movflags", "+faststart", str(out)], capture_output=True, text=True, timeout=300)
            if r.returncode != 0 or not out.exists():
                out.unlink(missing_ok=True)
                raise ValueError("could not repack the video: " + (r.stderr or "")[-200:])
        if out.stat().st_size > 95 * 1024 * 1024:
            raise ValueError("the video is bigger than 95 MB")
        with self.r.store.lock:
            row = self.r.store.db.execute("SELECT id FROM posts WHERE status='posted' AND video=? ORDER BY id DESC LIMIT 1",
                                          (str(out),)).fetchone()
        if row:
            raise ValueError(f"that video already went out in post #{row[0]}")
        import cv2
        cap = cv2.VideoCapture(str(out))
        cap.set(cv2.CAP_PROP_POS_MSEC, secs * 500)
        ok, fr = cap.read()
        cap.release()
        if not ok:
            raise ValueError("could not read a frame of the video")
        return {"photo": Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)), "video": str(out), "speech": speech,
                "about": f"video {p.name} from the owner, {secs:.0f} s long" + ("" if keep_sound else ", without its sound")}

    def _video_sheet(self, path):
        """Four frames of a video side by side (for the privacy check)."""
        try:
            import cv2
            cap = cv2.VideoCapture(str(path))
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
            tiles = []
            for i in range(4):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (i + 0.5) / 4))
                ok, fr = cap.read()
                if ok:
                    tiles.append(Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)))
            cap.release()
            if not tiles:
                return None
            w, h = tiles[0].size
            sheet = Image.new("RGB", (w * 2, h * 2), (0, 0, 0))
            for i, t in enumerate(tiles):
                sheet.paste(t, ((i % 2) * w, (i // 2) * h))
            return sheet
        except Exception:
            return None

    def _bsky_video(self, sess, auth, pds, path):
        """Upload an MP4 through Bluesky's video service. Returns (blob, (width, height))."""
        import cv2
        cap = cv2.VideoCapture(str(path))
        size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640, int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 440)
        cap.release()
        endpoint = pds
        for svc in (sess.get("didDoc") or {}).get("service", []):
            if str(svc.get("id", "")).endswith("atproto_pds"):
                endpoint = svc.get("serviceEndpoint", pds)
        tok = requests.get(f"{endpoint}/xrpc/com.atproto.server.getServiceAuth", headers=auth, timeout=30,
                           params={"aud": f"did:web:{urlparse(endpoint).hostname}", "lxm": "com.atproto.repo.uploadBlob",
                                   "exp": int(time.time()) + 1800})
        tok.raise_for_status()
        up = requests.post("https://video.bsky.app/xrpc/app.bsky.video.uploadVideo", timeout=300,
                           params={"did": sess["did"], "name": Path(path).name}, data=Path(path).read_bytes(),
                           headers={"Authorization": f"Bearer {tok.json()['token']}", "Content-Type": "video/mp4"})
        js = up.json() if up.content else {}
        js = js.get("jobStatus", js)
        if up.status_code >= 400 and js.get("error") != "already_exists":
            raise RuntimeError(f"upload {up.status_code}: {js.get('message') or js.get('error')}")
        blob, job, end = js.get("blob"), js.get("jobId"), time.time() + 300
        while not blob and job and time.time() < end:
            time.sleep(3)
            st = requests.get("https://video.bsky.app/xrpc/app.bsky.video.getJobStatus", params={"jobId": job}, timeout=30).json()
            st = st.get("jobStatus", st)
            if st.get("state") == "JOB_STATE_FAILED":
                raise RuntimeError(f"processing failed: {st.get('error') or st.get('message')}")
            blob = st.get("blob")
        if not blob:
            raise RuntimeError("video processing timed out")
        return blob, size

    # ------------------------------------------------------------------ how is it going? (public Bluesky numbers)
    def stats(self, refresh=False):
        st = self.r.store.kv_get("social_stats") or {}
        handle = os.environ.get("BLUESKY_HANDLE")
        if not handle or (not refresh and time.time() - st.get("ts", 0) < 1800):
            return st
        api = "https://public.api.bsky.app/xrpc/"
        prof = requests.get(api + "app.bsky.actor.getProfile", params={"actor": handle}, timeout=20).json()
        feed = requests.get(api + "app.bsky.feed.getAuthorFeed", params={"actor": handle, "limit": 50, "filter": "posts_no_replies"},
                            timeout=20).json()
        mine = [f["post"] for f in feed.get("feed", []) if f.get("post", {}).get("author", {}).get("did") == prof.get("did")]
        best = max(mine, key=lambda x: x.get("likeCount", 0), default=None)
        st = {"ts": time.time(), "followers": prof.get("followersCount", 0), "following": prof.get("followsCount", 0),
              "posts": prof.get("postsCount", 0), "likes": sum(x.get("likeCount", 0) for x in mine),
              "reposts": sum(x.get("repostCount", 0) for x in mine), "replies": sum(x.get("replyCount", 0) for x in mine),
              "best": {"text": best["record"].get("text", "")[:120], "likes": best.get("likeCount", 0)} if best else None}
        self.r.store.kv_set("social_stats", st)
        return st

    def push_stats(self, refresh=False):
        """Refresh the numbers and show them in the corner of the face."""
        self._stats_ts = time.time()
        self.track.maybe_sweep()
        try:
            st = self.stats(refresh)
        except Exception as e:
            self.r.log(f"bluesky stats failed: {e}", "warn", persist=False)
            st = self.r.store.kv_get("social_stats") or {}
        if st:
            self.r.cyd.send({"t": "social", "likes": st.get("likes", 0), "followers": st.get("followers", 0), "posts": st.get("posts", 0)})
        return st

    def explore(self):
        """Take a photo, blur faces, and post only if something fun is in view."""
        try:
            img = self.r.cam.still(1280, 720)
        except Exception as e:
            self.r.log(f"explore: camera busy ({e})", persist=False)
            return None
        img, faces = self.blur_faces(img)
        idea = self._ai_json("You are a small, curious, quirky robot looking through your own camera. If something in this photo is fun or "
                             "interesting to share publicly (a pet, a cool object, nice light, the weather, something you learned), write a "
                             f"post of at most 260 characters from your point of view, ending with 1-3 hashtags like {self.cfg()['hashtags']}. "
                             "If nothing is interesting, or the photo shows a person, any nudity, or personal information, reply exactly SKIP.", img)
        if not idea or idea.strip().upper().startswith("SKIP"):
            self.r.log("explore: nothing worth posting", persist=False)
            return None
        return self.draft(idea.strip()[:290], img, kind="explore", caption_ready=True)

    # ------------------------------------------------------------------ daily diary
    def daily_diary(self):
        if not self._ai_available():
            return None
        rows = [e for e in self.r.store.recent_events(200) if time.time() - e[0] < 86400 and e[1] in ("info", "heard", "ai_reply", "bump", "touch", "dev_agent")]
        notes = "; ".join(e[2] for e in rows[:60])
        return self.draft(f"Things that happened to me today (pick one fun, harmless thing to share): {notes[:3000]}", kind="diary")
