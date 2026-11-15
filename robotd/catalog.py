"""Live model catalog from OpenRouter (prices, context, vision/tool support), plus the robot's 'variety pack'.
Prices are fetched from https://openrouter.ai/api/v1/models and cached for 10 minutes, so they are always current."""
import re
import threading
import time

import requests

URL = "https://openrouter.ai/api/v1/models"
CACHE_S = 600

# Variety pack: (label, regex on the model id, prefer vision). The newest matching model with tool support wins,
# so the pack follows new releases automatically. Owner can pin exact ids in config brain.pack_pins.
PACK_RULES = [
    ("DeepSeek", r"^deepseek/deepseek-(chat|v\d)", False),
    ("GLM Flash", r"^z-ai/glm-[\d.]+-flash(x)?$", False),
    ("Gemini Flash", r"^google/gemini-[\d.]+-flash(?!.*(image|tts|audio))", True),
    ("Gemini Flash Lite", r"^google/gemini-[\d.]+-flash-lite", True),
    ("NVIDIA vision", r"^nvidia/.*(vl|vision|omni)", True),
    ("Qwen VL", r"^qwen/qwen[\d.]*-vl", True),
    ("Llama vision", r"^meta-llama/llama-[\d.]+-.*(vision|maverick|scout)", True),
    ("Mistral Small", r"^mistralai/mistral-small", True),
    ("GPT mini", r"^openai/gpt-[\d.o]+-mini", True),
    ("Claude Haiku", r"^anthropic/claude-.*haiku", True),
    ("Grok fast", r"^x-ai/grok-.*fast", False),
    ("Free pick", r":free$", True),
    ("Free pick 2", r":free$", False),
]


class Catalog:
    def __init__(self, robot):
        self.r = robot
        self.models = []
        self.fetched = 0
        self.lock = threading.Lock()
        self.error = ""

    def refresh(self, force=False):
        with self.lock:
            if not force and self.models and time.time() - self.fetched < CACHE_S:
                return self.models
            try:
                data = requests.get(URL, timeout=30).json()["data"]
            except Exception as e:
                self.error = str(e)
                return self.models
            out = []
            for m in data:
                p = m.get("pricing") or {}
                try:
                    pin, pout = float(p.get("prompt", 0)) * 1e6, float(p.get("completion", 0)) * 1e6
                except (TypeError, ValueError):
                    continue
                if pin < 0 or pout < 0 or m["id"].endswith(":batch"):   # router pseudo-models / batch-only listings
                    continue
                arch = m.get("architecture") or {}
                sp = m.get("supported_parameters") or []
                out.append({
                    "id": m["id"], "name": m.get("name", m["id"]), "company": m["id"].split("/")[0],
                    "in": round(pin, 4), "out": round(pout, 4), "ctx": m.get("context_length") or 0,
                    "vision": "image" in (arch.get("input_modalities") or []), "tools": "tools" in sp,
                    "audio": "audio" in (arch.get("input_modalities") or []),
                    "free": pin == 0 and pout == 0, "created": m.get("created") or 0,
                })
            self.models, self.fetched, self.error = out, time.time(), ""
            return out

    def get(self, mid):
        for m in self.refresh():
            if m["id"] == mid:
                return m
        return None

    def price(self, mid):
        m = self.get(mid)
        return (m["in"], m["out"]) if m else (None, None)

    def pack(self):
        """Resolve the variety pack against the live list (newest tool-capable match per rule)."""
        models = [m for m in self.refresh() if m["tools"]]
        pins = (self.r.cfg.get("brain") or {}).get("pack_pins") or {}
        chosen, used = [], set()
        for label, rx, want_vision in PACK_RULES:
            if label in pins:
                m = self.get(pins[label])
                if m:
                    chosen.append({**m, "slot": label})
                    used.add(m["id"])
                continue
            cands = [m for m in models if re.search(rx, m["id"]) and m["id"] not in used]
            if want_vision and any(c["vision"] for c in cands):
                cands = [c for c in cands if c["vision"]]
            if not cands:
                continue
            best = max(cands, key=lambda m: (m["created"], -m["in"]))
            used.add(best["id"])
            chosen.append({**best, "slot": label})
        return chosen

    def table(self, q="", vision=False, tools=False, free=False, sort="price", limit=200):
        rows = self.refresh()
        if q:
            ql = q.lower()
            rows = [m for m in rows if ql in m["id"].lower() or ql in m["name"].lower()]
        if vision:
            rows = [m for m in rows if m["vision"]]
        if tools:
            rows = [m for m in rows if m["tools"]]
        if free:
            rows = [m for m in rows if m["free"]]
        keys = {"price": lambda m: (m["in"] + m["out"] * 0.3), "context": lambda m: -m["ctx"], "company": lambda m: (m["company"], m["name"]),
                "newest": lambda m: -m["created"], "name": lambda m: m["name"].lower()}
        return sorted(rows, key=keys.get(sort, keys["price"]))[:limit]
