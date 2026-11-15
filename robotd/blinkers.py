"""BLINKERS LED display (salvaged from a used vape) driven by the PCF8574 on the screen's I2C port (CN1).

Wiring (owner): PCF8574 P0..P7 -> display contacts 1..8 (P0..P3 since 2026-11-11, P4..P7 since 2026-11-12). Config
`blinkers.wired` maps display contact -> PCF8574 bit, so a different wiring is a config change only.

How the display works: eight contacts, no ground pin. Every LED sits between two contacts (anode -> cathode),
so it lights when its anode is HIGH and its cathode is LOW (charlieplexing). LEDS below is the owner's measured map.

What the PCF8574 can and cannot do: a pin written 0 sinks current (LOW). A pin written 1 is NOT off: it is a weak
~100 uA pull-up, which is just enough to light a white LED dimly. There is no third "disconnected" state, so every
wired contact is either HIGH or LOW, and one write lights EVERY LED whose anode is HIGH and cathode is LOW.
A single LED on its own is therefore usually impossible; `plan()` finds the patterns whose union is what you want
and `show()` flips between them quickly (multiplexing). The current is tiny (< 1 mA in total), so nothing can be
damaged, but it is dim. See knowledge/manual/blinkers.md.

Speed: the screen board handles the serial lines in one pass of its main loop. While the face animates a pass takes
15-30 ms, on a quiet page (the RoboOS desktop) it takes about 5 ms. A multiplexed show therefore moves the screen to
the quiet page and sends the patterns on a steady beat (`blinkers.period_ms`) without waiting for every reply.
"""
import itertools
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from .core import DATA
from .camera import DEV, split_frames

SEG = "ABCDEFG"
_DIGIT_ANODES = {1: (2, 3, 4, 5, 6, 7, 8), 2: (1, 3, 4, 5, 6, 7, 8), 3: (1, 2, 4, 5, 6, 7, 8)}
LEDS = {}        # (anode contact, cathode contact) -> LED name
for _d, _anodes in _DIGIT_ANODES.items():
    for _s, _a in zip(SEG, _anodes):
        LEDS[(_a, _d)] = f"d{_d}{_s}"          # digit 1..3 (left to right), segment A=top .. G=middle
LEDS.update({
    (1, 4): "bat100", (2, 4): "bat75", (3, 4): "bat50", (5, 4): "bat25",
    (6, 4): "outline_top", (7, 4): "outline_right", (8, 4): "outline_right_low",
    (1, 5): "outline_bottom", (2, 5): "outline_left_low", (3, 5): "outline_left_up",
    (4, 5): "head_a", (6, 5): "head_b", (7, 5): "head_c", (8, 5): "head_d",
    (1, 6): "head_center", (2, 6): "head_right", (3, 6): "head_low_left", (4, 6): "head_low_left2",
    (5, 6): "head_right_low", (7, 6): "text_a", (8, 6): "text_b",
    (1, 7): "text_c", (2, 7): "text_right",
})
NAMES = {v: k for k, v in LEDS.items()}
DIGITS = {"0": "ABCDEF", "1": "BC", "2": "ABDEG", "3": "ABCDG", "4": "BCFG", "5": "ACDFG", "6": "ACDEFG",
          "7": "ABC", "8": "ABCDEFG", "9": "ABCDFG", "-": "G", " ": "", "A": "ABCEFG", "C": "ADEF", "E": "ADEFG",
          "F": "AEFG", "H": "BCEFG", "L": "DEF", "P": "ABEFG", "U": "BCDEF", "O": "ABCDEF"}
BATTERY = ["bat25", "bat50", "bat75", "bat100"]
ROT180 = {"A": "D", "B": "E", "C": "F", "D": "A", "E": "B", "F": "C", "G": "G"}   # a segment seen upside down
# 7-segment font for text: each character with its shapes, best first (S looks like 5, O can be big or small, ...)
GLYPHS = {"0": ["ABCDEF"], "1": ["BC", "EF"], "2": ["ABDEG"], "3": ["ABCDG"], "4": ["BCFG"], "5": ["ACDFG"], "6": ["ACDEFG", "CDEFG"],
          "7": ["ABC", "ABCF"], "8": ["ABCDEFG"], "9": ["ABCDFG", "ABCFG"],
          "A": ["ABCEFG"], "B": ["CDEFG"], "C": ["ADEF", "DEG"], "D": ["BCDEG"], "E": ["ADEFG"], "F": ["AEFG"], "G": ["ACDEF"],
          "H": ["BCEFG", "CEFG"], "I": ["EF", "BC"], "J": ["BCDE", "BCD"], "K": ["BCEFG"], "L": ["DEF"], "M": ["ACEG", "ABCEF"],
          "N": ["CEG", "ABCEF"], "O": ["ABCDEF", "CDEG"], "P": ["ABEFG"], "Q": ["ABCFG"], "R": ["EG", "AEF"], "S": ["ACDFG"],
          "T": ["DEFG"], "U": ["BCDEF", "CDE"], "V": ["BCDEF", "CDE"], "W": ["BDF"], "X": ["BCEFG"], "Y": ["BCDFG"], "Z": ["ABDEG"],
          "-": ["G"], "_": ["D"], "=": ["DG"], "°": ["ABFG"], "'": ["B"], "?": ["ABEG"], " ": [""]}
WINDOW = 6                                     # paced multiplexing: at most this many writes ahead of the screen's replies
# GPIO backend (2026-11-12): display contacts straight on Orange Pi 26-pin header pins (gpiochip0 line = PA number)
GPIO_DEFAULT = {1: 1, 2: 7, 3: 0, 4: 3, 5: 19, 6: 18, 7: 2, 8: 10}       # contact -> line (header 11,12,13,15,16,18,22,26)
HEADER_PIN = {1: 11, 7: 12, 0: 13, 3: 15, 19: 16, 18: 18, 2: 22, 10: 26, 15: 19, 16: 21, 14: 23, 13: 24, 6: 7}
GPIO_FLAG = 0x10000                            # a camera-test pattern in GPIO pin space: FLAG | high mask << 8 | low mask
EXP_MODES = {"quick": "find the display and film the 'one cathode' patterns (about 30 s)",
             "map": "film every pin and pin pair, learn how the display is wired and where every LED is (about 1 min)",
             "full": "film all 254 patterns, to catch any ghost glow (about 10 min)"}


def group_of(name):
    """Which part of the display an LED belongs to (for colours in pictures)."""
    if name.startswith("bat") or name.startswith("outline"):
        return "battery"
    if name.startswith("head"):
        return "head"
    if name.startswith("text"):
        return "text"
    return "digit"


