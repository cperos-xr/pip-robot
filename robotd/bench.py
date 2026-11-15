"""Standard robot benchmark: the same small tasks for every model, scored 0..1, with latency and cost recorded.
Results go to the bench_results table so models can be ranked per task over time."""
import io
import json
import re
import threading
import time
import base64

import requests
from PIL import Image, ImageDraw, ImageFont

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PERSONA = ("You are a small, curious, quirky home robot. Everything you write is spoken aloud by a tiny speaker, "
           "so answer in one to three short, plain sentences with no markdown, emoji or stage directions.")


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _img_shapes():
    im = Image.new("RGB", (480, 320), "white")
    d = ImageDraw.Draw(im)
    for x, y in ((60, 60), (220, 90), (380, 70)):
        d.ellipse((x - 35, y - 35, x + 35, y + 35), fill=(220, 30, 30))
    for x, y in ((120, 230), (330, 220)):
        d.rectangle((x - 35, y - 35, x + 35, y + 35), fill=(30, 60, 220))
    return im


def _img_text():
    im = Image.new("RGB", (560, 200), (245, 240, 225))
    d = ImageDraw.Draw(im)
    d.text((30, 30), "ROBOT 7429", fill="black", font=_font(64))
    d.text((30, 120), "battery: 86 %", fill=(60, 60, 60), font=_font(40))
    return im


def _b64(im):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


TOOLS = [{"type": "function", "function": {"name": "set_face", "description": "Change the robot's facial expression.",
          "parameters": {"type": "object", "properties": {"mood": {"type": "string", "enum": ["happy", "sad", "curious", "sleepy"]}},
                         "required": ["mood"]}}}]


def _sentences(t):
    return len([s for s in re.split(r"[.!?]+", t) if s.strip()])


# each task: id, title, needs_vision, builder -> (messages, use_tools), scorer(text, tool_calls) -> score 0..1
TASKS = [
    ("persona", "Spoken persona", False,
     lambda: ([{"role": "system", "content": PERSONA}, {"role": "user", "content": "A visitor walks in. Say hello and introduce yourself."}], False),
     lambda t, c: (1.0 if _sentences(t) <= 3 and len(t.split()) <= 60 else 0.5 if _sentences(t) <= 5 else 0.0) * (0.5 if re.search(r"[*#`]", t) else 1.0)),
    ("reasoning", "Reasoning", False,
     lambda: ([{"role": "user", "content": "A robot has 8 motor channels. It uses 2 for wheels and 3 for an arm, and one wheel channel breaks. "
                                            "How many channels are working but unused? Answer with just the number."}], False),
     lambda t, c: 1.0 if re.search(r"\b3\b", t) and not re.search(r"\b(2|4|5)\b", t) else 0.0),
    ("math", "Arithmetic", False,
     lambda: ([{"role": "user", "content": "What is 17 * 23 + 9? Answer with just the number."}], False),
     lambda t, c: 1.0 if "400" in t.replace(",", "") else 0.0),
    ("tool_use", "Tool use", False,
     lambda: ([{"role": "system", "content": PERSONA}, {"role": "user", "content": "Your owner just gave you new wheels! Show how you feel on your face."}], True),
     lambda t, c: 1.0 if any(x["name"] == "set_face" and json.loads(x.get("arguments") or "{}").get("mood") == "happy" for x in c)
     else 0.5 if c else 0.0),
    ("json", "Structured output", False,
     lambda: ([{"role": "user", "content": "Reply with ONLY a JSON object with keys \"mood\" (\"happy\" or \"sad\") and \"reason\" (short string) "
                                            "for this event: my battery just got fully charged."}], False),
     lambda t, c: _score_json(t)),
    ("vision_count", "Vision: counting", True,
     lambda: ([{"role": "user", "content": [{"type": "text", "text": "How many red circles are in this picture? Answer with just the number."},
                                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _b64(_img_shapes())}}]}], False),
     lambda t, c: 1.0 if re.search(r"\b3\b|three", t.lower()) else 0.0),
    ("ocr", "Vision: reading", True,
     lambda: ([{"role": "user", "content": [{"type": "text", "text": "What code and what battery percentage are written here? Answer briefly."},
                                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _b64(_img_text())}}]}], False),
     lambda t, c: (0.5 if "7429" in t else 0) + (0.5 if "86" in t else 0)),
]


def _score_json(t):
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return 0.0
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return 0.0
    return (0.5 if d.get("mood") == "happy" else 0) + (0.5 if isinstance(d.get("reason"), str) and d["reason"] else 0) - (0.25 if t.strip() != m.group(0) else 0)


