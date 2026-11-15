"""Shared robot state: paths, config, persistent memory (SQLite + JSONL), logging and a tiny event bus."""
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(os.environ.get("ROBOT_ROOT", "/root/robot"))
DATA = ROOT / "data"
PHOTOS = DATA / "photos"
KNOW = ROOT / "knowledge"
CONFIG_PATH = DATA / "config.json"
DB_PATH = DATA / "robot.db"
EXPERIENCE = KNOW / "experience_log.jsonl"

DEFAULT_CONFIG = {
    "tz": "UTC",                      # your time zone, e.g. "America/New_York" (data/config.json)
    "motors_enabled": False,          # owner must enable before any non-zero motor command is sent
    "motor_max": 60,                  # duty cap (of 127) once enabled
    "imu": {"source": "auto", "gyro_bias": [0, 0, 0], "up": [1, 0, 0], "forward": [0, 0, 1], "leveled": False},
    "camera": {"still_w": 1280, "still_h": 720, "live_w": 320, "live_h": 240, "live_fps": 15, "live_quality": 72},
    "api": {"port": 8080, "bind": "0.0.0.0"},
    "reactions": True,
    # for a Wi-Fi router that drops broadcasts: robotd tells these computers its address by unicast
    # (robotd/wifi_keep.py). Add yours in data/config.json: {"name": "laptop", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.20"}
    "wifi": {"keep": True, "keep_period_s": 20, "peers": []},
}


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = json.loads(json.dumps(DEFAULT_CONFIG))
        if self.path.exists():
            try:
                self._merge(self.data, json.loads(self.path.read_text()))
            except Exception as e:  # keep defaults if the file is damaged
                print("config load failed:", e)

    def _merge(self, base, new):
        for k, v in new.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._merge(base[k], v)
            else:
                base[k] = v

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)


class Store:
    """Long-term memory. events = everything notable; tests = every test run; kv = small facts."""

    def __init__(self, db=DB_PATH, experience=EXPERIENCE):
        self.experience = Path(experience)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(db), check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, msg TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS tests(id INTEGER PRIMARY KEY, ts REAL, test TEXT, ok INTEGER, summary TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, ts REAL);
            CREATE INDEX IF NOT EXISTS ev_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS t_test ON tests(test, ts);
            """
        )
        self.db.commit()

    def event(self, kind, msg, **data):
        with self.lock:
            self.db.execute("INSERT INTO events(ts,kind,msg,data) VALUES(?,?,?,?)", (time.time(), kind, msg, json.dumps(data)))
            self.db.commit()

    def test(self, test, ok, summary, **data):
        ts = time.time()
        with self.lock:
            self.db.execute("INSERT INTO tests(ts,test,ok,summary,data) VALUES(?,?,?,?,?)", (ts, test, int(ok), summary, json.dumps(data)))
            self.db.commit()
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)), "test": test,
               "result": "pass" if ok else "fail", "summary": summary}
        rec.update({k: v for k, v in data.items() if k != "image"})
        with open(self.experience, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def last_tests(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT test, ok, ts, summary FROM tests t WHERE ts = (SELECT MAX(ts) FROM tests WHERE test=t.test)"
            ).fetchall()
        return {r[0]: {"ok": bool(r[1]), "ts": r[2], "summary": r[3]} for r in rows}

    def kv_get(self, key, default=None):
        with self.lock:
            r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def kv_set(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv(key,value,ts) VALUES(?,?,?)", (key, json.dumps(value), time.time()))
            self.db.commit()

    def recent_events(self, n=50):
        with self.lock:
            return self.db.execute("SELECT ts, kind, msg FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()


class Bus:
    def __init__(self):
        self.handlers = defaultdict(list)

    def on(self, name, fn):
        self.handlers[name].append(fn)

    def emit(self, name, *a, **kw):
        for fn in list(self.handlers.get(name, [])):
            try:
                fn(*a, **kw)
            except Exception as e:
                print(f"handler error for {name}: {e!r}")


class RobotLog:
    def __init__(self, store, bus):
        self.lines = deque(maxlen=200)
        self.store = store
        self.bus = bus

    def __call__(self, msg, kind="info", persist=True, **data):
        line = time.strftime("%H:%M:%S ") + msg
        self.lines.append(line)
        print(line, flush=True)
        if persist:
            self.store.event(kind, msg, **data)
        self.bus.emit("log", line)


SECRETS = DATA / "secrets.env"


def load_secrets():
    """Load data/secrets.env (KEY=value lines, # comments) into the environment. Empty values are ignored."""
    import os
    if SECRETS.exists():
        for line in SECRETS.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"')
                if v:
                    os.environ[k.strip()] = v


def ensure_dirs():
    for p in (DATA, PHOTOS, KNOW):
        p.mkdir(parents=True, exist_ok=True)
