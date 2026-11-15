"""Offline test of robotd/blinkers.py with a simulated display, camera and screen board (no robot needed).
Needs numpy and OpenCV (the robot's venv has them; on a PC: a venv with numpy, opencv-python-headless, pillow).
Run:  python tools/sim_blinkers.py [model|plan|quick|map|pace|full|all]     (all = everything except full, about 1 minute)
It checks the planner (numbers never show a wrong segment, battery bars are right), the camera test (finds the display,
locates all 44 LEDs, memory stays small), the paced multiplexing (equal share per pattern, lost replies) and the animations."""
import json
import resource
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import robotd.blinkers as B

import tempfile
S = Path(tempfile.mkdtemp(prefix="blinkers_sim_"))
B.DATA = S / "fake_data"
B.DATA.mkdir(exist_ok=True)

# ---- the simulated display: where every LED really is in the 1280x1024 camera picture (truth) --------------------
TRUTH = {}
for k in (1, 2, 3):
    cx, cy = 650 + (k - 1) * 72, 480
    for s, (dx, dy) in {"A": (0, -30), "B": (14, -15), "C": (14, 15), "D": (0, 30), "E": (-14, 15), "F": (-14, -15), "G": (0, 0)}.items():
        TRUTH[f"d{k}{s}"] = (cx + dx, cy + dy)
for i, n in enumerate(("bat100", "bat75", "bat50", "bat25")):
    TRUTH[n] = (570, 445 + i * 18)
for i, n in enumerate(("outline_top", "outline_right", "outline_right_low", "outline_bottom", "outline_left_low", "outline_left_up")):
    TRUTH[n] = (540 + i * 8, 425 + i * 22)
