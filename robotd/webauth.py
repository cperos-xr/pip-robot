"""Web access control: owner password, QR pairing from the touchscreen, guest invites and a public page.
Sessions are HMAC-signed cookies (no server-side state needed). Roles: owner (full control), guest (view only).

This code is public, so nothing here may depend on being unknown: the password is stored as a slow salted hash,
sessions are signed with a random key made on this robot (data/web_auth.json), invite links carry a long random
token, and guessing (password or pairing code) is slowed down by Throttle."""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque

from .core import DATA

AUTH_FILE = DATA / "web_auth.json"
SESSION_DAYS = {"owner": 30, "guest": 7}


class Throttle:
    """Slows down guessing of the owner password and of pairing codes.

    Wrong tries are counted for each client address. The first FREE are free (a typing mistake costs nothing); after
    that the client must wait, and the wait doubles with every further wrong try (LOCK_FIRST up to LOCK_MAX seconds).
    A right answer, or WINDOW seconds without a wrong try, forgets the client. Password checks are also limited for
    all clients together (PW_PER_MIN): each one costs real CPU time on a small board."""
    FREE, WINDOW, LOCK_FIRST, LOCK_MAX, PW_PER_MIN = 5, 600, 30, 3600, 20

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = {}            # address -> [wrong tries, time of the last one, locked until]
        self.pw_times = deque()

    def wait_s(self, client):
        """Seconds this client must still wait before it may try again (0 = go ahead)."""
        now = time.time()
        with self.lock:
            c = self.clients.get(client)
            if not c:
                return 0
            if now >= c[2] and now - c[1] > self.WINDOW:
                del self.clients[client]
                return 0
            return max(0, int(c[2] - now + 0.999))

    def fail(self, client):
        now = time.time()
        with self.lock:
            c = self.clients.setdefault(client, [0, now, 0.0])
            c[0] += 1
            c[1] = now
            if c[0] >= self.FREE:
                c[2] = now + min(self.LOCK_MAX, self.LOCK_FIRST * 2 ** min(20, c[0] - self.FREE))
            if len(self.clients) > 5000:                 # never grow without limit: forget the longest-quiet ones
                for k in sorted(self.clients, key=lambda k: self.clients[k][1])[:1000]:
                    del self.clients[k]
            return c[0]

    def ok(self, client):
        with self.lock:
            self.clients.pop(client, None)

    def password_slot(self):
        """True if one more password check may run this minute (all clients together)."""
        now = time.time()
        with self.lock:
            while self.pw_times and now - self.pw_times[0] > 60:
                self.pw_times.popleft()
            if len(self.pw_times) >= self.PW_PER_MIN:
                return False
            self.pw_times.append(now)
            return True


class WebAuth:
    def __init__(self, robot):
        self.r = robot
        self.data = json.loads(AUTH_FILE.read_text()) if AUTH_FILE.exists() else {}
        if "secret" not in self.data:
            self.data["secret"] = secrets.token_hex(32)
            self._save()
        self.codes = {}   # one-time pairing codes and invite tokens: code -> (role, expires)
        self.codes_lock = threading.Lock()

    def _save(self):
        AUTH_FILE.write_text(json.dumps(self.data, indent=1))
        AUTH_FILE.chmod(0o600)

    # ---------- password ----------
    def set_password(self, pw):
        salt = secrets.token_hex(16)
        self.data["pw"] = {"salt": salt, "hash": hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()}
        self._save()

    def check_password(self, pw):
        p = self.data.get("pw")
        if not p:
            return False
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(p["salt"]), 200_000).hex()
        return hmac.compare_digest(h, p["hash"])

    @property
    def has_password(self):
        return "pw" in self.data

    # ---------- one-time codes (QR pairing, guest invites) ----------
    def _add_code(self, code, role, minutes):
        now = time.time()
        with self.codes_lock:
            for k in [k for k, v in self.codes.items() if v[1] < now]:     # forget the expired ones
                del self.codes[k]
            self.codes[code] = (role, now + minutes * 60)
        return code

    def new_code(self, role="owner", minutes=5):
        """A short code a person can read off the robot's screen and type (QR pairing). Short codes live for minutes
        only; anything that lasts longer must be a new_token."""
        return self._add_code("".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(6)), role, minutes)

    def new_token(self, role="guest", minutes=24 * 60):
        """A long random token for a link that is sent to someone (guest invites): it cannot be guessed."""
        return self._add_code(secrets.token_urlsafe(24), role, minutes)

    def redeem(self, code):
        code = (code or "").strip()
        with self.codes_lock:
            role, exp = self.codes.pop(code, None) or self.codes.pop(code.upper(), (None, 0))
        return role if role and time.time() < exp else None

    # ---------- sessions ----------
    def _sign(self, payload):
        return hmac.new(bytes.fromhex(self.data["secret"]), payload, hashlib.sha256).hexdigest()

    def make_cookie(self, role):
        body = base64.urlsafe_b64encode(json.dumps({"role": role, "exp": int(time.time() + SESSION_DAYS[role] * 86400),
                                                    "v": self.data.get("gen", 0)}).encode())
        return f"{body.decode()}.{self._sign(body)}", SESSION_DAYS[role] * 86400

    def role_from_cookie(self, cookie_header):
        for part in (cookie_header or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "robot_session" and "." in v:
                body, sig = v.rsplit(".", 1)
                if hmac.compare_digest(sig, self._sign(body.encode())):
                    try:
                        d = json.loads(base64.urlsafe_b64decode(body))
                    except ValueError:
                        return None
                    if d.get("exp", 0) > time.time() and d.get("v", 0) == self.data.get("gen", 0):
                        return d.get("role")
        return None

    def revoke_all(self):
        """Sign everyone out (bumps the session generation)."""
        self.data["gen"] = self.data.get("gen", 0) + 1
        self._save()

    # ---------- what the public page may show ----------
    def public_sections(self):
        d = self.data.setdefault("public", {"face": True, "status": True, "camera": False, "said": False})
        d.setdefault("posts", True)
        return d

    def set_public(self, sections):
        cur = self.public_sections()
        for k in cur:
            if k in sections:
                cur[k] = bool(sections[k])
        self._save()
