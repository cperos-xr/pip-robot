"""HTTP server: the web app, the public page and a JSON API for agents (Claude Code, Hermes, MCP, scripts).

Auth: an owner/guest session cookie (password login, QR pairing from the touchscreen, guest invite links)
or the API token from data/api_token (header X-Robot-Token or ?token=). GET needs guest or owner,
POST (anything that acts) needs owner. /public and /public/* are open but only show what the owner allows."""
import io
import json
import re
import secrets
import shutil
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

from . import health, tests
from .core import DATA, PHOTOS
from .webauth import Throttle, WebAuth

WEB = Path(__file__).parent / "web"
AUDIO_TYPES = {".m4a": "audio/mp4", ".aac": "audio/aac", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
               ".opus": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac", ".webm": "audio/webm"}
_HASHES = {}


def photo_hash(f):
    """64-bit difference hash as 16 hex digits (cached by mtime): near-identical photos differ in only a few bits."""
    key, mt = str(f), f.stat().st_mtime
    if _HASHES.get(key, (None,))[0] != mt:
        try:
            im = Image.open(f)
            im.draft("L", (80, 60))                   # fast JPEG decode at reduced size
            px = list(im.convert("L").resize((9, 8)).getdata())
            h = sum(1 << i for i in range(64) if px[(i // 8) * 9 + i % 8 + 1] > px[(i // 8) * 9 + i % 8])
            _HASHES[key] = (mt, f"{h:016x}")
        except Exception:
            _HASHES[key] = (mt, "")
    return _HASHES[key][1]
FEED_KINDS = ("heard", "said", "ai_reply", "touch", "bump", "tilt", "ocr", "motion")
MOOD_NAMES = ["idle", "happy", "curious", "thinking", "warning", "sleepy", "surprised", "sad", "booting", "love"]


def load_token():
    p = DATA / "api_token"
    if not p.exists():
        p.write_text(secrets.token_hex(12))
        p.chmod(0o600)
    return p.read_text().strip()


class Api(threading.Thread):
    def __init__(self, robot):
        super().__init__(daemon=True, name="api")
        self.r = robot
        self.token = load_token()
        self.auth = WebAuth(robot)
        self.throttle = Throttle()             # slows down password and pairing-code guessing
        self.answers = {}
        self.cv = threading.Condition()
        self.web_live_t = 0
        self.web_live_on = False
        self.live_lock = threading.Lock()
        robot.bus.on("answer", self._on_answer)

    # ---------- owner questions on the touchscreen ----------
    def _on_answer(self, aid, choice):
        with self.cv:
            self.answers[aid] = choice
            self.cv.notify_all()

    def ask_owner(self, title, q, opts, timeout=120):
        aid = "api:" + uuid.uuid4().hex[:8]
        self.r.cyd.send({"t": "ask", "id": aid, "title": title, "q": q, "opts": opts[:3]})
        self.r.log(f"asking owner: {title} - {q}")
        with self.cv:
            end = time.time() + timeout
            while aid not in self.answers and time.time() < end:
                self.cv.wait(end - time.time())
            return self.answers.pop(aid, None)

    # ---------- helpers used by robotd ----------
    def base_url(self):
        pub = (self.r.cfg.get("web") or {}).get("public_url")
        if pub:
            return pub.rstrip("/")
        ips = self.r.sys.ips()
        ip = ips.get("wlan0") or ips.get("end0") or "robot.local"
        return f"http://{ip}:{self.r.cfg['api']['port']}"

    def pairing_qr(self):
        code = self.auth.new_code("owner", 5)
        self.r.cyd.send({"t": "qr", "text": f"{self.base_url()}/pair?c={code}", "title": "Scan to pair your phone",
                         "sub": f"code {code} - 5 min"})
        self.r.log("pairing code shown on the screen (expires in 5 min)")

    def live_frame(self):
        """Latest camera frame for web viewers; starts the low-res stream on demand, stops it when idle."""
        self.web_live_t = time.time()
        with self.live_lock:                       # many viewer requests arrive at once: start one reaper only
            if not self.web_live_on:
                self.web_live_on = True
                self.r.cam.start_live("web")
                threading.Thread(target=self._live_reaper, daemon=True).start()
        frame, _ = self.r.cam.next_frame(time.time() - 1.0, timeout=2.0)
        return frame or self.r.cam.latest

    def _live_reaper(self):
        while time.time() - self.web_live_t < 8:   # web page stopped asking for frames -> camera off
            time.sleep(1)
        with self.live_lock:
            self.r.cam.stop_live("web")
            self.web_live_on = False

    def run(self):
        api, r = self, self.r

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json", headers=None):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body, indent=1).encode()
                elif isinstance(body, str):
                    body = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")      # pairing links and ?token= never leak in a Referer
                if ctype.startswith("text/html"):
                    self.send_header("X-Frame-Options", "DENY")           # no other site may put this app in a frame
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _send_file(self, f, ctype, headers=None):
                """Send a file in chunks, honouring a single "Range: bytes=a-b" (iPhone Safari needs it for audio)."""
                size = f.stat().st_size
                a, b, code = 0, size - 1, 200
                m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        a, b = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
                    else:
                        a = max(0, size - int(m.group(2)))
                    b = min(b, size - 1)
                    if a > b:
                        return self._send(416, "", "text/plain", {"Content-Range": f"bytes */{size}"})
                    code = 206
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(b - a + 1))
                self.send_header("Accept-Ranges", "bytes")
                if code == 206:
                    self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                with open(f, "rb") as fh:
                    fh.seek(a)
                    left = b - a + 1
                    while left > 0:
                        chunk = fh.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

            def _redirect(self, to, cookie=None):
                h = {"Location": to}
                if cookie is not None:
                    h["Set-Cookie"] = cookie
                self._send(303, "", "text/plain", h)

            def _role(self, q):
                tok = self.headers.get("X-Robot-Token") or q.get("token", [""])[0]
                if tok and secrets.compare_digest(tok.encode("utf-8", "replace"), api.token.encode()):
                    return "owner"
                return api.auth.role_from_cookie(self.headers.get("Cookie"))

            def _client(self):
                """Who is asking, for the sign-in throttle. Through the Cloudflare tunnel every request arrives from
                cloudflared on this machine, so the visitor's own address is the one Cloudflare reports."""
                peer = self.client_address[0]
                if peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                    return self.headers.get("CF-Connecting-IP") or peer
                return peer

            def _cookie(self, role):
                val, age = api.auth.make_cookie(role)
                https = self.headers.get("X-Forwarded-Proto") == "https" or '"https"' in (self.headers.get("CF-Visitor") or "")
                secure = "; Secure" if https else ""       # came in over https (the tunnel): the cookie is never sent in clear
                return f"robot_session={val}; Max-Age={age}; Path=/; HttpOnly; SameSite=Lax{secure}"

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > 40_000_000:                 # attachments are capped at ~30 MB: drain and ignore anything bigger
                    while n > 0:
                        n -= len(self.rfile.read(min(n, 1 << 20)) or b"x" * n)
                    return b""
                return self.rfile.read(n) if n else b""

            def _page(self, name, code=200, **subs):
                html = (WEB / name).read_text()
                for k, v in subs.items():
                    html = html.replace("{{" + k + "}}", v)
                self._send(code, html, "text/html; charset=utf-8")

            # ------------------------------------------------------------------ GET
            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                p = u.path
                if p == "/login":
                    return self._page("login.html", ERR="")
                if p == "/logout":
                    return self._redirect("/login", "robot_session=; Max-Age=0; Path=/")
                if p == "/pair":
                    who = self._client()
                    wait = api.throttle.wait_s(who)
                    if wait:
                        return self._page("login.html", 429, ERR=f"Too many wrong tries. Wait {wait} s, then use a new pairing code.")
                    role = api.auth.redeem(q.get("c", [""])[0])
                    if not role:
                        api.throttle.fail(who)
                        return self._page("login.html", ERR="That pairing code is expired or already used.")
                    api.throttle.ok(who)
                    r.log(f"new {role} device paired via code")
                    r.toast("Phone paired!" if role == "owner" else "Guest joined")
                    return self._redirect("/", self._cookie(role))
                if p == "/public":
                    return self._page("public.html")
                if p == "/public/data":
                    pub = api.auth.public_sections()
                    out = {}
                    if pub.get("face"):
                        mood = int((r.cyd.info.get("hb") or {}).get("mood", 0))
                        out["face"] = {"mood": MOOD_NAMES[mood] if 0 <= mood < len(MOOD_NAMES) else "idle"}
                    if pub.get("status"):
                        st = r.sys.status()
                        out["status"] = {"Uptime": f"{st['up'] // 3600} h {st['up'] % 3600 // 60} min", "Brain temperature": f"{st['temp']} °C",
                                         "AI": r.brain.summary()["label"],
                                         "Self-tests passing": sum(1 for v in r.store.last_tests().values() if v["ok"])}
                    if pub.get("said"):
                        ev = [e for e in r.store.recent_events(30) if e[1] == "said"]
                        out["said"] = ev[0][2] if ev else ""
                    out["camera"] = bool(pub.get("camera"))
                    return self._send(200, out)
                if p == "/public/live.jpg":
                    if not api.auth.public_sections().get("camera"):
                        return self._send(403, {"error": "camera is private"})
                    f = api.live_frame()
                    return self._send(200, f, "image/jpeg") if f else self._send(503, {"error": "no frame"})
                if p == "/public/posts":
                    if not api.auth.public_sections().get("posts"):
                        return self._send(200, [])
                    return self._send(200, [{"id": x["id"], "ts": x["ts"], "text": x["text"], "img": bool(x["image"]), "alt": x["alt"],
                                             "bsky": x["bsky_uri"]} for x in r.social.list("posted", 30)])
                if p == "/public/post_image":
                    x = r.social.get(int(q.get("id", ["0"])[0]))
                    if not x or x["status"] != "posted" or not x["image"] or not Path(x["image"]).is_file():
                        return self._send(404, {"error": "no image"})
                    return self._send(200, open(x["image"], "rb").read(), "image/jpeg")
                if p == "/sw.js":                    # offline placeholder: the browser shows offline.html when the robot is unreachable
                    return self._send(200, (WEB / "sw.js").read_text(), "text/javascript; charset=utf-8")
                if p == "/offline.html":
                    return self._page("offline.html")
                if p in ("/", "/index.html"):
                    if not self._role(q):
                        return self._redirect("/login")
                    return self._page("index.html")

                role = self._role(q)
                if not role:
                    return self._send(401, {"error": "sign in, pair, or send X-Robot-Token (see /root/robot/data/api_token)"})
                if p == "/api/whoami":
                    return self._send(200, {"role": role, "has_password": api.auth.has_password, "public": api.auth.public_sections()})
                if p == "/api/status":
                    st = dict(r.status_msg())
                    hb = r.cyd.info.get("hb") or {}      # the screen's heartbeat (every 2 s) says which face it shows
                    mood = int(hb.get("mood", 0) or 0)
                    st["face"] = {"mood": MOOD_NAMES[mood] if 0 <= mood < len(MOOD_NAMES) else "idle", "page": r.cyd.page,
                                  "asleep": bool(hb.get("asleep")), "up": bool(r.cyd.up),
                                  "theme": int(r.settings.screen_cfg().get("theme", 0) or 0)}
                    st["batt"] = r.battery.bar_info()     # header battery: {"s", "pct", "txt", "tip"} or null
                    return self._send(200, st)
                if p == "/api/devices":
                    return self._send(200, health.inventory(r))
                if p == "/api/ideas":
                    return self._send(200, health.ideas(r))
                if p == "/api/tests":
                    return self._send(200, tests.tests_list(r))
                if p == "/api/drive":
                    return self._send(200, r.drive.status())
                if p == "/api/blinkers":
                    return self._send(200, r.blinkers.status())
                if p == "/api/motion":
                    return self._send(200, {**r.pir.snapshot(), **r.motion_modes.status(owner=role == "owner")})
                if p == "/api/reminders":
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    return self._send(200, r.reminders.items())
                if p == "/api/security/photo":       # owner only: photos taken in security mode (never in Photos, never posted)
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    from .motion_modes import SECURITY
                    f = (SECURITY / q.get("name", [""])[0]).resolve()
                    if SECURITY.resolve() not in f.parents or not f.is_file():
                        return self._send(404, {"error": "no such photo"})
                    if q.get("thumb"):
                        img = Image.open(f)
                        img.thumbnail((320, 240))
                        buf = io.BytesIO()
                        img.save(buf, "JPEG", quality=80)
                        return self._send(200, buf.getvalue(), "image/jpeg")
                    return self._send(200, f.read_bytes(), "image/jpeg")
                if p == "/api/imu":
                    return self._send(200, r.imu.snapshot())
                if p == "/api/wheelie":
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    if q.get("run"):
                        one = r.wheelie.get_run(q["run"][0])
                        return self._send(200 if one else 404, one or {"error": "no such run"})
                    return self._send(200, r.wheelie.status())
                if p == "/api/log":
                    return self._send(200, list(r.log.lines))
                if p == "/api/brain":
                    return self._send(200, {**r.brain.summary(), "stats": r.brain.stats(), "usage": r.brain.usage})
                if p == "/api/memory":
                    return self._send(200, [{"ts": e[0], "kind": e[1], "msg": e[2]} for e in r.store.recent_events(int(q.get("n", ["50"])[0]))])
                if p == "/api/memories":
                    return self._send(200, r.brain.recall("", 50))
                if p == "/api/social":
                    return self._send(200, {"posts": r.social.list(None, 40), "channels": r.social.channels(), "cfg": r.social.cfg(),
                                            "counts": r.social.track.counts_by_post_id()})
                if p == "/api/social/image":
                    x = r.social.get(int(q.get("id", ["0"])[0]))
                    if not x or not x["image"] or not Path(x["image"]).is_file():     # the owner may delete post copies
                        return self._send(404, {"error": "no image"})
                    return self._send(200, open(x["image"], "rb").read(), "image/jpeg")
                if p == "/api/models":
                    g = lambda k: q.get(k, [""])[0]
                    rows = r.catalog.table(g("q"), g("vision") == "1", g("tools") == "1", g("free") == "1", g("sort") or "price")
                    scores = {e["model"]: e["score"] for e in r.bench.leaderboard()}
                    for x in rows:
                        x["score"] = scores.get(x["id"])
                    return self._send(200, {"models": rows, "fetched": r.catalog.fetched, "error": r.catalog.error})
                if p == "/api/models/pack":
                    scores = {e["model"]: e["score"] for e in r.bench.leaderboard()}
                    act = r.brain.active()[1].get("model")
                    return self._send(200, [{**x, "score": scores.get(x["id"]), "active": x["id"] == act} for x in r.catalog.pack()])
                if p == "/api/bench":
                    return self._send(200, {"job": r.bench.job, "leaderboard": r.bench.leaderboard(), "tasks": r.bench.tasks()})
                if p == "/api/vision":
                    return self._send(200, {**{k: v for k, v in r.vision.state.items() if k != "boxes"}, "cfg": r.vision.cfg()})
                if p == "/api/voice":
                    return self._send(200, r.voice.status())
                if p == "/api/settings":                 # the one settings list (screen, RoboOS and this app show it)
                    return self._send(200, {"groups": r.settings.all()})
                if p == "/api/camera":
                    return self._send(200, {"privacy": r.cam.privacy, "streaming": bool(r.cam.live_proc), "users": sorted(r.cam.live_owners)})
                if p == "/api/desktop":
                    dk = r.desktop
                    return self._send(200, {"stats": dk.stats, "windows": [w.name for w in dk.windows], "inputs": sorted(dk.inputs.kinds),
                                            "icons": dk.cfg()["icons"], "screen_page": r.cyd.page})
                if p == "/api/desktop.png":
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    return self._send(200, r.desktop.snapshot_png(), "image/png")
                if p == "/api/screen.png":           # the REAL touchscreen, read back from the LCD (about 2.5 s; owner only)
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    img = r.screencap.lcd()
                    if img is None:
                        return self._send(503, {"error": "screen did not answer (needs firmware 0.7.0+)"})
                    buf = io.BytesIO()
                    img.save(buf, "PNG")
                    return self._send(200, buf.getvalue(), "image/png")
                if p == "/api/desktop/demo.mp4":      # preview a show-off video (the robot plays a game by itself, off-screen)
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    from .screencap import encode_mp4
                    import tempfile
                    out = Path(tempfile.mkdtemp()) / "demo.mp4"
                    try:
                        if not encode_mp4(r.desktop.demo_clip(q.get("app", ["checkers"])[0], float(q.get("s", ["20"])[0])), out):
                            return self._send(500, {"error": "encoding failed"})
                        return self._send(200, out.read_bytes(), "video/mp4")
                    finally:
                        shutil.rmtree(out.parent, ignore_errors=True)
                if p == "/api/social/report":        # how the posts are doing: per post, and what changed in the last N hours
                    return self._send(200, {"text": r.social.track.report(int(q.get("hours", ["24"])[0] or 24))})
                if p == "/api/social/stats":         # followers / likes from the public Bluesky API
                    return self._send(200, r.social.stats(bool(q.get("refresh"))) or {})
                if p == "/api/manual":            # the robot's manual for AI helpers (no secrets in it)
                    from .brain import manual_pages, read_manual
                    page = q.get("page", [""])[0]
                    own = role == "owner"         # this robot's own notes (data/manual: home network, owner) are not for guests
                    return self._send(200, {"pages": manual_pages(own), "page": page or "README", "text": read_manual(page or "README", own)})
                if p in ("/api/agent", "/api/dev/job"):       # RoboDev (owner only: it shows code, commands and output)
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    since = int(q.get("since", ["0"])[0] or 0)
                    return self._send(200, {"status": r.agent.status(), "items": r.agent.since(since)})
                if p == "/api/agent/models":
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    return self._send(200, {"models": r.agent.models(), "current": r.agent.model()})
                if p == "/api/agent/file":                  # an attachment, e.g. to show the picture in the chat
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    from .agent import UPLOADS
                    f = (UPLOADS / Path(q.get("name", [""])[0]).name)
                    if not f.is_file():
                        return self._send(404, {"error": "no such file"})
                    ctype = "image/jpeg" if f.suffix.lower() in (".jpg", ".jpeg") else "application/octet-stream"
                    return self._send_file(f, ctype)
                if p == "/api/feed":
                    try:
                        since = int(q.get("since", ["0"])[0])
                    except ValueError:                 # a page that lost its place sent "undefined": start over
                        since = 0
                    with r.store.lock:
                        rows = r.store.db.execute(
                            f"SELECT id, ts, kind, msg FROM events WHERE id > ? AND kind IN ({','.join('?' * len(FEED_KINDS))}) ORDER BY id DESC LIMIT 60",
                            (since, *FEED_KINDS)).fetchall()
                    return self._send(200, [{"id": a, "ts": b, "kind": c, "msg": d} for a, b, c, d in reversed(rows)])
                if p == "/api/photos":
                    files = list(PHOTOS.rglob("*.jpg"))
                    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                    files = files[:300]
                    if q.get("info"):                 # web app: names + "copy made for a post" + look-alike hash
                        return self._send(200, [{"n": str(f.relative_to(PHOTOS)), "post": f.parent.name == "posts",
                                                 "h": photo_hash(f)} for f in files])
                    return self._send(200, [str(f.relative_to(PHOTOS)) for f in files])
                if p == "/api/music":                 # owner: the Music folder (RoboTube "Save audio" lands here)
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    from .desktop_apps import MUSIC_DIR, audio_files
                    au = r.desktop.audio
                    return self._send(200, {"folder": str(MUSIC_DIR), "playing": au.name if au.playing else "",
                                            "files": [{"n": str(f.relative_to(MUSIC_DIR)), "size": f.stat().st_size,
                                                       "ts": f.stat().st_mtime} for f in audio_files()]})
                if p == "/api/music/file":            # owner: stream one song to the browser (Range support for phones)
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    from .desktop_apps import MUSIC_DIR
                    f = (MUSIC_DIR / q.get("name", [""])[0]).resolve()
                    if MUSIC_DIR.resolve() not in f.parents or not f.is_file():
                        return self._send(404, {"error": "no such song"})
                    return self._send_file(f, AUDIO_TYPES.get(f.suffix.lower(), "application/octet-stream"))
                if p == "/api/photo":
                    f = (PHOTOS / q.get("name", [""])[0]).resolve()
                    if PHOTOS.resolve() not in f.parents or not f.exists():
                        return self._send(404, {"error": "no such photo"})
                    if q.get("thumb"):
                        img = Image.open(f)
                        img.thumbnail((320, 240))
                        buf = io.BytesIO()
                        img.convert("RGB").save(buf, "JPEG", quality=70)
                        return self._send(200, buf.getvalue(), "image/jpeg")
                    return self._send(200, f.read_bytes(), "image/jpeg")
                if p == "/api/weather":              # weather for the app / panel (the place itself is never returned)
                    return self._send(200, r.weather.status())
                if p == "/api/battery":              # INA219 battery level; ?history=1 adds (ts, V, mA, %) every 10 s for 24 h
                    return self._send(200, r.battery.status(hist=bool(q.get("history"))))
                if p in ("/api/mail", "/api/mail/message"):   # Pip's email (owner only; the inbox list is a cached copy)
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    from .mail import MailError
                    if p == "/api/mail":
                        return self._send(200, r.mail.status())
                    try:
                        return self._send(200, r.mail.read(q.get("id", [""])[0]))
                    except MailError as e:
                        return self._send(409, {"error": str(e)})
                if p in ("/api/files", "/api/files/get"):   # the owner's file browser (robotd/files.py); secrets are never listed or sent
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    from . import files
                    try:
                        if p == "/api/files":
                            return self._send(200, files.listing(q.get("path", [""])[0]))
                        f, ctype, head = files.serve_info(q.get("path", [""])[0], download=q.get("dl", ["0"])[0] == "1")
                        return self._send_file(f, ctype, head)
                    except files.FileError as e:
                        return self._send(404, {"error": str(e)})
                if p == "/api/debug/threads":        # where robotd's CPU time goes (robotd/profiler.py); ?secs=10
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    from . import profiler
                    return self._send(200, profiler.sample(float(q.get("secs", ["10"])[0] or 10)))
                if p == "/api/wifi":                 # Wi-Fi keeper: peers we announce ourselves to + our neighbour table
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    return self._send(200, r.wifi_keep.status())
                if p == "/api/live.mjpg":            # continuous stream: no round trip per frame (smooth through the tunnel)
                    fps = max(1.0, min(15.0, float(q.get("fps", ["10"])[0] or 10)))
                    vis = q.get("vision", ["0"])[0] == "1"
                    small = q.get("size", ["full"])[0] == "small"   # 320x240: about 13 KB a frame instead of 40 (remote viewers)
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    f, t_last = api.live_frame(), 0.0
                    end = time.time() + 900                  # one connection lasts at most 15 minutes (the page reconnects)
                    try:
                        while time.time() < end and not r.cam.privacy:
                            t0 = time.time()
                            api.web_live_t = t0              # keeps the camera on while someone watches
                            f, ts = r.cam.next_frame(t_last, timeout=2.0)
                            if not f:
                                continue
                            t_last = ts
                            if vis and r.vision.mode != "off":
                                ann, _ = r.vision.process(f, out_w=640)
                                f = r.vision.encode(ann, 640, 480, 80) if ann is not None else f
                            if small:
                                import cv2
                                import numpy as np
                                im = cv2.imdecode(np.frombuffer(f, np.uint8), cv2.IMREAD_REDUCED_COLOR_2)   # cheap half-size decode
                                if im is not None:
                                    f = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(f) + f + b"\r\n")
                            self.wfile.flush()                # blocks while the network is slow: old frames are skipped
                            time.sleep(max(0.0, 1.0 / fps - (time.time() - t0)))
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    return
                if p == "/api/live.jpg":
                    f = api.live_frame()
                    if f and q.get("vision", ["0"])[0] == "1" and r.vision.mode != "off":
                        ann, _ = r.vision.process(f, out_w=640)
                        f = r.vision.encode(ann, 640, 480, 80) if ann is not None else f
                    return self._send(200, f, "image/jpeg") if f else self._send(503, {"error": "no frame"})
                if p == "/api/snapshot.jpg":
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    w, h = int(q.get("w", ["640"])[0]), int(q.get("h", ["480"])[0])
                    img = r.cam.still(w, h, skip=4)
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=85)
                    return self._send(200, buf.getvalue(), "image/jpeg")
                return self._send(404, {"error": "unknown endpoint"})

            def _upload(self, q):
                """POST /api/files/upload?dir=data/files&name=cat.jpg with the file as the raw body: streamed straight to the
                card, never held in memory (the robot has 484 MB)."""
                from . import files
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if self._role(q) != "owner":
                    self.close_connection = True         # the unread body must not be taken for the next request
                    return self._send(403, {"ok": False, "msg": "owner sign-in required"})
                try:
                    path = files.save_upload(q.get("dir", [""])[0], q.get("name", [""])[0], self.rfile, n)
                except files.FileError as e:
                    self.close_connection = True
                    return self._send(409, {"ok": False, "msg": str(e)})
                except OSError as e:
                    self.close_connection = True
                    return self._send(500, {"ok": False, "msg": f"could not save it: {e}"})
                r.log("file received in the shared folder (web app: Media > Files)")
                r.social.upload_arrived(path)          # a photo or video: Pip looks at it and may post it (setting)
                return self._send(200, {"ok": True, "path": path, "msg": f"saved as {path}"})

            # ------------------------------------------------------------------ POST
            def do_POST(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                p = u.path
                if p == "/api/files/upload":
                    return self._upload(q)
                body = self._body()
                if p == "/login":
                    who = self._client()
                    wait = api.throttle.wait_s(who)
                    if wait or not api.throttle.password_slot():
                        return self._page("login.html", 429, ERR=f"Too many wrong tries. Wait {wait or 60} s and try again, "
                                                                   "or pair with the QR code on the robot's screen.")
                    pw = parse_qs(body.decode(errors="replace")).get("password", [""])[0]
                    if api.auth.check_password(pw) or (pw and secrets.compare_digest(pw.encode("utf-8", "replace"), api.token.encode())):
                        api.throttle.ok(who)
                        r.log("owner signed in to the web app")
                        return self._redirect("/", self._cookie("owner"))
                    n = api.throttle.fail(who)
                    if n == Throttle.FREE:                 # worth a line in the log: someone is guessing (or forgot the password)
                        r.log(f"web sign-in: {n} wrong passwords in a row from one address; it now has to wait between tries", "warn")
                    time.sleep(1.0)  # slow down guessing
                    return self._page("login.html", ERR="Wrong password.")
                role = self._role(q)
                if role != "owner":
                    return self._send(401 if not role else 403, {"error": "owner sign-in required"})
                try:
                    data = json.loads(body) if body and p != "/api/show" else {}
                except ValueError:
                    return self._send(400, {"error": "bad json"})

                if p.startswith("/api/test/"):
                    tid = p.rsplit("/", 1)[1]
                    if tid not in tests.TESTS:
                        return self._send(404, {"error": "unknown test", "tests": list(tests.TESTS)})
                    with r.test_lock:
                        res = tests.run_test(r, tid)
                    r.cyd.send({"t": "tests", "items": tests.tests_list(r)})
                    return self._send(200, res)
                if p == "/api/face":
                    # a mood picked here stays until mood "auto" (or a hold in ms is given); the robot's reactions show on top
                    r.face(data.get("mood"), data.get("say"), lasting=bool(data.get("mood")) and not data.get("hold"),
                           **{k: v for k, v in data.items() if k in ("look", "hold", "sec", "chime")})
                    return self._send(200, {"ok": True, "base": getattr(r, "face_base", "idle")})
                if p == "/api/toast":
                    r.toast(data.get("msg", ""))
                    return self._send(200, {"ok": True})
                if p == "/api/show":
                    img = Image.open(io.BytesIO(body))
                    return self._send(200, {"ok": r.show_image(img, q.get("caption", [""])[0])})
                if p == "/api/ask":
                    ans = api.ask_owner(data.get("title", "Question"), data.get("q", ""), data.get("opts", ["Yes", "No"]), float(data.get("timeout", 120)))
                    return self._send(200, {"answer": ans})
                if p == "/api/beep":
                    r.cyd.send({"t": "beep", **{k: v for k, v in data.items() if k in ("hz", "ms", "chime")}})
                    return self._send(200, {"ok": True})
                if p == "/api/snap":
                    path, img = r.cam.save_photo(PHOTOS)
                    r.log(f"photo saved {path.name} (web)")
                    return self._send(200, {"ok": True, "msg": f"saved {path.name}", "name": path.name})
                if p == "/api/say":
                    threading.Thread(target=r.voice.speak, args=(data.get("text", ""), data.get("mood")), daemon=True).start()
                    return self._send(200, {"ok": True})
                if p == "/api/chat":
                    reply = r.brain.ask(data.get("text", ""), src="web")
                    if data.get("speak", True):
                        threading.Thread(target=r.voice.speak, args=(reply,), daemon=True).start()
                    return self._send(200, {"reply": reply, "online": r.brain.online, "usage": r.brain.usage})
                if p == "/api/hear":                  # web app hold-to-talk: the phone's microphone -> text
                    import base64
                    audio = base64.b64decode(str(data.get("audio", "")).split(",")[-1] or b"")
                    if not audio or len(audio) > 8_000_000:
                        return self._send(400, {"error": "no audio (or over 8 MB)"})
                    text = r.voice.transcribe_upload(audio, str(data.get("mime", "")))
                    return self._send(200, {"text": text})
                if p == "/api/listen":
                    threading.Thread(target=r.conversation_turn, daemon=True).start()
                    return self._send(200, {"ok": True, "msg": "listening via the robot microphone"})
                if p == "/api/social/draft":
                    img = None
                    if data.get("photo"):
                        f = (PHOTOS / data["photo"]).resolve()
                        if PHOTOS.resolve() in f.parents and f.exists():
                            img = Image.open(f)
                    elif data.get("take_photo"):
                        img = r.cam.still(1280, 720)
                    vid = None
                    if data.get("video"):              # owner: an MP4 in data/social (e.g. a clip the owner filmed); photo = its preview
                        v = (DATA / "social" / str(data["video"])).resolve()
                        if not ((DATA / "social").resolve() in v.parents and v.suffix.lower() == ".mp4" and v.is_file()):
                            return self._send(400, {"error": "video must be an .mp4 file in data/social"})
                        vid = str(v)
                    return self._send(200, r.social.draft(data.get("note", "a moment from my day"), img,
                                                          data.get("kind", "video" if vid else "photo" if img else "update"), video=vid,
                                                          owner_photo=bool(data.get("owner_photo") and (data.get("photo") or vid)),
                                                          check_repeat=bool(data.get("check_repeat", False))))
                if p == "/api/social/offer":            # owner: media the brain may attach to a post by name (if it wants to)
                    try:
                        return self._send(200, r.social.offer(data.get("name", ""), data.get("about", ""), data.get("photo", ""),
                                                              data.get("video", ""), bool(data.get("remove"))))
                    except ValueError as e:
                        return self._send(400, {"error": str(e)})
                if p == "/api/social/publish":
                    ok, msg = r.social.publish(int(data.get("id", 0)), data.get("text"))
                    return self._send(200 if ok else 409, {"ok": ok, "msg": msg})
                if p == "/api/social/discard":
                    r.social._update(int(data.get("id", 0)), status="discarded")
                    return self._send(200, {"ok": True})
                if p == "/api/social/creds":
                    return self._send(200, {"channels": r.social.set_creds(data)})
                if p == "/api/social/settings":
                    c = r.social.cfg()
                    for k in ("auto_post_updates", "daily_diary", "hashtags", "autonomous", "max_posts_per_day", "min_gap_minutes",
                              "posts_per_day", "post_hours", "show_off", "self_portrait_days"):
                        if k in data:
                            c[k] = data[k]
                    r.cfg.save()
                    return self._send(200, c)
                if p == "/api/brain/use":
                    mid = data.get("model_id", "").strip()
                    info = r.catalog.get(mid)
                    if not info:
                        return self._send(404, {"error": "unknown OpenRouter model id"})
                    r.brain.models()[mid] = {"provider": "openrouter", "model": mid, "label": info["name"]}
                    r.cfg.save()
                    r.brain.select(mid)
                    return self._send(200, r.brain.summary())
                if p == "/api/bench/run":
                    models = data.get("models") or []
                    if models == "pack" or not models:
                        models = [x["id"] for x in r.catalog.pack()]
                    ok, msg = r.bench.start(models[:20])
                    return self._send(200 if ok else 409, {"ok": ok, "msg": msg})
                if p == "/api/vision":
                    c = r.vision.cfg()
                    if "mode" in data:
                        r.vision.set_mode(data["mode"])
                    for k in ("follow", "mirror", "reactions", "gaze_mirror"):
                        if k in data:
                            c[k] = bool(data[k])
                    r.cfg.save()
                    if (c["follow"] or c["gaze_mirror"]) and r.cyd.page == "face":
                        r.start_stream("follow", r.vision.follow_loop)
                    return self._send(200, c)
                if p == "/api/settings":
                    ok, msg = r.settings.apply(str(data.get("id", "")), data.get("op"), data.get("value"), bool(data.get("confirmed")))
                    return self._send(200 if ok or msg == "confirm" else 400, {"ok": ok, "msg": msg, "groups": r.settings.all()})
                if p == "/api/voice":
                    st = r.voice.set_output(data.get("output"), data.get("pi_volume"), data.get("pi_speaker"))
                    if data.get("test"):
                        threading.Thread(target=r.voice.speak, args=(data.get("text") or "Hello! This is how I sound now.",), daemon=True).start()
                    return self._send(200, {**st, "msg": f"voice: {st['active']}"})
                if p == "/api/camera":
                    if "privacy" in data:
                        r.desktop.set_privacy(bool(data["privacy"]))
                    return self._send(200, {"privacy": r.cam.privacy, "msg": "camera OFF (privacy)" if r.cam.privacy else "camera allowed"})
                if p == "/api/desktop/input":
                    r.desktop.remote_input(data)
                    return self._send(200, {"ok": True})
                if p == "/api/page":                 # switch the touchscreen to a page (face, menu, settings, desktop, ...)
                    r.cyd.send({"t": "page", "name": str(data.get("name", "face"))})
                    return self._send(200, {"ok": True, "msg": f"screen -> {data.get('name', 'face')}"})
                if p == "/api/desktop/open":
                    r.cyd.send({"t": "page", "name": "desktop"})
                    if data.get("app"):
                        r.desktop.open_app(str(data["app"]))
                    return self._send(200, {"ok": True, "msg": "desktop opened on the screen"})
                if p == "/api/social/show_off":       # post a demo video of a RoboOS game now (or a face screenshot)
                    threading.Thread(target=r.social.show_off, args=(data.get("app"), bool(data.get("video", True))), daemon=True).start()
                    return self._send(200, {"ok": True, "msg": "making a show-off post (a video takes about a minute)"})
                if p == "/api/social/avatar":         # owner: set the Bluesky profile picture from a file in data/photos
                    f = (PHOTOS / str(data.get("photo", ""))).resolve()
                    if not (PHOTOS.resolve() in f.parents and f.is_file()):
                        return self._send(400, {"error": "photo must be a file in data/photos"})
                    try:
                        r.social.set_avatar(Image.open(f))
                    except Exception as e:
                        return self._send(502, {"error": f"Bluesky: {e}"})
                    return self._send(200, {"ok": True, "msg": "profile picture changed"})
                if p == "/api/social/profile":        # owner: Bluesky display name and bio
                    try:
                        prof = r.social.update_profile(data.get("displayName"), data.get("description"))
                    except Exception as e:
                        return self._send(502, {"error": f"Bluesky: {e}"})
                    return self._send(200, {"ok": True, "profile": prof})
                if p in ("/api/social/draw", "/api/social/scene"):   # draw anything in RoboOS Paint: {"what": "...", "from_photo": false, "post": false}
                    what = str(data.get("what") or data.get("description") or "").strip()[:400] or None   # scene/description: RoboDev's older name
                    photo = r.cam.still(1280, 720, skip=6) if data.get("from_photo") else None
                    if not what and photo is None:
                        return self._send(400, {"ok": False, "msg": "say what to draw, or set from_photo"})
                    threading.Thread(target=r.social.draw, args=(what, photo, bool(data.get("post", False))), daemon=True).start()
                    return self._send(200, {"ok": True, "msg": "drawing started (about 2 minutes)"})
                if p == "/api/social/self_portrait":  # draw what I think I look like in RoboOS Paint, post the video
                    threading.Thread(target=r.social.self_portrait, args=(bool(data.get("post", True)),), daemon=True).start()
                    return self._send(200, {"ok": True, "msg": "drawing a self-portrait (about 2 minutes), then posting it"})
                if p == "/api/screen":
                    msg = {"t": "cfg"}
                    for k in ("theme", "bright"):            # (volume: POST /api/settings sound.volume, one volume for all outputs)
                        if k in data:
                            msg[k] = int(data[k])
                            r.settings.screen_cfg()[k] = int(data[k])
                    r.cyd.send(msg)
                    return self._send(200, {"ok": True, "msg": "screen updated"})
                if p in ("/api/agent/send", "/api/dev/task"):  # RoboDev: a message (text + optional attachments)
                    ok, msg = r.agent.send(data.get("text") or data.get("task", ""), data.get("attachments") or [],
                                           data.get("source") if data.get("source") in ("web", "mcp", "desktop") else "web")
                    return self._send(200 if ok else 409, {"ok": ok, "msg": msg, "status": r.agent.status()})
                if p == "/api/agent/approve":
                    ok = r.agent.answer(str(data.get("id", "")), bool(data.get("allow")))
                    return self._send(200 if ok else 409, {"ok": ok})
                if p == "/api/agent/stop":
                    r.agent.stop()
                    return self._send(200, {"ok": True})
                if p == "/api/agent/new":
                    ok, msg = r.agent.new_session()
                    return self._send(200 if ok else 409, {"ok": ok, "msg": msg})
                if p == "/api/agent/settings":
                    try:
                        st = r.agent.settings(**{k: data[k] for k in ("mode", "model", "voice_max_mode", "daily_budget_usd",
                                                                      "full_minutes", "max_steps") if k in data})
                    except (ValueError, TypeError) as e:
                        return self._send(400, {"error": str(e)})
                    return self._send(200, st)
                if p == "/api/firmware/fetch":
                    from . import firmware
                    ok, msg, files = firmware.fetch_ci_firmware(r)
                    return self._send(200 if ok else 409, {"ok": ok, "msg": msg, "files": files})
                if p == "/api/firmware/flash":
                    from . import firmware
                    fn = firmware.flash_cyd if data.get("target") == "cyd" else firmware.flash_rp2040
                    ok, out = fn(r, data.get("path", ""), data.get("reason", "requested via API"))
                    return self._send(200 if ok else 409, {"ok": ok, "out": out})
                if p == "/api/memories":
                    r.brain.remember(data.get("fact", ""))
                    return self._send(200, {"ok": True})
                if p == "/api/brain/select":
                    ok = r.brain.select(data.get("key", ""))
                    return self._send(200 if ok else 404, r.brain.summary())
                if p == "/api/brain/rate":
                    r.brain.rate_last(bool(data.get("good", True)))
                    return self._send(200, {"ok": True})
                if p == "/api/brain/model":
                    key = data.get("key", "").strip()
                    entry = {k: v for k, v in data.items() if k in ("provider", "model", "label", "effort", "fallbacks")}
                    if not key or entry.get("provider") not in ("anthropic", "openrouter", "offline"):
                        return self._send(400, {"error": "need key and provider anthropic|openrouter|offline"})
                    r.brain.models()[key] = entry
                    r.cfg.save()
                    r.cyd.send(r.brain.summary())
                    return self._send(200, r.brain.summary())
                if p == "/api/brain/key":
                    prov, key = data.get("provider"), data.get("key", "").strip()
                    names = {"anthropic": ("anthropic.env", "ANTHROPIC_API_KEY"), "openrouter": ("openrouter.env", "OPENROUTER_API_KEY")}
                    if prov not in names or not key:
                        return self._send(400, {"error": "provider anthropic|openrouter and key required"})
                    fname, var = names[prov]
                    path = DATA / fname
                    path.write_text(f"{var}={key}\n")
                    path.chmod(0o600)
                    import os as _os
                    _os.environ[var] = key
                    r.brain._init_client()
                    r.cyd.send(r.brain.summary())
                    r.log(f"API key stored for {prov}")
                    return self._send(200, r.brain.summary())
                if p == "/api/imu/level":
                    ok, msg = r.imu.level_here()
                    r.log(f"IMU LEVEL (api): {msg}")
                    r.store.test("imu_level", ok, msg, source="api")
                    return self._send(200, {"ok": ok, "msg": msg})
                if p == "/api/imu/forward":            # owner holds the front up: that direction is forward
                    ok, msg = r.imu.forward_here()
                    r.log(f"IMU FORWARD (api): {msg}")
                    return self._send(200, {"ok": ok, "msg": msg})
                if p == "/api/wheelie":                # owner only; see robotd/wheelie.py
                    if role != "owner":
                        return self._send(403, {"error": "owner only"})
                    cmd = str(data.get("cmd", "")).lower()
                    vals = {k: v for k, v in data.items() if k not in ("cmd", "ask", "secs", "level", "n", "routine", "moves")}
                    try:
                        if cmd == "setup":
                            ok, msg = r.wheelie.setup(level=bool(data.get("level", True)))
                            return self._send(200 if ok else 409, {"ok": ok, "msg": msg})
                        if cmd == "stop":
                            return self._send(200, r.wheelie.stop())
                        if cmd == "show":
                            ok, msg = r.wheelie.show(data.get("secs"), data.get("routine") or data.get("moves"))
                            return self._send(200 if ok else 409, {"ok": ok, "msg": msg})
                        if cmd == "practice":           # blocks until the session is over (minutes)
                            res = r.wheelie.practice(data.get("n", 5), data.get("secs", 20), ask=bool(data.get("ask", True)))
                            return self._send(200 if res.get("ok") else 409, res)
                        if cmd == "home":               # drive back by the last run's turn and (estimated) distance
                            last = r.wheelie.runs(1)
                            return self._send(200, {"ok": True, "msg": r.wheelie.go_home((last[0].get("metrics") or {}) if last else {})})
                        if cmd == "set":
                            return self._send(200, {"ok": True, "set": r.wheelie.set(vals), "params": r.wheelie.params()})
                        res = r.wheelie.run(cmd, ask=bool(data.get("ask", True)), secs=float(data.get("secs", 8)),
                                            moves=r.wheelie.routine(data.get("routine") or data.get("moves")), **vals)
                    except (ValueError, TypeError) as e:
                        return self._send(400, {"error": str(e)})
                    return self._send(200 if res.get("ok") else 409, res)
                if p == "/api/imu/gyro_cal":
                    ok, msg = r.imu.gyro_cal()
                    r.log(f"IMU GYRO 0 (api): {msg}")
                    r.store.test("imu_gyro_0", ok, msg, source="api")
                    return self._send(200, {"ok": ok, "msg": msg})
                if p == "/api/web/password":
                    pw = data.get("password", "")
                    if len(pw) < 8:
                        return self._send(400, {"error": "use at least 8 characters"})
                    api.auth.set_password(pw)
                    r.log("owner web password set")
                    return self._send(200, {"ok": True, "msg": "password set"})
                if p == "/api/web/invite":
                    code = api.auth.new_token("guest", 24 * 60)      # a link, not a typed code: long and unguessable
                    return self._send(200, {"url": f"{api.base_url()}/pair?c={code}"})
                if p == "/api/web/public":
                    api.auth.set_public(data)
                    return self._send(200, api.auth.public_sections())
                if p == "/api/web/revoke":
                    api.auth.revoke_all()
                    return self._send(200, {"ok": True}, headers={"Set-Cookie": self._cookie("owner")})
                if p == "/api/motors":
                    try:
                        res = r.motor.drive(data.get("values", [0] * 8), float(data.get("duration", 0.5)))
                    except PermissionError as e:
                        return self._send(403, {"error": str(e)})
                    return self._send(200, {"reply": res})
                if p == "/api/stop":
                    return self._send(200, {"reply": r.motor.stop()})
                if p == "/api/drive":                 # onboard steering: one move, {"seq": [...]}, or {"cmd": "stop"}
                    if str(data.get("cmd", "")).lower() == "stop":
                        r.drive.stop()
                        return self._send(200, {"ok": True, "msg": "stopped"})
                    try:
                        ok, msg = r.drive.start(data["seq"] if isinstance(data.get("seq"), list) else [data])
                    except (PermissionError, RuntimeError) as e:
                        return self._send(403, {"error": str(e)})
                    return self._send(200 if ok else 409, {"ok": ok, "msg": msg})
                if p == "/api/files":                # owner: {"cmd": "mkdir" | "rename" | "delete", "path": ..., "name": ...} (shared folder only)
                    from . import files
                    try:
                        cmd = data.get("cmd")
                        if cmd == "mkdir":
                            return self._send(200, {"ok": True, "path": files.mkdir(data.get("path", ""), data.get("name", ""))})
                        if cmd == "rename":
                            return self._send(200, {"ok": True, "path": files.rename(data.get("path", ""), data.get("name", ""))})
                        if cmd == "delete":
                            return self._send(200, {"ok": True, "path": files.delete(data.get("path", "")), "msg": "deleted"})
                    except files.FileError as e:
                        return self._send(409, {"ok": False, "msg": str(e)})
                    except OSError as e:
                        return self._send(500, {"ok": False, "msg": str(e)})
                    return self._send(400, {"ok": False, "msg": "unknown cmd"})
                if p == "/api/mail":                 # owner: {"cmd": "refresh" | "draft" | "discard" | "approve" | "send", ...}
                    from .mail import MailError
                    cmd = data.get("cmd", "refresh")
                    by_token = bool(self.headers.get("X-Robot-Token") or q.get("token", [""])[0])
                    if cmd in ("approve", "send") and by_token:
                        # owner rule: every send is approved by hand in the web app. The token (RoboDev, MCP, scripts) never sends.
                        return self._send(403, {"error": "sending needs the owner: tap Send on the touchscreen, or Settings > Email in the signed-in web app"})
                    try:
                        if cmd == "refresh":
                            r.mail.check()
                            return self._send(200, r.mail.status())
                        if cmd == "draft":
                            d = r.mail.draft(data.get("to", ""), data.get("subject", ""), data.get("text", ""), data.get("reply_to", ""),
                                             src="mcp" if by_token else "owner", note=data.get("note", ""))
                            return self._send(200, {"ok": True, "msg": f"draft #{d['id']} saved (not sent)", "draft": d})
                        if cmd == "discard":
                            return self._send(200, {"ok": True, "msg": "draft discarded", "draft": r.mail.discard(data.get("id"))})
                        if cmd == "approve":
                            d = r.mail.approve_and_send(data.get("id"), data.get("to"), data.get("subject"), data.get("text"))
                            return self._send(200, {"ok": True, "msg": f"sent to {d['to']}", "draft": d})
                        if cmd == "send":            # the owner's own email from the web app: Send is the approval
                            d = r.mail.draft(data.get("to", ""), data.get("subject", ""), data.get("text", ""), data.get("reply_to", ""), src="owner")
                            d = r.mail.approve_and_send(d["id"])
                            return self._send(200, {"ok": True, "msg": f"sent to {d['to']}", "draft": d})
                    except MailError as e:
                        return self._send(409, {"ok": False, "msg": str(e)})
                    return self._send(400, {"ok": False, "msg": f"unknown cmd {cmd}"})
                if p == "/api/battery":              # owner: {"cmd": "test_start" [stop_cell_v] | "test_stop" | "add_pack" series mah [chem note select]}
                    cmd = data.get("cmd", "")
                    if cmd == "test_start":
                        return self._send(200, r.battery.start_test(data.get("stop_cell_v")))
                    if cmd == "test_stop":
                        return self._send(200, r.battery.stop_test())
                    if cmd == "add_pack":
                        pk = r.battery.add_pack(data.get("series", 3), data.get("mah", 1000), data.get("chem", "liion"),
                                                str(data.get("note", ""))[:200], bool(data.get("select")), data.get("test_stop_cell_v"))
                        return self._send(200, {"ok": True, "pack": pk})
                    return self._send(400, {"ok": False, "msg": f"unknown cmd {cmd}"})
                if p == "/api/weather":              # owner: {"cmd": "refresh" | "panel_on" | "panel_off"}
                    cmd = data.get("cmd", "refresh")
                    if cmd == "panel_on":
                        return self._send(200, r.weather.panel_on())
                    if cmd == "panel_off":
                        return self._send(200, r.weather.panel_off())
                    r.weather.refresh(wait=True)
                    return self._send(200, r.weather.status())
                if p == "/api/blinkers":              # owner: {"cmd": status|off|write|read|plan|show|number|battery|link_test|experiment, ...}
                    try:
                        return self._send(200, r.blinkers.command(data))
                    except (OSError, RuntimeError, ValueError, KeyError) as e:
                        return self._send(400, {"error": str(e)})
                if p == "/api/motion":                # owner: {"mode": off|wake|greet|security, "greet_ai", "security_warn", ...}
                    try:
                        msg = r.motion_modes.set(data.get("mode"), greet_ai=data.get("greet_ai"), security_warn=data.get("security_warn"),
                                                 greet_cooldown=data.get("greet_cooldown"), arm_delay=data.get("arm_delay"))
                    except ValueError as e:
                        return self._send(400, {"error": str(e)})
                    return self._send(200, {**r.motion_modes.status(owner=True), "msg": msg})
                if p == "/api/reminders":             # owner: {"text","title","not_before","fallback_at","action","on_motion"}
                    try:
                        it = r.reminders.add(data.get("text", ""), data.get("title", "Reminder"), data.get("not_before"),
                                             data.get("fallback_at"), data.get("action"), data.get("opts"), data.get("on_motion", True))
                    except ValueError as e:
                        return self._send(400, {"error": str(e)})
                    return self._send(200, {"ok": True, "reminder": it, "msg": "reminder saved"})
                if p == "/api/reminders/clear":
                    r.reminders.clear()
                    return self._send(200, {"ok": True, "msg": "reminders cleared"})
                if p == "/api/photos/delete":         # owner: {"names": [...]} files in data/photos (web app Media tab)
                    n = 0
                    for name in data.get("names") or []:
                        f = (PHOTOS / str(name)).resolve()
                        if PHOTOS.resolve() in f.parents and f.is_file() and f.suffix.lower() == ".jpg":
                            f.unlink()
                            n += 1
                    r.log(f"owner deleted {n} photo(s) from the web app")
                    return self._send(200, {"ok": True, "deleted": n, "msg": f"deleted {n} photo{'s' if n != 1 else ''}"})
                if p == "/api/music/delete":          # owner: {"name"} one file in data/music
                    from .desktop_apps import MUSIC_DIR
                    f = (MUSIC_DIR / str(data.get("name", ""))).resolve()
                    if MUSIC_DIR.resolve() not in f.parents or not f.is_file():
                        return self._send(404, {"error": "no such song"})
                    if r.desktop.audio.playing and str(r.desktop.audio.source) == str(f):
                        r.desktop.audio.stop()
                    f.unlink()
                    r.log(f"owner deleted song {f.name}")
                    return self._send(200, {"ok": True, "msg": "song deleted"})
                if p == "/api/music/play":            # owner: {"name"} play on the robot's speaker, or {"stop": true}
                    from .desktop_apps import MUSIC_DIR
                    if data.get("stop"):
                        r.desktop.audio.stop()
                        return self._send(200, {"ok": True, "msg": "stopped"})
                    f = (MUSIC_DIR / str(data.get("name", ""))).resolve()
                    if MUSIC_DIR.resolve() not in f.parents or not f.is_file():
                        return self._send(404, {"error": "no such song"})
                    r.desktop.audio.play(f)
                    return self._send(200, {"ok": True, "msg": "playing on the robot"})
                if p == "/api/security/clear":        # owner: delete all security-mode photos
                    from .motion_modes import SECURITY
                    n = 0
                    for f in SECURITY.glob("sec_*.jpg"):
                        f.unlink(missing_ok=True)
                        n += 1
                    r.log(f"security photos cleared by the owner ({n})")
                    return self._send(200, {"ok": True, "msg": f"deleted {n} security photos"})
                if p == "/api/drive/rc":              # joystick: {"x","y","max"} every 100-200 ms; stops 0.4 s after the last
                    try:
                        L, R = r.drive.rc(data.get("x", 0), data.get("y", 0), data.get("max"))
                    except (PermissionError, RuntimeError) as e:
                        return self._send(403, {"error": str(e)})
                    return self._send(200, {"ok": True, "l": L, "r": R})
                if p == "/api/motors/settings":       # owner only: unlock / lock the motors and set the power cap (of 127)
                    if "enabled" in data:
                        r.cfg.data["motors_enabled"] = bool(data["enabled"])
                    if "max" in data:
                        r.cfg.data["motor_max"] = int(max(0, min(127, int(data["max"]))))
                    r.cfg.save()
                    if not r.cfg["motors_enabled"]:
                        r.drive.stop()
                    r.log(f"motors {'ENABLED' if r.cfg['motors_enabled'] else 'locked'} by the owner, max {r.cfg['motor_max']}")
                    return self._send(200, {"motors_enabled": r.cfg["motors_enabled"], "motor_max": r.cfg["motor_max"]})
                return self._send(404, {"error": "unknown endpoint"})

        c = r.cfg["api"]
        srv = ThreadingHTTPServer((c["bind"], c["port"]), H)
        srv.daemon_threads = True
        r.log(f"web app + API on port {c['port']}")
        srv.serve_forever()