for i, n in enumerate(("head_a", "head_b", "head_c", "head_d", "head_center", "head_right", "head_low_left", "head_low_left2", "head_right_low")):
    TRUTH[n] = (600 + (i % 3) * 14, 440 + (i // 3) * 22)
for i, n in enumerate(("text_a", "text_b", "text_c", "text_right")):
    TRUTH[n] = (660 + i * 45, 540)
assert set(TRUTH) == set(B.NAMES), set(B.NAMES) ^ set(TRUTH)
TRUE_WIRING = {i: i - 1 for i in range(1, 9)}      # how the simulated display is REALLY wired (contact -> pin)
TRUE_GPIO = dict(B.GPIO_DEFAULT)                    # GPIO backend: contact -> gpio line, really


def gpio_really_lit(steps):
    """LEDs that the GPIO scanner's steps light (the camera sees all steps at once)."""
    out = set()
    for hi, lo in steps:
        hi, lo = set(hi), set(lo)
        out |= {n for (a, c), n in B.LEDS.items() if TRUE_GPIO[a] in hi and TRUE_GPIO[c] in lo}
    return sorted(out)


def really_lit(v):
    """LEDs that light for byte v on the simulated display, whatever the robot's config believes."""
    return [n for (a, c), n in B.LEDS.items() if v >> TRUE_WIRING[a] & 1 and not v >> TRUE_WIRING[c] & 1]


class Bus:
    def __init__(self):
        self.h = {}

    def on(self, name, fn):
        self.h.setdefault(name, []).append(fn)

    def emit(self, name, *a):
        for fn in self.h.get(name, []):
            fn(*a)


class Cyd:
    """The screen board: executes pcf_write after `lat` seconds and answers with pcf_written."""
    up = True
    page = "face"

    def __init__(self, robot, lat=0.0):
        self.robot, self.lat, self.log = robot, lat, []

    def _exec(self, v):
        self.log.append((time.perf_counter(), v))
        self.robot.screen_value = v
        self.robot.bus.emit("cyd:pcf_written", {"t": "pcf_written", "ok": True})

    def send(self, msg):
        if msg.get("t") == "page":
            self.page = msg["name"]
        if msg.get("t") == "pcf_write":
            if self.lat:
                threading.Timer(self.lat, self._exec, (msg["v"],)).start()
            else:
                self._exec(msg["v"])
        return True

    def request(self, msg, reply_type, timeout=2.0):
        self.send(msg)
        time.sleep(self.lat)
        return {"t": reply_type, "ok": True}


class Cam:
    privacy = False
    lock = threading.RLock()
    live_owners = set()

    def _kill_live(self):
        pass

    def _spawn_live(self):
        pass


class Robot:
    def __init__(self, lat=0.0):
        self.bus = Bus()
        self.cfg = type("C", (), {"data": {}})()
        self.cam = Cam()
        self.cyd = Cyd(self, lat)
        self.screen_value = 0xFF
        self.logs = []
        self.desktop = type("D", (), {"open_app": lambda s, n: None})()

    def log(self, msg, kind="info", **kw):
        self.logs.append(msg)

    def cyd_bus(self):
        return ["0x20", "0x68"]

    def pcf_read(self):
        return self.screen_value


class FakeCamera:
    """Renders the camera picture for whatever byte the display holds when a frame is asked for."""

    def __init__(self, blk, w, h):
        self.blk, self.w, self.h = blk, w, h
        self.rng = np.random.default_rng(1)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def frame_after(self, t0):
        v = self.blk.r.screen_value
        gpio_mode = self.blk.gpio()
        img = np.full((self.h, self.w), 40, np.float32) + self.rng.normal(0, 2.0, (self.h, self.w)).astype(np.float32)
        img += np.linspace(0, 12, self.w, dtype=np.float32)[None, :]            # room light gradient
        lit = gpio_really_lit(self.blk.r.gpio_steps) if gpio_mode else really_lit(v)
        share = {}
        for n in lit:
            share[B.NAMES[n][0]] = share.get(B.NAMES[n][0], 0) + 1
        yy, xx = np.mgrid[0:self.h, 0:self.w]
        for n in lit:
            x, y = TRUTH[n]
            amp = 215.0 / (share[B.NAMES[n][0]] ** 1.5) if not gpio_mode else 230.0 / max(1, len(self.blk.r.gpio_steps)) ** 0.5
            x0, x1, y0, y1 = int(x) - 12, int(x) + 13, int(y) - 12, int(y) + 13
            img[y0:y1, x0:x1] += amp * np.exp(-((xx[y0:y1, x0:x1] - x) ** 2 + (yy[y0:y1, x0:x1] - y) ** 2) / (2 * 2.6 ** 2))
        bgr = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        return cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def make(lat=0.0, backend="pcf"):
    r = Robot(lat)
    r.gpio_steps = []
    if backend == "pcf":
        r.cfg.data["blinkers"] = {"wired_v": 3, "backend": "pcf"}
    else:
        r.cfg.data["blinkers"] = {"wired_v": 3, "backend": "gpio", "gpio_lines": {str(k): v for k, v in B.GPIO_DEFAULT.items()}}
    blk = B.Blinkers(r)
    blk._camera = lambda w, h: FakeCamera(blk, w, h)
    if backend == "gpio":
        def raw(steps, dwell_us=None):
            r.gpio_steps = [list(x) for x in steps]
        blk._gpio_raw = raw
        blk.gpio_stats = lambda: {"steps_per_s": 0}
    return r, blk


def check(name, ok, extra=""):
    ok = bool(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    return ok


def test_model():
    r, b = make()
    ok = check("default wiring is all 8 contacts", b.wired() == {i: i - 1 for i in range(1, 9)})
    ok &= check("44 LEDs reachable", len(b.reachable()) == 44)
    ok &= check("254 patterns", len(set(b.patterns())) == 254, str(len(b.patterns())))
    ok &= check("8 column patterns minus the empty cathode 8", len(b.column_patterns()) == 7, str(len(b.column_patterns())))
    ok &= check("44 pair patterns", len(b.pair_patterns()) == 44)
    # old stored config (4 wires, no version) is upgraded once
    r2 = Robot()
    r2.cfg.data["blinkers"] = {"wired": {"1": 0, "2": 1, "3": 2, "4": 3}}
    b2 = B.Blinkers(r2)
    ok &= check("old 4-wire config upgraded to 8 and to the GPIO backend", len(b2.wired()) == 8 and b2.gpio() and r2.cfg.data["blinkers"]["wired_v"] == 3)
    r3 = Robot()
    r3.cfg.data["blinkers"] = {"wired_v": 3, "backend": "pcf", "wired": {str(i): 8 - i for i in range(1, 9)}}
    b3 = B.Blinkers(r3)
    ok &= check("a learned PCF wiring is kept (no migration on version 3)", b3.wired() == {i: 8 - i for i in range(1, 9)} and not b3.gpio())
    # column minus pair isolates one LED in the model
    w = set(b.wired())
    for (a, c), name in B.LEDS.items():
        pc, pca = b.lit(b.byte_for(w - {c})), b.lit(b.byte_for(w - {a, c}))
        if name not in pc or name in pca:
            ok &= check(f"column/pair for {name}", False)
    ok &= check("column pattern lights the LED, pair pattern does not (all 44)", True)
    return ok


def test_plan():
    r, b = make()
    ok = True
    digits = b.digit_leds()
    res = b.show_number("777", 0.2)
    wanted = set(b.number_leds("777"))
    shown = set(res["lit"]) | set(res["extra"])
    ok &= check("777: shown, 3 patterns", res["ok"] and len(res["patterns"]) == 3, str(res.get("bytes")))
    ok &= check("777: every wanted segment lit", wanted <= shown)
    ok &= check("777: no wrong digit segment ever lights", not ((shown & digits) - wanted), str(sorted((shown & digits) - wanted)))
    ok &= check("777: extras only in head/outline/text", all(B.group_of(n) != "digit" and not n.startswith("bat") for n in res["extra"]), str(res["extra"]))
    time.sleep(0.5)
    ok &= check("'1' is refused, not shown wrong", not b.show_number("1", 0.2)["ok"])
    ok &= check("'12' is refused", not b.show_number("12", 0.2)["ok"])
    for ch in "0123456789":
        res = b.show_number(ch, 0.2)
        shown = set(res.get("lit", [])) | set(res.get("extra", []))
        wrong = (shown & digits) - set(b.number_leds(ch))
        if res["ok"]:
            ok &= check(f"number '{ch}': no wrong segment", not wrong)
        time.sleep(0.05)
    for pct, want in ((0, []), (25, ["bat25"]), (50, ["bat25", "bat50"]), (75, ["bat25", "bat50", "bat75"]), (100, ["bat25", "bat50", "bat75", "bat100"])):
        res = b.show_battery(pct, 0.2)
        shown = set(res.get("lit", [])) | set(res.get("extra", []))
        bars = {n for n in shown if n.startswith("bat")}
        if pct:
            ok &= check(f"battery {pct}%: exactly the right bars", res["ok"] and bars == set(want), f"{sorted(bars)} extras {res['extra']}")
        else:
            ok &= check("battery 0%: nothing to show", not res["ok"])
        time.sleep(0.05)
    p = b.plan(b.number_leds("888"), True)
    ok &= check("888: clean, 4 patterns, nothing extra", not p["extra"] and not p["missing"] and len(p["patterns"]) == 4, str(p["bytes"]))
    p = b.plan(["bat100", "d2A"], True, forbid={"d1A"})
    ok &= check("forbid keeps a LED dark", "d1A" not in p["extra"] and "d1A" not in p["lit"])
    b.off()
    return ok


def test_wiring():
    """The owner's 8 wires are not on the pins the config says: the map test must find the real order and fix the config."""
    global TRUE_WIRING
    ok = True
    for label, true in (("contacts 1 and 8 on P3 and P7 (like the robot on 2026-11-12)", {1: 3, 2: 0, 3: 1, 4: 2, 5: 4, 6: 5, 7: 6, 8: 7}),
                        ("fully reversed", {i: 8 - i for i in range(1, 9)}),
                        ("random", {1: 5, 2: 2, 3: 7, 4: 0, 5: 3, 6: 6, 7: 1, 8: 4})):
        TRUE_WIRING = true
        r, b = make()
        before = b.show_battery(100, 0.1)
        wrong = really_lit(before["patterns"][0])
        time.sleep(0.3)
        res = b.experiment(mode="map", settle=0.0)
        s = res["summary"]
        print(f"   {label}: before, 'Full battery' really lit {wrong}; test: wiring {s.get('wiring')} score {s.get('wiring_score')}")
        ok &= check(f"wiring/{label}: learned and config fixed", s.get("wiring_found") and b.wired() == true, f"{b.wired()} vs {true}")
        ok &= check(f"wiring/{label}: 44/44 LEDs located, layout ok", s["leds_located"] == 44 and s["layout_ok"], f"{s['leds_located']} {res.get('layout')}")
        after = b.show_battery(100, 0.1)
        lit = set().union(*(set(really_lit(v)) for v in after["patterns"]))
        ok &= check(f"wiring/{label}: 'Full battery' now lights all 4 bars", {"bat25", "bat50", "bat75", "bat100"} <= lit, str(sorted(lit)))
        n7 = b.show_number("777", 0.1)
        lit7 = set().union(*(set(really_lit(v)) for v in n7["patterns"]))
        ok &= check(f"wiring/{label}: '777' really shows 777, no wrong segment",
                    set(b.number_leds("777")) <= lit7 and not ((lit7 & b.digit_leds()) - set(b.number_leds("777"))), str(sorted(lit7 & b.digit_leds())))
        b.off()
    # the head's LEDs behind one diffuser (they light the same spot, like the real display), wired like the robot
    global TRUTH
    keep = dict(TRUTH)
    for n in keep:
        if n.startswith("head"):
            TRUTH[n] = (614, 452) if n in ("head_a", "head_b", "head_c", "head_d") else (616, 470)
    TRUE_WIRING = {1: 3, 2: 0, 3: 1, 4: 2, 5: 4, 6: 5, 7: 6, 8: 7}
    r, b = make()
    res = b.experiment(mode="map", settle=0.0)
    s_ = res["summary"]
    print(f"   head behind a diffuser: {res['wiring']['score']}, contradictions {res['wiring']['contradictions']}, exact {s_['leds_located']} approx {s_['leds_approx']}")
    ok &= check("wiring/head behind one diffuser: learned", b.wired() == TRUE_WIRING, f"{s_.get('wiring')} {res['wiring'].get('layout')}")
    TRUTH = dict(keep)
    # the display upside down in the camera's view (and wired in a random order)
    keep = dict(TRUTH)
    TRUTH = {n: (1440 - x, 960 - y) for n, (x, y) in keep.items()}
    TRUE_WIRING = {1: 6, 2: 4, 3: 0, 4: 7, 5: 2, 6: 1, 7: 5, 8: 3}
    r, b = make()
    res = b.experiment(mode="map", settle=0.0)
    ok &= check("wiring/upside down in view: learned", b.wired() == TRUE_WIRING and res["summary"]["layout_ok"],
                f"{res['summary'].get('wiring')} {res.get('layout')}")
    TRUTH = keep
    TRUE_WIRING = {i: i - 1 for i in range(1, 9)}
    return ok


def test_text():
    """Text and scrolling never light a wrong digit segment; what cannot be shown cleanly is refused or skipped."""
    r, b = make()
    ok = True
    digits = b.digit_leds()
    shown, refused = [], []
    for t in ["72", "88", "9", "A", "P", "72°", "H80", "L65", "SUN", "FOG", "0", "23", "1", "45"]:
        res = b.show_text(t, 0.05)
        if res["ok"]:
            lit = set().union(*(set(really_lit(v)) for v in [int(x, 16) for x in res["bytes"]]))
            want = {f"d{i + 1}{seg}" for i, ch in enumerate(res["shown_as"]) for seg in next(
                o for o in B.GLYPHS.get(ch, [""]) if {f"d{i + 1}{x}" for x in o} <= lit and not ({f"d{i + 1}{z}" for z in "ABCDEFG"} - {f"d{i + 1}{x}" for x in o}) & lit) } if True else set()
            wrong = {x for x in lit & digits if x[:2] in {"d1", "d2", "d3"}} - want
            ok &= check(f"text {t!r}: shown as {res['shown_as']!r}, no wrong segment", not wrong, str(sorted(wrong)))
            shown.append(t)
        else:
            refused.append(t)
        time.sleep(0.02)
    print(f"   shown: {shown}   refused (not clean with this wiring): {refused}")
    ok &= check("'1' alone is refused", "1" in refused)
    t0 = time.time()
    frames, report = b.scroll_frames("RAIN 72")
    dt = time.time() - t0
    print(f"   scroll 'RAIN 72': {len(frames)} frames, skipped {[w['window'] for w in report if not w['ok']]}, planned in {dt:.2f} s")
    for pats, leds in frames:
        lit = set().union(*(set(really_lit(v)) for v in pats))
        ok &= check(f"scroll frame: no digit segment lights that was not planned", (lit & digits) <= leds, str(sorted((lit & digits) - leds)))
    res = b.scroll("72 88", 50, 1.0)
    ok &= check("scroll starts", res["ok"], str(res))
    time.sleep(2.6)
    ok &= check("scroll ends released", r.screen_value == 0xFF)
    return ok


def test_gpio():
    """GPIO backend: pins switch off, so every text and every LED alone is clean; the camera test learns the GPIO wiring."""
    global TRUE_GPIO
    ok = True
    r, b = make(backend="gpio")
    digits = b.digit_leds()
    bad = []
    for t in ["SUN", "FOG", "L65", "H79", "75°", "1", "12", "45", "CLR", "SNO", "-5°", "100", "HI", "rAn", "dry"]:
        res = b.show_text(t, 0.05)
        lit = set(gpio_really_lit(r.gpio_steps)) if res.get("ok") else set()
        want = set()
        if res.get("ok"):
            for i, ch in enumerate(res["shown_as"]):
                want |= {f"d{i + 1}{x}" for x in B.GLYPHS.get(ch, B.GLYPHS.get(ch.upper(), [""]))[0]}
        if not res.get("ok") or lit != want:
            bad.append((t, res.get("shown_as"), sorted(lit ^ want)))
        time.sleep(0.03)
    ok &= check("gpio: every weather text shows exactly its segments", not bad, str(bad[:3]))
    frames, report = b.scroll_frames("RAIN 72 SUNNY")
    ok &= check("gpio: scrolling skips nothing", all(w["ok"] for w in report), str([w for w in report if not w["ok"]][:3]))
    res = b.show_battery(50, 0.05); time.sleep(0.02)
    ok &= check("gpio: battery 50% = exactly 2 bars, nothing else", set(gpio_really_lit(r.gpio_steps)) == {"bat25", "bat50"}, str(gpio_really_lit(r.gpio_steps)))
    res = b.show(["head_center"], None, 0.05); time.sleep(0.02)
    ok &= check("gpio: a single LED alone", gpio_really_lit(r.gpio_steps) == ["head_center"], str(gpio_really_lit(r.gpio_steps)))
    for name in ("shimmer", "scan", "chase", "pulse"):
        res = b.animate(b.named_frames(name), 50, 0.3)
        ok &= check(f"gpio: animation {name} plays", res["ok"] and res["frames"] >= 2, str(res)[:80])
        time.sleep(0.5)
    ok &= check("gpio: off after the animation", r.gpio_steps == [], str(r.gpio_steps))
    # the camera test learns a scrambled GPIO wiring
    TRUE_GPIO = {1: 10, 2: 2, 3: 18, 4: 19, 5: 3, 6: 0, 7: 7, 8: 1}
    r, b = make(backend="gpio")
    res = b.experiment(mode="map", settle=0.0)
    s_ = res["summary"]
    print(f"   gpio map: {s_.get('wiring_score')}, located {s_['leds_located']}/{s_['leds_total']}, wiring {s_.get('wiring')}")
    ok &= check("gpio: map test learns the real GPIO wiring", b.gpio_lines() == TRUE_GPIO, f"{b.gpio_lines()} vs {TRUE_GPIO}")
    ok &= check("gpio: all 44 LEDs located exactly, layout ok", s_["leds_located"] == 44 and s_["layout_ok"], f"{s_['leds_located']} {res.get('layout')}")
    res = b.show_text("SUN", 0.05); time.sleep(0.02)
    lit = set(gpio_really_lit(r.gpio_steps))
    ok &= check("gpio: after learning, SUN really shows S U N", lit == {"d1A", "d1C", "d1D", "d1F", "d1G", "d2B", "d2C", "d2D", "d2E", "d2F", "d3C", "d3E", "d3G"},
                str(sorted(lit)))
    TRUE_GPIO = dict(B.GPIO_DEFAULT)
    return ok


def run_exp(mode, settle=0.0):
    r, b = make()
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.time()
    res = b.experiment(mode=mode, settle=settle)
    dt = time.time() - t0
    rss = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss0) / 1024
    s = res["summary"]
    print(f"   {mode}: {dt:.1f}s, +{rss:.0f} MB peak, summary {json.dumps(s)}")
    ok = check(f"{mode}: display found", s["display_found"])
    roi = res["roi"]
    ok &= check(f"{mode}: roi covers the display", roi and roi[0] < 540 and roi[2] > 860 and roi[1] < 430 and roi[3] > 550, str(roi))
    ok &= check(f"{mode}: controls dark", s["controls_dark"])
    ok &= check(f"{mode}: no unexpected glow", not s["unexpected_glow"], str(s["unexpected_glow"]))
    ok &= check(f"{mode}: no error", not res["errors"], str(res["errors"]))
    if mode != "quick":
        ok &= check(f"{mode}: 44/44 LEDs located", s["leds_located"] == 44, f"{s['leds_located']}/{s['leds_total']}")
        errs = [((e["x"] + roi[0] - TRUTH[n][0]) ** 2 + (e["y"] + roi[1] - TRUTH[n][1]) ** 2) ** 0.5 for n, e in res["leds"].items() if e.get("seen")]
        ok &= check(f"{mode}: LED positions within 3 px of the truth", max(errs) < 3, f"max {max(errs):.1f} px, mean {np.mean(errs):.1f}")
        ok &= check(f"{mode}: layout looks like the owner's map", s["layout_ok"], json.dumps(res["layout"]))
        ok &= check(f"{mode}: leds.json + led_map.jpg saved", (B.DATA / "blinkers" / "leds.json").exists() and (B.DATA / "blinkers" / "led_map.jpg").exists())
        ok &= check(f"{mode}: layout() loads", b.layout() and len(b.layout()["leds"]) == 44)
    if mode == "full":
        ok &= check("full: 254 patterns + 2 controls filmed", len(res["patterns"]) == 256, str(len(res["patterns"])))
        ok &= check("full: sheets of 24", len(res["sheets"]) == 11, str(res.get("sheets")))
        ok &= check("full: memory stays small", rss < 150, f"{rss:.0f} MB")
    ok &= check(f"{mode}: progress finished", b.progress["running"] is False and b.progress.get("i") == b.progress.get("n"), str(b.progress))
    return ok, b, res


def test_pace():
    ok = True
    for lat, label in ((0.0, "instant screen"), (0.005, "screen answers after 5 ms")):
        r, b = make(lat)
        r.cyd.page = "face"
        info = b.show_number("777", 1.0)
        ok &= check(f"pace/{label}: 3 patterns", info["multiplexed"] and len(info["patterns"]) == 3)
        time.sleep(2.6)
        log = [x for x in r.cyd.log if x[1] != 0xFF]
        n = len(log)
        gaps = [(b_[0] - a_[0]) * 1000 for a_, b_ in zip(log, log[1:])]
        gaps = [g for g in gaps if g < 50]
        pats = set(info["patterns"])
        seq_ok = all(log[i][1] in pats for i in range(n))
        cnt = {p: sum(1 for x in log if x[1] == p) for p in pats}
        print(f"   {label}: {n} writes in the 1 s show, rate {b.rate}/s, gap median {np.median(gaps):.2f} ms p95 {np.percentile(gaps, 95):.2f} ms, per pattern {sorted(cnt.values())}, page now {r.cyd.page}")
        ok &= check(f"pace/{label}: rate close to 1/period (>= 250/s)", b.rate and b.rate >= 250, str(b.rate))
        ok &= check(f"pace/{label}: only the show patterns were sent", seq_ok)
        ok &= check(f"pace/{label}: every pattern gets an equal share", max(cnt.values()) - min(cnt.values()) <= 2, str(cnt))
        ok &= check(f"pace/{label}: screen ends released (0xFF) and back on the face", r.screen_value == 0xFF and r.cyd.page == "face", f"{r.screen_value:#x} {r.cyd.page}")
        ok &= check(f"pace/{label}: nothing left outstanding", b.sent - b.acked <= WINDOW_SLACK, f"sent {b.sent} acked {b.acked}")
    # sync mode (config pace=sync) on the face page stays ack-by-ack
    r, b = make(0.005)
    b.cfg()["pace"] = "sync"
    b.show_number("777", 0.6)
    time.sleep(2.0)
    print(f"   sync: rate {b.rate}/s")
    ok &= check("pace/sync: slower than the paced beat, never bunches", b.rate and 40 < b.rate < 250, str(b.rate))
    # a lost reply must not stall forever
    r, b = make(0.0)
    orig = r.cyd._exec
    cnt = {"n": 0}

    def lossy(v):
        cnt["n"] += 1
        r.cyd.log.append((time.perf_counter(), v))
        r.screen_value = v
        if cnt["n"] % 50:
            r.bus.emit("cyd:pcf_written", {"ok": True})        # every 50th reply is lost
    r.cyd._exec = lossy
    b.show_number("777", 1.0)
    time.sleep(2.6)
    ok &= check("pace/lost replies: keeps running (>= 100 writes)", b.rate and b.rate > 100, f"rate {b.rate} stalls {b.stalls}")
    return ok


WINDOW_SLACK = B.WINDOW + 2

if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    ok = True
    if what in ("model", "all"):
        ok &= test_model()
    if what in ("plan", "all"):
        ok &= test_plan()
    if what in ("quick", "all"):
        ok &= run_exp("quick")[0]
    if what in ("map", "all"):
        ok_, b, res = run_exp("map")
        ok &= ok_
        # flicker test code path (the fake camera shows one instant, so only the plumbing is checked here)
        b.r.cyd.page = "desktop"
        ft = b.flicker_test("777", 1.0)
        ok &= check("flicker_test runs and reports 3 digits", len(ft["digit_brightness"]) == 3, json.dumps(ft)[:160])
        # background start
        b.progress = {"running": False}
        out = b.start_experiment("quick")
        ok &= check("start_experiment returns at once", out["ok"] and b.progress["running"])
        ok &= check("a second start is refused while running", not b.start_experiment("quick")["ok"])
        for _ in range(60):
            if not b.progress["running"]:
                break
            time.sleep(0.5)
        ok &= check("background test finished", b.progress["running"] is False and b.progress.get("msg") == "done", str(b.progress))
    if what in ("gpio", "all"):
        ok &= test_gpio()
    if what in ("text", "all"):
        ok &= test_text()
    if what in ("wiring", "all"):
        ok &= test_wiring()
    if what in ("pace", "all"):
        ok &= test_pace()
    if what in ("full",):
        ok &= run_exp("full")[0]
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