class Blinkers:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.RLock()     # one user of the expander at a time (show loop, experiment, writes)
        self.stop_ev = threading.Event()
        self.thread = None
        self.value = None                 # last byte written
        self.showing = None               # what is on right now (text)
        self.on = set()                   # LED names on right now (all patterns of a multiplexed show)
        self.rate = None                  # writes per second reached by the last multiplex loop
        self.writes = self.fails = self.stalls = 0
        self.sent = self.acked = 0        # pcf_write lines sent / confirmed by the screen
        self.ack_cv = threading.Condition()
        self.last_exp = None
        self.progress = {"running": False}   # camera test progress, shown in the RoboOS app
        self._exp_lock = threading.Lock()
        self._layout, self._layout_mt = None, 0
        self._quiet_prev = None           # screen page to go back to after a multiplexed show
        self._scan_proc = None            # GPIO backend: the scanner process (robotd/blinkers_scan.py)
        self._scan_lock = threading.Lock()
        robot.bus.on("cyd:pcf_written", self._on_ack)

    def _on_ack(self, msg):
        with self.ack_cv:
            self.acked += 1
            self.ack_cv.notify_all()

    def cfg(self):
        c = self.r.cfg.data.setdefault("blinkers", {})
        if c.get("wired_v", 0) < 2:       # 2026-11-12: the owner wired all 8 contacts (P0..P7 -> 1..8); the old default was 4
            c["wired"] = {str(i): i - 1 for i in range(1, 9)}
            c["wired_v"] = 2
        c.setdefault("wired", {str(i): i - 1 for i in range(1, 9)})
        c.setdefault("max_secs", 600)                              # longest show() before it switches itself off
        c.setdefault("period_ms", 2.0)                             # beat of a multiplexed show (one pattern every period)
        c.setdefault("quiet_screen", True)                         # move the screen to the quiet desktop page while multiplexing
        c.setdefault("pace", "auto")                               # "sync": wait for every reply (slower, never bunches up)
        if c.get("wired_v", 0) < 3:       # 2026-11-12: the owner moved the 8 wires to Orange Pi header GPIO pins
            c["backend"] = "gpio"
            c["gpio_lines"] = {str(k): v for k, v in GPIO_DEFAULT.items()}
            c["wired_v"] = 3
        c.setdefault("backend", "gpio")                            # "gpio" (Orange Pi header) or "pcf" (PCF8574 on the screen)
        c.setdefault("gpio_lines", {str(k): v for k, v in GPIO_DEFAULT.items()})
        c.setdefault("gpio_chip", "/dev/gpiochip0")
        c.setdefault("text_rot180", False)                         # text for someone who sees the display upside down
        c.setdefault("gpio_dwell_us", 2000)                        # how long each scan step stays on (3 steps: ~140 Hz)
        if c.get("gpio_dwell_us") == 1200:                         # the first value (saved by accident): lighter on the CPU
            c["gpio_dwell_us"] = 2000
        return c

    def gpio(self):
        return self.cfg().get("backend") == "gpio"

    def gpio_lines(self):
        return {int(k): int(v) for k, v in self.cfg()["gpio_lines"].items()}

    def gpio_pins(self):
        """The GPIO lines in a fixed order (pin index 0..7 of the camera test), whatever contact each one carries."""
        return sorted(set(self.gpio_lines().values()))

    def contacts(self):
        return set(self.gpio_lines()) if self.gpio() else set(self.wired())

    @staticmethod
    def _pname(pat):
        hi, lo = pat
        return f"{''.join(map(str, hi))}>{''.join(map(str, lo))}"

    def wired(self):
        return {int(k): int(v) for k, v in self.cfg()["wired"].items()}

    def byte_for(self, high):
        """PCF8574 byte: wired contacts in `high` released (weak HIGH), other wired contacts LOW, unwired bits released."""
        v = 0xFF
        for c, bit in self.wired().items():
            if c not in high:
                v &= ~(1 << bit)
        return v & 0xFF

    def high_for(self, v):
        return {c for c, bit in self.wired().items() if v >> bit & 1}

    def lit(self, v):
        """LEDs the model predicts are lit by byte v."""
        if v is None:
            return []
        if isinstance(v, (tuple, list)):                        # GPIO pattern: (HIGH contacts, LOW contacts), the rest off
            hi, lo = set(v[0]), set(v[1])
            return sorted(n for (a, c), n in LEDS.items() if a in hi and c in lo)
        w, hi = self.wired(), self.high_for(v)
        return sorted(n for (a, c), n in LEDS.items() if a in w and c in w and a in hi and c not in hi)

    def reachable(self):
        w = self.contacts()
        return sorted(n for (a, c), n in LEDS.items() if a in w and c in w)

    def patterns(self):
        """Every useful byte: each split of the wired contacts into a HIGH group and a LOW group."""
        w = sorted(self.wired())
        return [self.byte_for(set(hi)) for k in range(1, len(w)) for hi in itertools.combinations(w, k)]

    def column_patterns(self):
        """One cathode LOW and every other wired contact HIGH: all LEDs of that cathode light, each fed by its own anode
        (full brightness). One byte per cathode that has an LED."""
        w = self.wired()
        return [self.byte_for(set(w) - {c}) for c in sorted(w) if any(cc == c and a in w for (a, cc) in LEDS)]

    def pair_patterns(self):
        """For every LED (anode a, cathode c): the byte with both a and c LOW. Next to the column pattern of c it differs
        only by that one LED, so (column minus pair) shows where exactly that LED is."""
        w = self.wired()
        return [self.byte_for(set(w) - {a, c}) for (a, c) in LEDS if a in w and c in w and len(w) > 2]

    def plan(self, names, allow_extra=False, forbid=None):
        """Patterns whose union lights `names`. Clean patterns (nothing extra) first; with allow_extra, fill the gaps
        with the patterns that add the fewest unwanted LEDs. `forbid`: LEDs that must never light (a pattern that would
        light one is not used), e.g. the other segments of a digit or the wrong bars of a battery."""
        want = {str(n) for n in names}
        reach = set(self.reachable())
        target = want & reach
        if self.gpio():                   # pins can switch off: one step per cathode, exactly the wanted LEDs, nothing extra
            by_c = {}
            for n in target:
                a, c = NAMES[n]
                by_c.setdefault(c, set()).add(a)
            pats = [(tuple(sorted(by_c[c])), (c,)) for c in sorted(by_c)]
            return {"patterns": pats, "bytes": [self._pname(x) for x in pats], "lit": sorted(target), "extra": [], "missing": [],
                    "not_wired": sorted(want & set(NAMES) - reach), "unknown": sorted(want - set(NAMES))}
        forbid = set(forbid or ()) - target
        cands = {v: set(self.lit(v)) for v in self.patterns()}
        cands = {v: c for v, c in cands.items() if not c & forbid}
        good = {v: s for v, s in cands.items() if s and s <= target}
        chosen, covered, extras = [], set(), set()
        while good:
            best = max(good, key=lambda v: len(good[v] - covered))
            if not good[best] - covered:
                break
            chosen.append(best)
            covered |= good[best]
        missing = target - covered
        while allow_extra and missing:
            useful = {v: s for v, s in cands.items() if s & missing}        # only patterns that add something wanted
            if not useful:
                break
            # most wanted LEDs first, few NEW unwanted LEDs (already-lit extras are free), then the smaller pattern
            best = max(useful, key=lambda v: (len(useful[v] & missing) - 0.5 * len(useful[v] - target - extras), -len(useful[v])))
            chosen.append(best)
            extras |= cands[best] - target
            covered |= cands[best] & target
            missing = target - covered
        if allow_extra and len(chosen) > 1:                                  # drop a pattern if the others already cover its LEDs
            for v in list(chosen):
                rest = [u for u in chosen if u != v]
                if rest and set().union(*(cands[u] & target for u in rest)) >= cands[v] & target:
                    chosen = rest
            extras = set().union(*(cands[u] for u in chosen)) - target
            covered = set().union(*(cands[u] for u in chosen)) & target
        return {"patterns": chosen, "bytes": [f"0x{v:02X}" for v in chosen], "lit": sorted(covered), "extra": sorted(extras),
                "missing": sorted(missing), "not_wired": sorted(want & set(NAMES) - reach), "unknown": sorted(want - set(NAMES))}

    def plan_pref(self, names, allow_extra, forbids):
        """The plan for the strictest `forbid` set that still covers everything wanted (else the plan with the least missing)."""
        best = None
        for f in forbids or [set()]:
            p = self.plan(names, allow_extra, f)
            if not p["missing"]:
                return {**p, "forbidden": sorted(f)}
            if best is None or len(p["missing"]) < len(best["missing"]):
                best = {**p, "forbidden": sorted(f)}
        return best

    def digit_leds(self):
        return {n for n in NAMES if group_of(n) == "digit"}

    def seg(self, pos, s):
        """LED name of segment s of character position pos (1..3, left to right as the viewer reads), honouring
        blinkers.text_rot180 (the display seen upside down: digit order reversed, every segment turned)."""
        if self.cfg().get("text_rot180"):
            return f"d{4 - pos}{ROT180[s]}"
        return f"d{pos}{s}"

    def number_leds(self, text):
        text = str(text).upper()[-3:].rjust(3)
        out = []
        for i, ch in enumerate(text, 1):
            out += [self.seg(i, s) for s in DIGITS.get(ch, "")]
        return out

    def battery_leds(self, pct):
        n = max(0, min(4, int(round(float(pct) / 25.0))))
        return BATTERY[:n]

    # ------------------------------------------------------------------ hardware
    def _on_cyd(self):
        return any(a.startswith(("0x2", "0x3")) for a in self.r.cyd_bus())

    def write(self, v):
        """Write one byte to the expander and wait for the screen's confirmation."""
        v = int(v) & 0xFF
        if self._on_cyd():
            self.sent += 1
            rep = self.r.cyd.request({"t": "pcf_write", "v": v}, "pcf_written", 1.0)
            ok = bool(rep and rep.get("ok"))
        else:
            try:
                self.r.pcf.write(v)
                ok = True
            except OSError:
                ok = False
        self.writes += 1
        if not ok:
            self.fails += 1
            raise OSError("PCF8574 write failed (no answer from the screen's I2C port)")
        self.value = v
        self.r._pcf_last = v              # the housekeeping check of "inputs changed" must not log our own patterns
        return v

    def _send_nowait(self, v, on_cyd):
        """One write without waiting for the reply (paced multiplexing)."""
        v = int(v) & 0xFF
        if on_cyd:
            self.sent += 1
            if not self.r.cyd.send({"t": "pcf_write", "v": v}):
                raise OSError("the link to the screen is down")
        else:
            self.r.pcf.write(v)
        self.writes += 1
        self.value = v
        self.r._pcf_last = v

    def read(self):
        return self.r.pcf_read()

    # ------------------------------------------------------------------ GPIO backend
    def _scanner(self):
        """The scanner process (started on first use, restarted if it died)."""
        p = self._scan_proc
        if p and p.poll() is None:
            return p
        pins = self.gpio_pins()
        p = subprocess.Popen([sys.executable, str(Path(__file__).with_name("blinkers_scan.py")), self.cfg()["gpio_chip"],
                              ",".join(map(str, pins))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1)
        import select                     # wait until it holds the GPIO lines (python + gpiod take a second or two to start)
        r_, _, _ = select.select([p.stdout], [], [], 15.0)
        line = p.stdout.readline() if r_ else ""
        if '"ready"' not in line:
            err = (p.stderr.read() if p.poll() is not None else "")[-300:]
            p.kill()
            raise OSError(f"the GPIO scanner did not start: {err or 'no answer'}")
        self._scan_proc = p
        return p

    def _gpio_raw(self, steps, dwell_us=None):
        """steps: [[HIGH lines], [LOW lines]] each; the rest off. An empty list switches the display off."""
        with self._scan_lock:
            for attempt in range(2):
                p = self._scanner()
                try:
                    p.stdin.write(json.dumps({"steps": steps, "dwell_us": dwell_us or self.cfg()["gpio_dwell_us"]}) + "\n")
                    p.stdin.flush()
                    break
                except (BrokenPipeError, OSError):
                    err = (p.stderr.read() if p.poll() is not None else "")[-300:]
                    self._scan_proc = None
                    if attempt:
                        raise OSError(f"the GPIO scanner does not run: {err}")
        self.writes += 1

    def _gpio_set(self, pats):
        """Show GPIO patterns (HIGH contacts, LOW contacts); the scanner cycles through them."""
        L = self.gpio_lines()
        self._gpio_raw([[[L[a] for a in hi if a in L], [L[c] for c in lo if c in L]] for hi, lo in pats])

    def gpio_stats(self):
        p = self._scanner()
        with self._scan_lock:
            p.stdin.write(json.dumps({"stats": True}) + "\n")
            p.stdin.flush()
        import select
        r_, _, _ = select.select([p.stdout], [], [], 2.0)
        return json.loads(p.stdout.readline()) if r_ else {"error": "no answer from the scanner"}

    def off(self):
        self.stop_ev.set()
        t = self.thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(3)
        with self.lock:
            if self.gpio():
                self._gpio_set([])
            else:
                self.write(0xFF)
            self.showing = None
            self.on = set()
        return "display off (all pins released)"

    # ------------------------------------------------------------------ showing things
    def show(self, leds=None, value=None, secs=10, allow_extra=False, label=None, forbids=None, need_all=False):
        """Light LEDs by name (multiplexing when needed) or one raw byte, for `secs` seconds (0 = the maximum).
        forbids: sets of LEDs that must stay dark, strictest first (see plan_pref). need_all: show nothing at all unless
        every wanted LED can be lit (a number with a missing segment would be a different number)."""
        if value is not None and self.gpio():          # a raw byte: bit n-1 = contact n HIGH (1) or LOW (0), as on the PCF8574
            v = int(value) & 0xFF
            pat = (tuple(c for c in sorted(self.contacts()) if v >> (c - 1) & 1), tuple(c for c in sorted(self.contacts()) if not v >> (c - 1) & 1))
            pats = [pat]
            info = {"patterns": pats, "bytes": [self._pname(pat)], "lit": self.lit(pat), "extra": [], "missing": []}
        elif value is not None:
            pats = [int(value) & 0xFF]
            info = {"patterns": pats, "bytes": [f"0x{pats[0]:02X}"], "lit": self.lit(pats[0]), "extra": [], "missing": []}
        else:
            info = self.plan_pref(leds or [], allow_extra, forbids)
            pats = info["patterns"]
        if not pats:
            return {"ok": False, "msg": "nothing I can light for that with the current wiring", **info}
        if need_all and info.get("missing"):
            return {"ok": False, "msg": "cannot show that cleanly: with every contact wired each pattern also lights other LEDs, "
                                        "and a wrong segment would make it a different number", **info}
        cap = float(self.cfg()["max_secs"])
        secs = cap if not secs else min(float(secs), cap)
        self.off()
        self.stop_ev.clear()
        self.showing = label or ", ".join(info["lit"] + info["extra"])
        self.on = set(info["lit"]) | set(info["extra"])
        self.thread = threading.Thread(target=self._loop, args=(pats, secs), daemon=True, name="blinkers")
        self.thread.start()
        return {"ok": True, "secs": secs, "multiplexed": len(pats) > 1, **info}

    def show_number(self, text, secs=10, allow_extra=True):
        """Show up to 3 characters. Other segments of the digits stay dark (a 7 must not look like a 9), extras may glow
        in the head, outline and text areas. Characters that cannot be shown cleanly (every '1', for example, because every
        wired contact is HIGH or LOW) are refused instead of shown wrong."""
        leds = self.number_leds(text)
        return self.show(leds, None, secs, allow_extra, label=f"number {str(text).strip()}",
                         forbids=[self.digit_leds() - set(leds)], need_all=True)

    def show_battery(self, pct, secs=10, allow_extra=True):
        """Show a battery level in 25 % steps. The wrong bars never light (a gauge must not lie); other digits stay dark
        when that is possible, otherwise their segments may glow."""
        leds = self.battery_leds(pct)
        bars = set(BATTERY) - set(leds)
        return self.show(leds, None, secs, allow_extra, label=f"battery {pct}%", forbids=[bars | self.digit_leds(), bars, set()])

    # ------------------------------------------------------------------ animations (the head's many LEDs are for a shimmer)
    def shimmer_frames(self, n=14):
        """Patterns that light head LEDs and nothing misleading (no digit segment, no battery bar), the fewest other LEDs
        first, ordered so that neighbouring frames light different head LEDs: the head twinkles."""
        head = {x for x in self.reachable() if x.startswith("head")}
        bad = self.digit_leds() | set(BATTERY)
        cands = []
        for v in self.patterns():
            lit = set(self.lit(v))
            if lit & head and not lit & bad:
                cands.append((len(lit - head), v, lit & head))
        cands.sort(key=lambda t: (t[0], t[1]))
        frames, seen, prev = [], set(), set()
        pool = list(cands)
        while pool and len(frames) < n:
            # prefer frames that light head LEDs not seen yet, then ones that overlap the previous frame least
            best = min(pool, key=lambda t: (-len(t[2] - seen), len(t[2] & prev), t[0]))
            pool.remove(best)
            frames.append(best[1])
            seen |= best[2]
            prev = best[2]
        return frames

    def named_frames(self, name):
        """Built-in animations: shimmer (head twinkle), scan (every cathode in turn: digits, battery, head, text),
        chase (the three digits one after another), pulse (the battery column on and off)."""
        cols = {c: v for c, v in zip(sorted(self.wired()), [self.byte_for(set(self.wired()) - {c}) for c in sorted(self.wired())])}
        have = [c for c in sorted(self.wired()) if any(cc == c and a in self.wired() for (a, cc) in LEDS)]
        if self.gpio():
            return self._gpio_frames(name)
        if name == "shimmer":
            return self.shimmer_frames()
        if name == "scan":
            return [cols[c] for c in have]
        if name == "chase":
            return [cols[c] for c in (1, 2, 3) if c in cols]
        if name == "pulse":
            return [cols[4], 0xFF] if 4 in cols else []
        raise ValueError("animation must be shimmer, scan, chase or pulse")

    def _gpio_frames(self, name):
        """Animations as LED sets (GPIO: any LED on its own). shimmer: the head's LEDs one after another in a sweep,
        with the whole head now and then; scan: every part in turn; chase: an 8 running over the digits; pulse: the battery."""
        reach = set(self.reachable())
        head = [n for n in ("head_a", "head_b", "head_d", "head_right", "head_right_low", "head_low_left2", "head_low_left", "head_c",
                            "head_center") if n in reach]
        lay = self.layout()
        if lay:                                   # sweep around the head as the camera saw it
            pos = {n: (e["x"], e["y"]) for n, e in lay["leds"].items() if n in head and e.get("box")}
            if len(pos) >= 3:
                import math
                cx = sum(x for x, _ in pos.values()) / len(pos)
                cy = sum(y for _, y in pos.values()) / len(pos)
                head = sorted(pos, key=lambda n: math.atan2(pos[n][1] - cy, pos[n][0] - cx)) + [n for n in head if n not in pos]
        digit = lambda d: [f"d{d}{s}" for s in SEG if f"d{d}{s}" in reach]
        battery = [n for n in BATTERY + ["outline_top", "outline_right", "outline_right_low", "outline_bottom", "outline_left_low",
                                         "outline_left_up"] if n in reach]
        if name == "shimmer":
            frames = [[h] for h in head] + [[a, b] for a, b in zip(head, head[1:] + head[:1])] + [head]
            return frames
        if name == "scan":
            return [[n] for n in BATTERY if n in reach] + [battery, head, digit(1), digit(2), digit(3),
                                                            [n for n in reach if n.startswith("text")]]
        if name == "chase":
            return [digit(1), digit(2), digit(3)]
        if name == "pulse":
            return [battery, []]
        raise ValueError("animation must be shimmer, scan, chase or pulse")

    def animate(self, frames, frame_ms=80, secs=10, label="animation"):
        """Play frames one after another, each for frame_ms, for `secs` seconds (0 = the maximum), then switch off.
        PCF8574: frames are bytes. GPIO: frames are LED-name lists (or bytes, read as HIGH/LOW per contact)."""
        if self.gpio():
            fr = []
            for f in frames:
                if isinstance(f, (list, tuple, set)):
                    fr.append(self.plan(list(f))["patterns"])
                else:
                    v = int(str(f), 0) & 0xFF
                    fr.append([(tuple(c for c in sorted(self.contacts()) if v >> (c - 1) & 1),
                                tuple(c for c in sorted(self.contacts()) if not v >> (c - 1) & 1))])
            if not fr:
                return {"ok": False, "msg": "no frames"}
            cap = float(self.cfg()["max_secs"])
            secs = cap if not secs else min(float(secs), cap)
            dt = max(0.04, float(frame_ms) / 1000.0)
            self.off()
            self.stop_ev.clear()
            lit = set()
            for f in fr:
                for pat in f:
                    lit |= set(self.lit(pat))
            self.showing, self.on = label, lit
            self.thread = threading.Thread(target=self._play, args=(fr, dt, secs), daemon=True, name="blinkers-anim")
            self.thread.start()
            return {"ok": True, "frames": len(fr), "frame_ms": round(dt * 1000), "secs": secs, "lit": sorted(lit)}
        pats = [int(str(v), 0) & 0xFF if not isinstance(v, int) else v & 0xFF for v in frames]
        if not pats:
            return {"ok": False, "msg": "no frames to play with the current wiring"}
        cap = float(self.cfg()["max_secs"])
        secs = cap if not secs else min(float(secs), cap)
        dt = max(0.04, float(frame_ms) / 1000.0)
        self.off()
        self.stop_ev.clear()
        lit = set()
        for v in pats:
            lit |= set(self.lit(v))
        self.showing, self.on = label, lit
        self.thread = threading.Thread(target=self._play, args=([[v] for v in pats], dt, secs), daemon=True, name="blinkers-anim")
        self.thread.start()
        return {"ok": True, "frames": [f"0x{v:02X}" for v in pats], "frame_ms": round(dt * 1000), "secs": secs, "lit": sorted(lit)}

    def _play(self, frames, frame_s, secs):
        """Play frames (each a list of patterns; several patterns are multiplexed within the frame) until secs, then off."""
        if self.gpio():
            try:
                end, n = time.time() + secs, 0
                while not self.stop_ev.is_set() and time.time() < end:
                    with self.lock:
                        self._gpio_set(frames[n % len(frames)])
                    n += 1
                    self.stop_ev.wait(max(0.0, min(end, time.time() + frame_s) - time.time()))
            except OSError as e:
                self.r.log(f"blinkers: {e}", "warn")
            finally:
                with self.lock:
                    try:
                        self._gpio_set([])
                    except OSError:
                        pass
                    self.showing = None
                    self.on = set()
            return
        try:
            with self.lock:
                multi = any(len(f) > 1 for f in frames)
                quiet = self._quiet_screen() if multi else False
                paced = quiet and self.cfg().get("pace", "auto") != "sync"
                end = time.time() + secs                     # the page switch must not eat into the show
                n = 0
                while not self.stop_ev.is_set() and time.time() < end:
                    pats = frames[n % len(frames)]
                    n += 1
                    fend = min(end, time.time() + frame_s)
                    if len(pats) == 1:
                        self.write(pats[0])
                        self.stop_ev.wait(max(0.0, fend - time.time()))
                    else:
                        self._multiplex(pats, fend, paced)
        except OSError as e:
            self.r.log(f"blinkers: {e}", "warn")
        finally:
            with self.lock:
                try:
                    self.write(0xFF)
                except OSError:
                    pass
                self.showing = None
                self.on = set()
            self._restore_screen()

    # ------------------------------------------------------------------ text on the three digits
    def _window_options(self, chars):
        """LED sets for exactly three characters, trying the other shapes of each character (best first)."""
        vs = [GLYPHS.get(c, GLYPHS.get(c.upper(), [""])) for c in chars]
        for combo in itertools.islice(itertools.product(*vs), 24):
            yield [self.seg(i + 1, sg) for i, segs in enumerate(combo) for sg in segs]

    def _render_window(self, chars):
        """The first shape combination of 3 characters that shows with no wrong digit segment, or None."""
        if all(c == " " for c in chars):
            return {"leds": [], "patterns": [0xFF], "bytes": ["0xFF"], "extra": []}
        for leds in self._window_options(chars):
            p = self.plan(leds, True, forbid=self.digit_leds() - set(leds))
            if not p["missing"] and p["patterns"]:
                return {"leds": leds, **p}
        return None

    def text_render(self, text):
        """Up to 3 characters on the digits: right-aligned first, then left, then centred; other shapes per character.
        Returns the first clean rendering, or None (with every wire on the PCF8574 many texts cannot be shown cleanly)."""
        t = [c for c in str(text).upper() if c in GLYPHS or c.upper() in GLYPHS] or [" "]
        if len(t) > 3:
            return None
        aligns = [t] if len(t) == 3 else [[" "] * (3 - len(t)) + t, t + [" "] * (3 - len(t)), ([" "] + t + [" "])[:3] if len(t) == 1 else None]
        for a in aligns:
            if a:
                r = self._render_window(a)
                if r:
                    return {**r, "shown_as": "".join(a)}
        return None

    def show_text(self, text, secs=10):
        """Show up to 3 characters (refused, not shown wrong, if this wiring cannot show them cleanly)."""
        r = self.text_render(text)
        if not r:
            return {"ok": False, "msg": f"cannot show {text!r} cleanly with this wiring (the PCF8574 has no off state; "
                                        "each digit can only show shapes that include its top two segments unless the neighbours help)"}
        self.off()
        self.stop_ev.clear()
        self.showing, self.on = f"text {str(text).strip()}", set(r["leds"]) | set(r["extra"])
        cap = float(self.cfg()["max_secs"])
        secs = cap if not secs else min(float(secs), cap)
        self.thread = threading.Thread(target=self._play, args=([r["patterns"]], secs, secs), daemon=True, name="blinkers-text")
        self.thread.start()
        return {"ok": True, "shown_as": r["shown_as"], "bytes": r["bytes"], "extra": r["extra"], "secs": secs}

    def scroll_frames(self, text):
        """3-character windows of the text sliding right to left; windows that cannot be shown cleanly are left out."""
        t = "   " + "".join(c if (c in GLYPHS or c.upper() in GLYPHS) else " " for c in str(text).upper()) + "   "
        frames, report = [], []
        for i in range(len(t) - 2):
            r = self._render_window(list(t[i:i + 3]))
            report.append({"window": t[i:i + 3], "ok": bool(r)})
            if r:
                frames.append((r["patterns"], set(r["leds"]) | set(r["extra"])))
        return frames, report

    def scroll(self, text, step_ms=450, secs=0):
        """Scroll text across the three digits. secs 0 = twice through (at most blinkers.max_secs)."""
        frames, report = self.scroll_frames(text)
        shown = [f for f in frames if f[0] != [0xFF]]
        if not shown:
            return {"ok": False, "msg": f"no part of {text!r} can be shown cleanly with this wiring", "windows": report}
        step = max(0.15, float(step_ms) / 1000.0)
        cap = float(self.cfg()["max_secs"])
        secs = min(cap, float(secs) if secs else 2 * len(frames) * step)
        self.off()
        self.stop_ev.clear()
        lit = set()
        for _, l in frames:
            lit |= l
        self.showing, self.on = f"scrolling {str(text).strip()}", lit
        self.thread = threading.Thread(target=self._play, args=([f[0] for f in frames], step, secs), daemon=True, name="blinkers-scroll")
        self.thread.start()
        bad = [w["window"] for w in report if not w["ok"]]
        return {"ok": True, "frames": len(frames), "skipped": bad, "step_ms": round(step * 1000), "secs": round(secs, 1)}

    def _quiet_screen(self):
        """A multiplexed show needs the screen board's main loop to spin fast: move it from the animated face to the
        RoboOS desktop (static) and remember where to go back. Returns True when the screen is quiet."""
        cyd = self.r.cyd
        if not self.cfg().get("quiet_screen", True) or not cyd.up:
            return cyd.page == "desktop"
        if cyd.page != "desktop":
            self._quiet_prev = cyd.page
            cyd.send({"t": "page", "name": "desktop"})
            try:
                self.r.desktop.open_app("blinkers")        # a static picture of the display, drawn once
            except Exception:
                pass
            time.sleep(0.8)                                 # the screen switches page and draws the first frame
        return True

    def _restore_screen(self):
        prev, self._quiet_prev = self._quiet_prev, None
        if prev and self.r.cyd.up and self.r.cyd.page == "desktop":
            self.r.cyd.send({"t": "page", "name": prev})

    def _multiplex(self, pats, end, paced):
        """Switch between the patterns until `end`. Paced: one pattern every `period_ms` on a steady beat, without waiting
        for each reply, so every pattern is on for the same time (at most WINDOW writes ahead of the screen's replies).
        Not paced: send the next one when the previous is confirmed (about 5 ms each on a quiet screen, 15+ ms on the face)."""
        n, t0 = 0, time.perf_counter()
        if not paced:
            while not self.stop_ev.is_set() and time.time() < end:
                self.write(pats[n % len(pats)])
                n += 1
        else:
            period = max(0.0005, float(self.cfg().get("period_ms", 2.0)) / 1000.0)
            on_cyd = self._on_cyd()
            nxt = t0
            while not self.stop_ev.is_set() and time.time() < end:
                if on_cyd and self.sent - self.acked >= WINDOW:
                    with self.ack_cv:
                        got = self.ack_cv.wait_for(lambda: self.sent - self.acked < WINDOW, 0.05)
                    if not got:                              # a reply got lost: do not wait for ever
                        self.stalls += 1
                        self.acked = self.sent
                        if self.stalls > 20:
                            raise OSError("the screen stopped answering")
                    nxt = time.perf_counter()
                self._send_nowait(pats[n % len(pats)], on_cyd)
                n += 1
                nxt += period
                d = nxt - time.perf_counter()
                if d > 0.0007:
                    time.sleep(d - 0.0005)
                while time.perf_counter() < nxt:
                    pass
                if time.perf_counter() - nxt > 0.02:         # fell behind (busy Pi): do not fire a burst to catch up
                    nxt = time.perf_counter()
        self.rate = round(n / max(0.001, time.perf_counter() - t0), 1)

    def _loop(self, pats, secs):
        end = time.time() + secs
        if self.gpio():
            try:
                with self.lock:
                    self._gpio_set(pats)
                self.stop_ev.wait(max(0, end - time.time()))
            except OSError as e:
                self.r.log(f"blinkers: {e}", "warn")
            finally:
                with self.lock:
                    try:
                        self._gpio_set([])
                    except OSError:
                        pass
                    self.showing = None
                    self.on = set()
            return
        try:
            with self.lock:
                if len(pats) == 1:
                    self.write(pats[0])
                    self.stop_ev.wait(max(0, end - time.time()))
                else:
                    quiet = self._quiet_screen()
                    end = time.time() + secs                 # the page switch must not eat into the show
                    self._multiplex(pats, end, paced=quiet and self.cfg().get("pace", "auto") != "sync")
        except OSError as e:
            self.r.log(f"blinkers: {e}", "warn")
        finally:
            with self.lock:
                try:
                    self.write(0xFF)
                except OSError:
                    pass
                self.showing = None
                self.on = set()
            self._restore_screen()

    def link_test(self, n=20):
        if self.gpio():
            return {"backend": "gpio", **self.gpio_stats()}
        with self.lock:
            ts = []
            for _ in range(n):
                t = time.time()
                self.write(0xFF)
                ts.append(time.time() - t)
        return {"writes": n, "avg_ms": round(1000 * sum(ts) / n, 1), "max_ms": round(1000 * max(ts), 1),
                "per_s": round(n / sum(ts), 1), "page": self.r.cyd.page}

    # ------------------------------------------------------------------ the webcam for a test
    class _Camera:
        """A private MJPG stream of the webcam for one test (the live stream stops meanwhile and comes back after)."""

        def __init__(self, robot, w, h):
            self.r, self.w, self.h = robot, w, h
            self.latest = {"f": None, "t": 0.0}
            self.cv = threading.Condition()
            self.proc = None

        def __enter__(self):
            r = self.r
            r.cam.lock.acquire()
            self.was_live = bool(r.cam.live_owners)
            r.cam._kill_live()
            self.proc = subprocess.Popen(["v4l2-ctl", "-d", DEV, f"--set-fmt-video=width={self.w},height={self.h},pixelformat=MJPG",
                                          "--stream-mmap=3", "--stream-to=-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            threading.Thread(target=self._reader, daemon=True).start()
            return self

        def _reader(self):
            buf = b""
            while self.proc.poll() is None:
                chunk = self.proc.stdout.read1(65536)
                if not chunk:
                    break
                buf += chunk
                frames, buf = split_frames(buf)
                if frames:
                    with self.cv:
                        self.latest["f"], self.latest["t"] = frames[-1], time.time()
                        self.cv.notify_all()

        def frame_after(self, t0):
            """The first frame that started after time t0 (JPEG bytes)."""
            with self.cv:
                end = time.time() + 4
                while self.latest["t"] <= t0 and time.time() < end:
                    self.cv.wait(end - time.time())
                if self.latest["t"] <= t0:
                    raise RuntimeError("camera stopped sending frames")
                return self.latest["f"]

        def __exit__(self, *exc):
            r = self.r
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                if self.was_live and not r.cam.privacy:
                    r.cam._spawn_live()
            finally:
                r.cam.lock.release()
            return False

    def _camera(self, w, h):
        return Blinkers._Camera(self.r, w, h)

    # ------------------------------------------------------------------ experiment: film patterns, measure, map the LEDs
    def start_experiment(self, mode="map", **kw):
        """Run the camera test in the background (it takes minutes); progress is in status()['progress']."""
        if self._exp_lock.locked():
            return {"ok": False, "msg": "a test is already running", "progress": self.progress}
        self.progress = {"running": True, "mode": mode, "i": 0, "n": 0, "msg": "starting", "t0": time.time()}

        def go():
            try:
                self.experiment(mode=mode, **kw)
            except Exception as e:
                self.progress = {**self.progress, "running": False, "error": str(e)}
                self.r.log(f"blinkers test failed: {e}", "warn")
        threading.Thread(target=go, daemon=True, name="blinkers-test").start()
        return {"ok": True, "msg": f"{mode} test started: {EXP_MODES.get(mode, '')}", "progress": self.progress}

    def experiment(self, w=1280, h=1024, reps=2, settle=0.35, mode="map"):
        """Light patterns, film each one (off and on, `reps` times) and measure what brightens.
        quick: the 'one cathode' patterns, enough to find the display. map: also every LED on its own (so every LED's place
        in the picture is measured). full: all 254 splits (a ghost hunt). Only crops around the display are saved
        (data/blinkers/exp_*), never whole room frames, because people may be in view."""
        import cv2
        import numpy as np
        r = self.r
        if mode not in EXP_MODES:
            raise ValueError(f"mode must be one of {', '.join(EXP_MODES)}")
        if r.cam.privacy:
            raise RuntimeError("the camera is switched off (privacy mode)")
        if not self._exp_lock.acquire(blocking=False):
            raise RuntimeError("a test is already running")
        try:
            wired = sorted(self.wired())
            self.off()
            out = DATA / "blinkers" / time.strftime("exp_%Y%m%d_%H%M%S")
            out.mkdir(parents=True, exist_ok=True)
            ctrl = [0xFF, self.byte_for(set())]                    # controls: nothing should light
            gp = self.gpio()
            pins = self.gpio_pins() if gp else []
            if gp:
                # GPIO pin space (independent of the configured wiring): all off and all LOW (controls), one pin LOW with
                # the others HIGH (finds the display), then each ordered pin pair alone: exactly one LED, or nothing
                pack = lambda hm, lm: GPIO_FLAG | (hm << 8) | lm
                full = (1 << len(pins)) - 1
                ctrl = [pack(0, 0), pack(0, full)]
                cols = [pack(full & ~(1 << q), 1 << q) for q in range(len(pins))]
                rest = [] if mode == "quick" else [pack(1 << a, 1 << b) for a in range(len(pins)) for b in range(len(pins)) if a != b]
            elif mode == "map":
                # raw pin space, independent of the configured wiring, so the test also learns how the display is
                # really wired (_learn_wiring): one pin LOW (8 bytes), then every two pins LOW (28 bytes)
                ctrl = [0xFF, 0x00]
                cols = [0xFF & ~(1 << b) for b in range(8)]
                rest = [0xFF & ~((1 << p) | (1 << q)) for p, q in itertools.combinations(range(8), 2)]
            else:
                cols = [v for v in dict.fromkeys(self.column_patterns()) if v not in ctrl] or self.patterns()[:8]
                rest = [] if mode == "quick" else [v for v in self.patterns() if v not in ctrl and v not in cols]
            order = ctrl + cols + rest
            self.progress = {"running": True, "mode": mode, "i": 0, "n": len(order), "msg": "finding the display", "t0": time.time()}
            res = {"t": time.time(), "dir": str(out), "mode": mode, "backend": "gpio" if gp else "pcf",
                   "wired": self.cfg()["gpio_lines"] if gp else self.cfg()["wired"], "link": self.link_test(),
                   "patterns": []}
            state = {"roi": None, "thr": 10.0, "noise": 0.0, "base": None}
            sigs, tiles, rows, errors = {}, {}, {}, []
            keep = set(cols) | set(rest if (mode == "map" or gp) else self.pair_patterns())

            def gray_full(j):
                g = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_GRAYSCALE)
                return cv2.GaussianBlur(g, (5, 5), 0).astype(np.int16)

            def locate(first):
                """Find the display: the one place that brightens every time a 'column' pattern comes on."""
                sig = {}
                for v, (ons, offs, _reads) in first.items():
                    m = None
                    for on, off in zip(ons, offs):
                        d = np.clip(gray_full(on) - gray_full(off), 0, 255).astype(np.uint8)
                        m = d if m is None else np.minimum(m, d)
                    sig[v] = m
                noise = max(float(np.percentile(sig[c], 99.99)) for c in ctrl)
                thr = max(10.0, noise * 1.5 + 4)
                acc = np.zeros_like(sig[ctrl[0]])
                for v in cols:
                    acc = np.maximum(acc, sig[v])
                mask = cv2.morphologyEx((acc > thr).astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
                roi = None
                n, lab, stats, cents = cv2.connectedComponentsWithStats(mask)
                if n > 1:
                    sums = [float(acc[lab == i].sum()) for i in range(1, n)]
                    best = 1 + int(np.argmax(sums))
                    cx, cy = cents[best]
                    keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 3 and abs(cents[i][0] - cx) < w * 0.2
                            and abs(cents[i][1] - cy) < h * 0.2]
                    x0 = min(stats[i, 0] for i in keep)
                    y0 = min(stats[i, 1] for i in keep)
                    x1 = max(stats[i, 0] + stats[i, 2] for i in keep)
                    y1 = max(stats[i, 1] + stats[i, 3] for i in keep)
                    bw, bh = max(x1 - x0, 60), max(y1 - y0, 40)
                    mx, my = int(bw * 0.6), int(bh * 0.8)
                    roi = [max(0, int(x0) - mx), max(0, int(y0) - my), min(w, int(x1) + mx), min(h, int(y1) + my)]
                    if (roi[2] - roi[0]) * (roi[3] - roi[1]) > 0.25 * w * h:
                        roi = None                                 # too big: probably something moving, not LEDs
                state.update(roi=roi, thr=thr, noise=noise)

            def process(v, ons, offs, reads):
                """Measure one pattern inside the display crop and keep only small things (a crop, a heat map)."""
                roi, thr = state["roi"], state["thr"]

                def gcrop(j):
                    g = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_GRAYSCALE)
                    return cv2.GaussianBlur(g[roi[1]:roi[3], roi[0]:roi[2]], (5, 5), 0).astype(np.int16)
                m = None
                for on, off in zip(ons, offs):
                    d = np.clip(gcrop(on) - gcrop(off), 0, 255).astype(np.uint8)
                    m = d if m is None else np.minimum(m, d)
                if v in keep:                      # only the patterns that locate LEDs are kept in memory
                    sigs[v] = m
                peak, px = int(m.max()), int((m > thr).sum())
                pred = self._exp_lit(v)
                share = {}
                for n_ in pred:
                    share[NAMES[n_][0]] = share.get(NAMES[n_][0], 0) + 1
                hi, lo = self._exp_hl(v)
                row = {"byte": self._exp_label(v), "high": hi, "low": lo, "predicted": pred,
                       "per_anode": max(share.values()) if share else 0,      # LEDs sharing one anode's ~100 uA (dimmer when >1)
                       "control": v in ctrl, "peak": peak, "bright_pixels": px, "lit_seen": peak > thr,
                       "readback": [f"{x:08b}" if x is not None else None for x in reads]}
                rows[v] = row
                img = cv2.imdecode(np.frombuffer(ons[0], np.uint8), cv2.IMREAD_COLOR)[roi[1]:roi[3], roi[0]:roi[2]]
                if state["base"] is None:
                    state["base"] = cv2.imdecode(np.frombuffer(offs[0], np.uint8), cv2.IMREAD_COLOR)[roi[1]:roi[3], roi[0]:roi[2]]
                    state["base_g"] = gcrop(offs[0])
                else:                              # did the display (or the camera) move since the first picture?
                    state["moved"] = max(state.get("moved", 0.0), float(np.abs(gcrop(offs[-1]) - state["base_g"]).mean()))
                heat = cv2.applyColorMap(np.clip(m.astype(np.int16) * 4, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
                tile = np.hstack([img, heat])
                tile = cv2.resize(tile, (360, max(1, int(tile.shape[0] * 360.0 / tile.shape[1]))))
                bar = np.zeros((34, 360, 3), np.uint8)
                if gp:                             # the wiring is not known yet: name the header pins, not LEDs
                    hm, lm = (v >> 8) & 0xFF, v & 0xFF
                    hp = [str(HEADER_PIN.get(pins[i], pins[i])) for i in range(len(pins)) if hm >> i & 1]
                    lp = [str(HEADER_PIN.get(pins[i], pins[i])) for i in range(len(pins)) if lm >> i & 1]
                    head = f"{row['byte']} peak {peak}"
                    lit = ("off" if not (hm or lm) else f"HIGH pin {'+'.join(hp) if len(hp) < 3 else 'all others'}, LOW pin {'+'.join(lp) if len(lp) < 3 else 'all'}")
                elif mode == "map":                # the wiring is not known yet: name the pins, not LEDs
                    low = [f"P{b}" for b in range(8) if not v >> b & 1]
                    head, lit = f"{row['byte']} peak {peak}", ("LOW: " + ",".join(low)) if low else "all pins released"
                else:
                    head, lit = f"{row['byte']} H{''.join(map(str, row['high']))} peak {peak}", ",".join(pred) or "nothing"
                cv2.putText(bar, head, (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
                cv2.putText(bar, lit[:58], (4, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (180, 220, 255), 1)
                jpg = cv2.imencode(".jpg", np.vstack([bar, tile]), [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
                tiles[v] = jpg
                (out / ("p_" + "".join(ch if ch.isalnum() else "_" for ch in row["byte"]) + ".jpg")).write_bytes(jpg)

            q = queue.Queue(maxsize=6)

            def worker():
                while True:
                    item = q.get()
                    if item is None:
                        return
                    try:
                        process(*item)
                    except Exception as e:        # one bad frame must not end the test
                        errors.append(f"0x{item[0]:02X}: {e!r}")

            wk = threading.Thread(target=worker, daemon=True, name="blinkers-analyse")
            with self.lock, self._camera(w, h) as cam:
                try:
                    time.sleep(2.5)                                 # let auto exposure settle

                    def shoot(v):
                        ons, offs, reads = [], [], []
                        for _ in range(reps):
                            self._exp_apply(None)
                            t0 = time.time()
                            time.sleep(settle)
                            offs.append(cam.frame_after(t0 + settle * 0.6))
                            self._exp_apply(v)
                            t0 = time.time()
                            time.sleep(settle)
                            ons.append(cam.frame_after(t0 + settle * 0.6))
                            try:
                                reads.append(None if gp else self.read())
                            except OSError:
                                reads.append(None)
                        self.progress["i"] += 1
                        return ons, offs, reads

                    first = {v: shoot(v) for v in ctrl + cols}
                    self.progress["msg"] = "measuring"
                    locate(first)
                    if state["roi"] is not None:
                        wk.start()
                        for v, (ons, offs, reads) in first.items():
                            q.put((v, ons, offs, reads))
                        first.clear()
                        self.progress["msg"] = "filming patterns"
                        for v in rest:
                            ons, offs, reads = shoot(v)
                            q.put((v, ons, offs, reads))
                finally:
                    try:
                        self._exp_apply(None)
                    except OSError:
                        pass
            if wk.is_alive():
                q.put(None)
                wk.join()
            self.progress["msg"] = "analysing"
            roi, thr = state["roi"], state["thr"]
            moved = state.get("moved", 0.0) > 6.0             # mean change of the dark display picture, 0-255
            res.update({"noise": round(state["noise"], 1), "threshold": round(thr, 1), "roi": roi, "resolution": [w, h],
                        "movement": round(state.get("moved", 0.0), 1), "moved": moved,
                        "errors": errors[:10]})
            for v in order:
                if v in rows:
                    res["patterns"].append(rows[v])

            # contact sheets: 24 pictures each
            names = [v for v in order if v in tiles]
            sheets = []
            for si in range(0, len(names), 24):
                chunk = [cv2.imdecode(np.frombuffer(tiles[v], np.uint8), cv2.IMREAD_COLOR) for v in names[si:si + 24]]
                th = max(t.shape[0] for t in chunk)
                chunk = [np.vstack([t, np.zeros((th - t.shape[0], 360, 3), np.uint8)]) for t in chunk]
                while len(chunk) % 3:
                    chunk.append(np.zeros_like(chunk[0]))
                sheet = np.vstack([np.hstack(chunk[i:i + 3]) for i in range(0, len(chunk), 3)])
                p = out / f"sheet_{si // 24 + 1:02d}.jpg"
                cv2.imwrite(str(p), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
                sheets.append(p.name)
            if sheets:
                res["sheets"] = sheets
                res["sheet"] = str(out / sheets[0])

            # where is every LED? column pattern minus the pair pattern leaves exactly that LED
            leds = {}
            if roi is not None and (mode == "map" or (gp and mode == "full")):
                if gp:                                      # each pin pair was lit on its own: its picture IS that LED
                    bitmap = {(a, b): {**self._blob(sigs[pack(1 << a, 1 << b)], thr), "loose": {}}
                              for a in range(len(pins)) for b in range(len(pins)) if a != b and pack(1 << a, 1 << b) in sigs}
                else:
                    bitmap = self._bit_matrix(sigs, thr)
                wl = self._learn_wiring(bitmap)
                if moved:                                   # pictures from before and after a move do not compare
                    wl = {**wl, "wiring": None, "changed": False, "why": "the display moved during the test: run it again"}
                res["wiring"] = wl
                wmap = wl.get("wiring")
                res["pin_matrix"] = {f"P{p}->P{q}": {"seen": e["seen"], "peak": e["peak"], "loose_peak": e.get("loose", {}).get("peak"),
                                                     "x": e.get("x"), "y": e.get("y"), "box": e.get("box")}
                                     for (p, q), e in sorted(bitmap.items())}
                if wmap:
                    for (a, c), name in LEDS.items():
                        if a in wmap and c in wmap and (wmap[a], wmap[c]) in bitmap:
                            e = dict(bitmap[(wmap[a], wmap[c])])
                            lo = e.pop("loose", {})
                            if not e["seen"] and lo.get("box"):         # behind a shared diffuser: where it got dimmer
                                e.update({"x": lo["x"], "y": lo["y"], "box": lo["box"], "approx": True})
                            leds[name] = {"anode": a, "cathode": c, **e}
                    if wl["changed"]:
                        if gp:
                            self.cfg()["gpio_lines"] = {str(c): pins[b] for c, b in sorted(wmap.items())}
                            txt = ", ".join(f"contact {c} -> header pin {HEADER_PIN.get(pins[b], pins[b])}" for c, b in sorted(wmap.items()))
                        else:
                            self.cfg()["wired"] = {str(c): b for c, b in sorted(wmap.items())}
                            txt = ", ".join(f"contact {c} -> P{b}" for c, b in sorted(wmap.items()))
                        try:
                            self.r.cfg.save()
                        except Exception:
                            pass
                        r.log("blinkers: learned the real wiring with the camera: " + txt)
                        for v, row in rows.items():             # predictions again, with the real wiring
                            row["predicted"] = self._exp_lit(v)
                            row["high"], row["low"] = self._exp_hl(v)
            elif roi is not None:
                for (a, c), name in LEDS.items():
                    if a not in wired or c not in wired:
                        continue
                    pc, pca = self.byte_for(set(wired) - {c}), self.byte_for(set(wired) - {a, c})
                    if pc not in sigs or pca not in sigs:
                        continue
                    leds[name] = {"anode": a, "cathode": c, **self._blob(self._led_diff(sigs[pc], sigs[pca], thr), thr)}
            res["leds"] = leds
            layout_ok = self._layout_checks(leds)
            res["layout"] = layout_ok
            if leds and any(e["seen"] for e in leds.values()):
                self._save_layout(res, leds, roi, state["base"], out)

            good = [p for p in res["patterns"] if not p["control"]]
            seen_leds = sum(1 for e in leds.values() if e["seen"])
            approx_leds = sum(1 for e in leds.values() if e.get("approx"))
            res["summary"] = {
                "mode": mode, "display_found": roi is not None, "patterns_tested": len(good),
                "patterns_seen_lit": sum(p["lit_seen"] for p in good),
                "patterns_predicted_lit": sum(1 for p in good if p["predicted"]),
                "predicted_but_dark": [p["byte"] for p in good if p["predicted"] and not p["lit_seen"]][:40],
                "unexpected_glow": [p["byte"] for p in good if not p["predicted"] and p["lit_seen"]][:40],
                "controls_dark": all(not p["lit_seen"] for p in res["patterns"] if p["control"]),
                "leds_located": seen_leds, "leds_approx": approx_leds, "leds_total": len(leds),
                "layout_ok": layout_ok.get("ok") if leds else None,
                "moved": moved,
            }
            if res.get("wiring"):
                wl = res["wiring"]
                res["summary"].update({"wiring": {str(c): (f"header pin {HEADER_PIN.get(pins[b], pins[b])}" if gp else f"P{b}")
                                                  for c, b in sorted((wl.get("wiring") or {}).items())},
                                       "wiring_learned": bool(wl.get("changed")), "wiring_score": wl.get("score"),
                                       "wiring_found": bool(wl.get("wiring"))})
            (out / "results.json").write_text(json.dumps(res, indent=1))
            self.last_exp = res
            self.progress = {**self.progress, "running": False, "msg": "done", "i": self.progress.get("n", 0)}
            s = res["summary"]
            r.log(f"blinkers test ({mode}): display {'found' if roi else 'NOT found'}, {s['patterns_seen_lit']}/{len(good)} patterns lit, "
                  f"{seen_leds}/{len(leds)} LEDs located, controls {'dark' if s['controls_dark'] else 'NOT dark'} -> {out}")
            return res
        finally:
            self.progress["running"] = False
            self._exp_lock.release()

    def _pin_map(self):
        """contact -> pin index of the camera test (PCF8574 bit, or position in gpio_pins())."""
        if self.gpio():
            pins = self.gpio_pins()
            return {c: pins.index(o) for c, o in self.gpio_lines().items()}
        return self.wired()

    def _exp_apply(self, v):
        """Show one camera-test pattern (None = everything off)."""
        if not self.gpio():
            self.write(0xFF if v is None else v)
            return
        pins = self.gpio_pins()
        hm, lm = ((v >> 8) & 0xFF, v & 0xFF) if v is not None else (0, 0)
        steps = [[[pins[i] for i in range(len(pins)) if hm >> i & 1], [pins[i] for i in range(len(pins)) if lm >> i & 1]]]
        self._gpio_raw(steps if (hm or lm) else [])

    def _exp_hl(self, v):
        """(HIGH contacts, LOW contacts) of a camera-test pattern with the configured wiring."""
        if not self.gpio():
            hi = self.high_for(v) & set(self.wired())
            return sorted(hi), sorted(set(self.wired()) - hi)
        pins, inv = self.gpio_pins(), {o: c for c, o in self.gpio_lines().items()}
        hm, lm = (v >> 8) & 0xFF, v & 0xFF
        return (sorted(inv[pins[i]] for i in range(len(pins)) if hm >> i & 1 and pins[i] in inv),
                sorted(inv[pins[i]] for i in range(len(pins)) if lm >> i & 1 and pins[i] in inv))

    def _exp_lit(self, v):
        if not self.gpio():
            return self.lit(v)
        hi, lo = self._exp_hl(v)
        return self.lit((tuple(hi), tuple(lo)))

    def _exp_label(self, v):
        if not self.gpio():
            return f"0x{v:02X}"
        pins = self.gpio_pins()
        hm, lm = (v >> 8) & 0xFF, v & 0xFF
        name = lambda m: "+".join(str(HEADER_PIN.get(pins[i], pins[i])) for i in range(len(pins)) if m >> i & 1)
        if not (hm or lm):
            return "off"
        if not hm:
            return "all LOW"
        return f"{name(hm) if bin(hm).count('1') < 3 else 'rest'}>{name(lm)}"

    @staticmethod
    def _blob(dmap, thr):
        """Peak, centre and box of the brightest spot in a difference picture."""
        import cv2
        import numpy as np
        pk = int(dmap.max())
        e = {"peak": pk, "seen": pk > thr}
        if e["seen"]:
            m = (dmap >= 0.6 * pk).astype(np.uint8)
            n, lab, stats, cents = cv2.connectedComponentsWithStats(m)
            ys, xs = np.unravel_index(int(dmap.argmax()), dmap.shape)
            k = int(lab[ys, xs])
            bx, by, bw, bh = (int(stats[k, i]) for i in range(4))
            e.update({"x": round(float(cents[k][0]), 1), "y": round(float(cents[k][1]), 1), "box": [bx, by, bx + bw, by + bh]})
        return e

    def _bit_matrix(self, sigs, thr):
        """Pin space: for every ordered pin pair (p, q), does an LED light from pin p (HIGH) to pin q (LOW), and where?
        column(q) has only q LOW; pair(p, q) has p and q LOW; column minus pair is the LED p -> q alone."""
        import numpy as np
        out = {}
        for q in range(8):
            cq = 0xFF & ~(1 << q)
            for p in range(8):
                pq = 0xFF & ~((1 << p) | (1 << q))
                if p != q and cq in sigs and pq in sigs:
                    e = self._blob(self._led_diff(sigs[cq], sigs[pq], thr), thr)
                    loose = self._blob(np.clip(sigs[cq].astype(np.int16) - sigs[pq], 0, 255).astype(np.uint8), thr)
                    e["loose"] = {k: loose[k] for k in ("peak", "x", "y", "box") if k in loose}
                    out[(p, q)] = e
        return out

    @staticmethod
    def _led_diff(col, pair, thr):
        """The LED that is lit in `col` and fully OFF in `pair`. With a second pin LOW the other LEDs of that cathode share
        their anode's current and only get dimmer; those spots are not this LED, so only spots that go (nearly) dark count."""
        import cv2
        import numpy as np
        c, p = col.astype(np.int16), pair.astype(np.int16)
        d = np.where((c > thr) & (p < 0.2 * c), c - p, 0).clip(0, 255).astype(np.uint8)
        return cv2.morphologyEx(d, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    def _learn_wiring(self, bitmap):
        """Which display contact is on which PCF8574 pin? Try every assignment of the contacts to pins (8! = 40320) and keep
        the one whose LEDs (the owner's map) agree best with the pin pairs that lit; ties are broken by the layout check."""
        import numpy as np
        contacts = sorted(self.contacts())
        n = len(contacts)
        E = np.zeros((8, 8), bool)
        for (p, q), e in bitmap.items():
            E[p, q] = bool(e.get("seen"))
        idx = {c: i for i, c in enumerate(contacts)}
        M = np.zeros((n, n), bool)
        for (a, c) in LEDS:
            if a in idx and c in idx:
                M[idx[a], idx[c]] = True
        perms = np.array(list(itertools.permutations(range(8), n)), dtype=np.int8)
        off = ~np.eye(n, dtype=bool)
        Ep = E[perms[:, :, None], perms[:, None, :]] & off
        # A pin pair that lit (its spot went fully dark) must be an LED of the map: a contradiction costs a lot. An LED of
        # the map that was not seen costs little: LEDs behind a shared diffuser (the head, light pipes) never go fully dark.
        contra = (Ep & ~M).sum(axis=(1, 2))
        explained = (Ep & M).sum(axis=(1, 2))
        score = explained - 4 * contra
        best = int(score.max())
        total = int((M & off).sum())
        cands = [dict(zip(contacts, (int(b) for b in perms[i]))) for i in np.flatnonzero(score == best)[:6000]]
        best_contra = int(contra[int(np.argmax(score))])
        # The map is symmetric (contacts 1 and 2 can swap, and so can 3, 4, 5 and 6, without changing which pairs have an
        # LED), so several orders fit equally well. Where the LEDs are in the picture decides: the best-shaped layout wins.
        scored = []
        for w in cands:
            leds = {name: bitmap[(w[a], w[c])] for (a, c), name in LEDS.items() if a in w and c in w and (w[a], w[c]) in bitmap}
            scored.append((self._layout_score(leds), w))
        top = max(sc for sc, _ in scored)
        winners = [w for sc, w in scored if sc == top]
        chosen = winners[0]
        rev = {b: c for c, b in chosen.items()}
        missing = [LEDS[(a, c)] for (a, c) in LEDS if a in chosen and c in chosen and not E[chosen[a], chosen[c]]]
        unexpected = [f"contact {rev.get(p, '?')} -> {rev.get(q, '?')} (P{p}->P{q})" for p in range(8) for q in range(8)
                      if p != q and E[p, q] and (rev.get(p), rev.get(q)) not in LEDS]
        seen = int((E & ~np.eye(8, dtype=bool)).sum())
        leds = {name: bitmap[(chosen[a], chosen[c])] for (a, c), name in LEDS.items()
                if a in chosen and c in chosen and (chosen[a], chosen[c]) in bitmap}
        lay = self._layout_checks(leds)
        # accepted: enough LEDs seen, no contradiction, and the shape rules all pass (digits, order, bars)
        ok = (seen >= min(20, total // 2) and best_contra == 0 and len(winners) == 1
              and (lay["ok"] or lay.get("rules", 0) and lay["rules_passed"] >= 0.8 * lay["rules"]))   # a small display: most rules
        return {"wiring": chosen if ok else None, "score": f"{seen - best_contra}/{seen} seen LEDs explained, {total - len(missing)}/{total} map LEDs seen",
                "contradictions": best_contra, "layout": lay, "candidates": len(cands), "layout_score": top,
                "still_ambiguous": len(winners) > 1,
                "changed": ok and chosen != self._pin_map(), "pairs_lit": int(E.sum()), "missing_leds": missing[:20],
                "unexpected": unexpected[:20]}

    FLIPS = {"as seen": (1, 1), "upside down": (-1, -1), "mirrored": (-1, 1), "flipped": (1, -1)}

    @staticmethod
    def _rules(P):
        """Shape rules of the display for points P (name -> (x, y)), as a list of booleans (only for parts fully located)."""
        out = []
        for d in (1, 2, 3):
            g = {s: P.get(f"d{d}{s}") for s in SEG}
            if all(g.values()):
                x = {k: v[0] for k, v in g.items()}
                y = {k: v[1] for k, v in g.items()}
                # every segment has exactly one place: A top, D bottom, G middle, F/E left, B/C right, upper above lower
                out += [y["A"] < y["B"], y["A"] < y["F"], y["B"] < y["G"] < y["C"], y["F"] < y["G"] < y["E"],
                        y["D"] > y["C"], y["D"] > y["E"], x["F"] < x["A"] < x["B"], x["E"] < x["D"] < x["C"],
                        x["F"] < x["G"] < x["B"], x["E"] < x["G"] < x["C"]]
        cs = [sum(P[f"d{d}{s}"][0] for s in SEG) / 7.0 for d in (1, 2, 3) if all(P.get(f"d{d}{s}") for s in SEG)]
        if len(cs) == 3:
            out += [cs[0] < cs[1], cs[1] < cs[2]]
        bars = [P.get(n) for n in ("bat100", "bat75", "bat50", "bat25")]
        if all(bars):                                   # the four bars stand in a row, in order
            ax = (bars[3][0] - bars[0][0], bars[3][1] - bars[0][1])
            pr = [(b[0] - bars[0][0]) * ax[0] + (b[1] - bars[0][1]) * ax[1] for b in bars]
            out += [pr[0] < pr[1], pr[1] < pr[2], pr[2] < pr[3]]
        return out

    @classmethod
    def _oriented(cls, leds):
        """(rules passed, rules judged, orientation) for the orientation of the display that fits best."""
        pts = {n: (e["x"], e["y"]) for n, e in leds.items() if e.get("seen") and "x" in e}
        best = (-1, 0, "as seen")
        for name, (sx, sy) in cls.FLIPS.items():
            rules = cls._rules({n: (sx * x, sy * y) for n, (x, y) in pts.items()})
            if sum(rules) > best[0]:
                best = (sum(rules), len(rules), name)
        return best

    @classmethod
    def _layout_score(cls, leds):
        return cls._oriented(leds)[0]

    @classmethod
    def _layout_checks(cls, leds):
        """Does the measured picture look like the owner's map? Each digit must be an '8' (A above G above D, B and C on
        the right of F and E), the digits must run left to right and the bars must stand in order. The display may be
        upside down or mirrored in the camera's view; the best-fitting orientation is used."""
        good, judged, orient = cls._oriented(leds)
        return {"rules_passed": max(0, good), "rules": judged, "orientation": orient, "ok": judged > 0 and good == judged}

    def _save_layout(self, res, leds, roi, base, out):
        """Keep the measured LED positions (data/blinkers/leds.json) and a labelled picture; the RoboOS app draws from them."""
        import cv2
        d = DATA / "blinkers"
        lay = {"t": res["t"], "dir": res["dir"], "mode": res["mode"], "roi": roi, "size": [roi[2] - roi[0], roi[3] - roi[1]], "leds": leds}
        (d / "leds.json").write_text(json.dumps(lay, indent=1))
        if base is not None:
            img = cv2.convertScaleAbs(base, alpha=2.0, beta=10)
            img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            col = {"digit": (0, 215, 255), "battery": (80, 220, 80), "head": (255, 120, 255), "text": (255, 200, 80)}
            for name, e in leds.items():
                if not e.get("box"):
                    continue
                x0, y0, x1, y1 = (int(2 * v) for v in e["box"])
                c = col[group_of(name)] if e.get("seen") else tuple(v // 2 for v in col[group_of(name)])   # approximate: dimmer
                cv2.rectangle(img, (x0 - 2, y0 - 2), (x1 + 2, y1 + 2), c, 1)
                cv2.putText(img, name.replace("outline_", "o_").replace("head_", "h_").replace("text_", "t_"), (x0, max(10, y0 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.32, c, 1)
            cv2.imwrite(str(out / "led_map.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
            cv2.imwrite(str(d / "led_map.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
            res["led_map"] = str(out / "led_map.jpg")
        self._layout, self._layout_mt = None, 0

    def layout(self):
        """The measured LED positions from the last good map test (None before the first)."""
        p = DATA / "blinkers" / "leds.json"
        try:
            mt = p.stat().st_mtime
        except OSError:
            return None
        if self._layout is None or mt != self._layout_mt:
            try:
                self._layout, self._layout_mt = json.loads(p.read_text()), mt
            except (OSError, ValueError):
                return None
        return self._layout

    def flicker_test(self, text="777", secs=3.0, pace=None, w=1280, h=1024):
        """How steady does a multiplexed show look to the camera? Shows `text` and films the three digits for `secs`; the
        brightness of every digit should be about equal and steady. Needs a map test first (to know where the digits are)."""
        import cv2
        import numpy as np
        lay = self.layout()
        if not lay:
            raise RuntimeError("run the camera test (mode map) first, so I know where the LEDs are")
        roi, leds = lay["roi"], lay["leds"]
        digits = {}
        for d in (1, 2, 3):
            bx = [leds[f"d{d}{s}"]["box"] for s in "ABC" if leds.get(f"d{d}{s}", {}).get("seen")]
            if bx:
                digits[d] = (min(b[0] for b in bx), min(b[1] for b in bx), max(b[2] for b in bx), max(b[3] for b in bx))
        if not digits:
            raise RuntimeError("no digit LEDs were located")
        old = self.cfg().get("pace", "auto")
        if pace:
            self.cfg()["pace"] = pace
        try:
            info = self.show_number(text, secs + 6)
            time.sleep(1.5)
            series = {d: [] for d in digits}
            with self._camera(w, h) as cam:
                end = time.time() + secs
                while time.time() < end:
                    g = cv2.imdecode(np.frombuffer(cam.frame_after(time.time()), np.uint8), cv2.IMREAD_GRAYSCALE)[roi[1]:roi[3], roi[0]:roi[2]]
                    for d, (x0, y0, x1, y1) in digits.items():
                        series[d].append(float(g[y0:y1 + 1, x0:x1 + 1].mean()))
            rate = self.rate
            self.off()
        finally:
            self.cfg()["pace"] = old
        means = {d: round(float(np.mean(s)), 1) for d, s in series.items()}
        steady = {d: round(float(np.std(s) / max(1.0, np.mean(s))), 3) for d, s in series.items()}
        hi, lo = max(means.values()), min(means.values())
        return {"text": text, "patterns": info.get("bytes"), "writes_per_s": rate, "frames": len(next(iter(series.values()))),
                "digit_brightness": means, "unsteadiness": steady, "even": bool(hi <= 0 or lo / hi > 0.75), "pace": pace or old,
                "screen_page": self.r.cyd.page}

    def display_photo(self, label="display", frames=4, w=1280, h=1024):
        """A photo of just the display (the camera crop around it, never the room), averaged over a few frames so a
        multiplexed show looks the way eyes see it. Saved in data/blinkers/photos/. Needs a map test first."""
        import cv2
        import numpy as np
        lay = self.layout()
        if not lay:
            raise RuntimeError("run the camera test (mode map) first, so I know where the display is")
        x0, y0, x1, y1 = lay["roi"]
        acc = None
        with self._camera(w, h) as cam:
            time.sleep(1.2)                                 # auto exposure
            for _ in range(frames):
                img = cv2.imdecode(np.frombuffer(cam.frame_after(time.time()), np.uint8), cv2.IMREAD_COLOR)[y0:y1, x0:x1].astype(np.float32)
                acc = img if acc is None else acc + img
        img = (acc / frames).clip(0, 255).astype(np.uint8)
        out = DATA / "blinkers" / "photos"
        out.mkdir(parents=True, exist_ok=True)
        path = out / time.strftime(f"%Y%m%d_%H%M%S_{''.join(c if c.isalnum() else '_' for c in label)[:30]}.jpg")
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return {"photo": str(path), "showing": self.showing}

    # ------------------------------------------------------------------ status, API, brain tool
    def status(self):
        e = self.last_exp
        lay = self.layout()
        if self.gpio():
            wired = {str(c): f"header pin {HEADER_PIN.get(o, '?')} (PA{o})" for c, o in sorted(self.gpio_lines().items())}
        else:
            wired = {k: f"P{v}" for k, v in self.cfg()["wired"].items()}
        return {"backend": self.cfg().get("backend"), "wired": wired, "value": None if self.value is None else f"0x{self.value:02X}",
                "lit_now": self.lit(self.value) if self.showing or self.value not in (None, 0xFF) else [],
                "showing": self.showing, "on": sorted(self.on), "reachable": self.reachable(), "writes": self.writes, "fails": self.fails,
                "multiplex_rate": self.rate, "progress": self.progress, "mapped": bool(lay),
                "last_experiment": e and {"dir": e["dir"], "summary": e["summary"], "link": e["link"]}}

    def command(self, d):
        cmd = d.get("cmd", "status")
        secs = d.get("secs", 10)
        if cmd == "status":
            return self.status()
        if cmd == "off":
            return {"msg": self.off()}
        if cmd == "write" and self.gpio():
            return self.show(None, int(str(d["value"]), 0), 0)
        if cmd == "write":
            with self.lock:
                v = self.write(int(str(d["value"]), 0))
                self.on = set(self.lit(v))
                self.showing = f"byte 0x{v:02X}" if self.on else None
                return {"value": f"0x{v:02X}", "lit": sorted(self.on)}
        if cmd == "read":
            v = self.read()
            return {"value": f"{v:08b}"}
        if cmd == "plan":
            return self.plan(d.get("leds") or [], bool(d.get("allow_extra")))
        if cmd == "show":
            return self.show(d.get("leds"), int(str(d["value"]), 0) if d.get("value") is not None else None, secs, bool(d.get("allow_extra")))
        if cmd == "number":
            return self.show_number(d.get("text", ""), secs, bool(d.get("allow_extra", True)))
        if cmd == "battery":
            return self.show_battery(d.get("percent", 0), secs, bool(d.get("allow_extra", True)))
        if cmd == "link_test":
            return self.link_test(int(d.get("n", 20)))
        if cmd == "experiment":
            mode = str(d.get("mode", "map"))
            if d.get("background"):
                return self.start_experiment(mode, w=int(d.get("w", 1280)), h=int(d.get("h", 1024)), reps=int(d.get("reps", 2)))
            return self.experiment(int(d.get("w", 1280)), int(d.get("h", 1024)), int(d.get("reps", 2)), mode=mode)
        if cmd == "text_rot180":
            self.cfg()["text_rot180"] = bool(d.get("on", not self.cfg().get("text_rot180")))
            try:
                self.r.cfg.save()
            except Exception:
                pass
            return {"text_rot180": self.cfg()["text_rot180"]}
        if cmd == "text":
            return self.show_text(d.get("text", ""), secs)
        if cmd == "scroll":
            return self.scroll(d.get("text", ""), d.get("step_ms", 450), d.get("secs", 0))
        if cmd == "animate":
            frames = d.get("frames") or self.named_frames(str(d.get("name", "shimmer")))
            return self.animate(frames, d.get("frame_ms", 80), secs, label=f"animation {d.get('name', 'custom')}")
        if cmd == "flicker_test":
            return self.flicker_test(str(d.get("text", "777")), float(d.get("secs", 3)), d.get("pace"))
        if cmd == "photo":
            return self.display_photo(str(d.get("label", "display")))
        if cmd == "layout":
            return self.layout() or {"error": "no map yet: run the experiment with mode map"}
        raise ValueError(f"unknown cmd {cmd}")

    def tool(self, args):
        """Brain tool: action + arg (LED names comma-separated, a number to show, a battery percent, or a byte like 0xF7)."""
        a, arg, secs = args.get("action", "status"), str(args.get("arg") or "").strip(), args.get("secs") or 10
        try:
            if a == "show":
                res = self.command({"cmd": "show", "leds": [x.strip() for x in arg.split(",") if x.strip()], "secs": secs, "allow_extra": True})
            elif a == "raw":
                res = self.command({"cmd": "show", "value": arg, "secs": secs})
            elif a == "number":
                res = self.command({"cmd": "number", "text": arg, "secs": secs})
            elif a == "battery":
                res = self.command({"cmd": "battery", "percent": float(arg or 0), "secs": secs})
            elif a == "plan":
                res = self.command({"cmd": "plan", "leds": [x.strip() for x in arg.split(",") if x.strip()], "allow_extra": True})
            elif a == "text":
                res = self.command({"cmd": "text", "text": arg, "secs": secs})
            elif a == "scroll":
                res = self.command({"cmd": "scroll", "text": arg, "secs": 0})
            elif a == "animate":
                res = self.command({"cmd": "animate", "name": arg or "shimmer", "secs": secs})
            elif a == "experiment":
                res = self.start_experiment(arg if arg in EXP_MODES else "map")      # takes minutes: runs in the background
                res = {"started": res.get("ok"), "msg": res.get("msg"), "check": "action status shows progress and the summary"}
            else:
                res = self.command({"cmd": a})
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            return f"error: {e}"
        return json.dumps(res)