class Bench:
    def __init__(self, robot):
        self.r = robot
        self.job = {"state": "idle"}
        self.lock = threading.Lock()
        robot.store.db.execute("CREATE TABLE IF NOT EXISTS bench_results(id INTEGER PRIMARY KEY, ts REAL, run TEXT, model TEXT, task TEXT, "
                               "score REAL, latency_ms INTEGER, in_tok INTEGER, out_tok INTEGER, cost REAL, output TEXT)")
        robot.store.db.commit()

    def _call(self, model, messages, use_tools):
        import os
        body = {"model": model, "messages": messages, "max_tokens": 600}
        if use_tools:
            body["tools"] = TOOLS
        t0 = time.time()
        r = requests.post(OPENROUTER_URL, timeout=120, json=body,
                          headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "X-Title": "ai-multi-bot benchmark"})
        dt = time.time() - t0
        r.raise_for_status()
        d = r.json()
        msg = d["choices"][0]["message"]
        calls = [{"name": c["function"]["name"], "arguments": c["function"].get("arguments")} for c in (msg.get("tool_calls") or [])]
        u = d.get("usage") or {}
        return (msg.get("content") or "").strip(), calls, dt, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("cost")

    def run_model(self, model, run_id):
        info = self.r.catalog.get(model) or {}
        pin, pout = info.get("in", 0) or 0, info.get("out", 0) or 0
        total = []
        for tid, title, needs_vision, build, score in TASKS:
            if needs_vision and info and not info.get("vision"):
                s, out, dt, ti, to, cost = 0.0, "(model has no vision - scored 0)", 0, 0, 0, 0.0
            else:
                try:
                    msgs, use_tools = build()
                    text, calls, dt, ti, to, c = self._call(model, msgs, use_tools)
                    s = max(0.0, min(1.0, score(text, calls)))
                    out = (text or json.dumps(calls))[:400]
                    cost = c if isinstance(c, (int, float)) else (ti * pin + to * pout) / 1e6
                except Exception as e:
                    s, out, dt, ti, to, cost = 0.0, f"error: {e}"[:400], 0, 0, 0, 0.0
            total.append(s)
            with self.r.store.lock:
                self.r.store.db.execute("INSERT INTO bench_results(ts,run,model,task,score,latency_ms,in_tok,out_tok,cost,output) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                        (time.time(), run_id, model, tid, s, int(dt * 1000), ti, to, cost, out))
                self.r.store.db.commit()
            self.job["done"] += 1
        return sum(total) / len(total)

    def start(self, models):
        import os
        if not os.environ.get("OPENROUTER_API_KEY"):
            return False, "needs OPENROUTER_API_KEY"
        if not self.lock.acquire(blocking=False):
            return False, "a benchmark is already running"
        run_id = time.strftime("%Y%m%d-%H%M%S")
        self.job = {"state": "running", "run": run_id, "models": models, "done": 0, "total": len(models) * len(TASKS), "current": ""}

        def go():
            try:
                for m in models:
                    self.job["current"] = m
                    sc = self.run_model(m, run_id)
                    self.r.log(f"benchmark {m}: {sc * 100:.0f}%")
                self.job["state"] = "done"
            except Exception as e:
                self.job["state"] = f"error: {e}"
            finally:
                self.lock.release()
        threading.Thread(target=go, daemon=True).start()
        return True, f"benchmarking {len(models)} model(s), {len(TASKS)} tasks each"

    def leaderboard(self):
        """Latest run per model: per-task scores, total, latency, cost."""
        with self.r.store.lock:
            rows = self.r.store.db.execute(
                "SELECT model, task, score, latency_ms, cost, run FROM bench_results b WHERE run = "
                "(SELECT MAX(run) FROM bench_results WHERE model=b.model)").fetchall()
        board = {}
        for model, task, score, lat, cost, run in rows:
            e = board.setdefault(model, {"model": model, "run": run, "tasks": {}, "latency_ms": 0, "cost": 0.0, "n": 0})
            e["tasks"][task] = round(score, 2)
            e["latency_ms"] += lat or 0
            e["cost"] += cost or 0
            e["n"] += 1
        out = []
        for e in board.values():
            e["score"] = round(sum(e["tasks"].values()) / max(1, len(e["tasks"])), 3)
            e["avg_latency_s"] = round(e["latency_ms"] / max(1, e["n"]) / 1000, 2)
            e["cost"] = round(e["cost"], 6)
            out.append(e)
        return sorted(out, key=lambda e: -e["score"])

    def tasks(self):
        return [{"id": t[0], "title": t[1], "vision": t[2]} for t in TASKS]
