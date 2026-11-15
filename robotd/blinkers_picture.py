"""A picture of the BLINKERS display that looks like the real one (battery, octagon face, three slanted digits,
BLINKERS lettering), drawn with PIL for the RoboOS app. Owner's reference drawing 2026-11-12.

Coordinates are in a 1000 x 300 box. Battery bars, outline pieces and digit segments have one LED each (the owner's map
names them). The head (8 dashes, 2 eyes, a smile) and the 8 letters are lit by fewer LEDs, and the map does not say which
part each one lights, so `assign()` works it out from the camera test: it fits the measured battery and digit LEDs onto
this picture (an affine map from the camera's view) and gives every head/letter part to the LED whose glow covers it.
"""
import math

W, H = 1000, 300
BG = (12, 12, 14)
OFF = (55, 55, 62)            # a segment that is wired but off (like an unlit segment on the real display)
NOWIRE = (30, 30, 34)
LIT = (255, 244, 170)          # lit now: warm glow
PICK = (0, 200, 255)           # picked in the app, not shown yet
GROUP_LIT = {"battery": (120, 255, 140), "head": (255, 170, 255), "digit": (255, 244, 170), "text": (140, 210, 255)}


def _slant(x, y, y_base=190, k=0.17):
    return (x + (y_base - y) * k, y)


def build():
    """Every drawable part: id -> {"kind": line|rect|arc|text, ..., "c": centre, "group": ...}."""
    E = {}

    def line(eid, pts, wd, group, slant=False):
        p = [_slant(*q) for q in pts] if slant else pts
        E[eid] = {"kind": "line", "pts": p, "w": wd, "group": group,
                  "c": (sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p))}

    def rect(eid, box, group):
        E[eid] = {"kind": "rect", "box": box, "group": group, "c": ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)}
    # battery: outline in 6 pieces (+ the nub with the top) and 4 bars
    line("outline_top", [(62, 55), (168, 55)], 9, "battery")
    rect("outline_nub", (93, 36, 137, 52), "battery")
    line("outline_left_up", [(47, 68), (47, 160)], 9, "battery")
    line("outline_left_low", [(47, 170), (47, 262)], 9, "battery")
    line("outline_right", [(183, 68), (183, 160)], 9, "battery")
    line("outline_right_low", [(183, 170), (183, 262)], 9, "battery")
    line("outline_bottom", [(62, 275), (168, 275)], 9, "battery")
    for i, n in enumerate(("bat100", "bat75", "bat50", "bat25")):
        rect(n, (70, 72 + i * 48, 160, 110 + i * 48), "battery")
    # head: an octagon of 8 dashes, two eyes, a smile
    cx, cy, r = 353, 160, 104
    for k in range(8):
        a0, a1 = math.radians(22.5 + 45 * k + 6), math.radians(22.5 + 45 * (k + 1) - 6)
        ang = (45 * (k + 1)) % 360                  # direction of the dash's middle: 0 right, 90 down, 180 left, 270 up
        line(f"h_dash{ang}", [(cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1))], 10, "head")
    rect("h_eye_left", (304, 118, 320, 150), "head")
    rect("h_eye_right", (386, 118, 402, 150), "head")
    E["h_smile"] = {"kind": "arc", "box": (308, 128, 398, 212), "a": (35, 145), "w": 9, "group": "head", "c": (353, 205)}
    # three slanted 7-segment digits
    for d, x0 in ((1, 548), (2, 702), (3, 856)):
        y0, y1, w = 42, 182, 82
        ym = (y0 + y1) / 2
        segs = {"A": [(x0 + 10, y0), (x0 + w - 10, y0)], "B": [(x0 + w, y0 + 10), (x0 + w, ym - 7)], "C": [(x0 + w, ym + 7), (x0 + w, y1 - 10)],
                "D": [(x0 + 10, y1), (x0 + w - 10, y1)], "E": [(x0, ym + 7), (x0, y1 - 10)], "F": [(x0, y0 + 10), (x0, ym - 7)],
                "G": [(x0 + 10, ym), (x0 + w - 10, ym)]}
        for s, pts in segs.items():
            line(f"d{d}{s}", pts, 12, "digit", slant=True)
    # the BLINKERS letters
    for i, ch in enumerate("BLINKERS"):
        x = 528 + i * 55
        E[f"t_{i}{ch}"] = {"kind": "text", "xy": (x, 228), "ch": ch, "group": "text", "c": (x + 18, 252)}
    return E


ELEMENTS = build()
HEAD_PARTS = [e for e in ELEMENTS if e.startswith("h_")]
TEXT_PARTS = [e for e in ELEMENTS if e.startswith("t_")]
# before the first camera test: a sensible guess for which LED lights which head/letter part
DEFAULT = {"head_a": ["h_dash270"], "head_b": ["h_dash315"], "head_c": ["h_dash225"], "head_d": ["h_dash0"],
           "head_center": ["h_eye_left", "h_eye_right"], "head_right": ["h_dash45"], "head_low_left": ["h_dash135"],
           "head_low_left2": ["h_dash180"], "head_right_low": ["h_dash90", "h_smile"],
           "text_a": ["t_0B", "t_1L"], "text_b": ["t_2I", "t_3N"], "text_c": ["t_4K", "t_5E"], "text_right": ["t_6R", "t_7S"],
           "outline_top": ["outline_top", "outline_nub"]}


def assign(layout):
    """LED name -> picture parts. Battery and digits are fixed; head and letters come from the camera test when there is one.
    Returns (mapping, info)."""
    m = {e: [e] for e in ELEMENTS if not e.startswith(("h_", "t_", "outline_nub"))}
    m.update(DEFAULT)
    info = {"source": "guess"}
    if not layout:
        return m, info
    leds = {n: e for n, e in layout.get("leds", {}).items() if e.get("x") is not None}
    anchors = [n for n in leds if (n.startswith("d") or n.startswith("bat")) and n in ELEMENTS and leds[n].get("seen", True)]
    if len(anchors) < 6:
        return m, info
    try:
        import numpy as np
        X = np.array([[leds[n]["x"], leds[n]["y"], 1.0] for n in anchors])
        Y = np.array([ELEMENTS[n]["c"] for n in anchors])
        A, *_ = np.linalg.lstsq(X, Y, rcond=None)
        err = float(np.sqrt(((X @ A - Y) ** 2).sum(axis=1)).mean())
    except Exception as e:
        return m, {"source": "guess", "error": str(e)}
    if err > 45:                                  # the camera picture does not fit the drawing: keep the guess
        return m, {"source": "guess", "fit_error": round(err, 1)}
    tf = lambda x, y: (x * A[0][0] + y * A[1][0] + A[2][0], x * A[0][1] + y * A[1][1] + A[2][1])
    for group, parts, prefix in (("head", HEAD_PARTS, "head_"), ("text", TEXT_PARTS, "text_")):
        names = [n for n in leds if n.startswith(prefix)]
        if not names:
            continue
        boxes = {}
        for n in names:
            e = leds[n]
            bx = e.get("box") or [e["x"] - 2, e["y"] - 2, e["x"] + 2, e["y"] + 2]
            pts = [tf(bx[0], bx[1]), tf(bx[2], bx[1]), tf(bx[0], bx[3]), tf(bx[2], bx[3])]
            boxes[n] = (min(p[0] for p in pts) - 12, min(p[1] for p in pts) - 12, max(p[0] for p in pts) + 12, max(p[1] for p in pts) + 12,
                        tf(e["x"], e["y"]))
        got = {n: [] for n in names}
        for part in parts:
            pc = ELEMENTS[part]["c"]
            inside = [n for n, b in boxes.items() if b[0] <= pc[0] <= b[2] and b[1] <= pc[1] <= b[3]]
            pool = inside or names
            best = min(pool, key=lambda n: (boxes[n][4][0] - pc[0]) ** 2 + (boxes[n][4][1] - pc[1]) ** 2)
            got[best].append(part)
        for n, ps in got.items():
            m[n] = ps or [min(parts, key=lambda p: (ELEMENTS[p]["c"][0] - boxes[n][4][0]) ** 2 + (ELEMENTS[p]["c"][1] - boxes[n][4][1]) ** 2)]
    return m, {"source": "camera", "fit_error": round(err, 1)}


def draw(d, box, leds_of, on=(), picked=(), reach=(), font=None):
    """Draw the display into box (x0, y0, x1, y1). leds_of: LED name -> parts (from assign()). Returns
    {LED name: [(x, y) screen centres]} for tapping."""
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=BG)
    s = min((x1 - x0 - 6) / W, (y1 - y0 - 4) / H)
    ox, oy = x0 + ((x1 - x0) - W * s) / 2, y0 + ((y1 - y0) - H * s) / 2
    P = lambda p: (ox + p[0] * s, oy + p[1] * s)
    owner = {}
    for led, parts in leds_of.items():
        for p in parts:
            owner.setdefault(p, []).append(led)
    on, picked, reach = set(on), set(picked), set(reach)
    centres = {}
    for eid, e in ELEMENTS.items():
        leds = owner.get(eid, [])
        if any(n in on for n in leds):
            col = GROUP_LIT.get(e["group"], LIT)
        elif any(n in picked for n in leds):
            col = PICK
        elif any(n in reach for n in leds):
            col = OFF
        else:
            col = NOWIRE
        if e["kind"] == "line":
            d.line([P(q) for q in e["pts"]], fill=col, width=max(1, round(e["w"] * s)))
        elif e["kind"] == "rect":
            b = e["box"]
            d.rectangle((*P((b[0], b[1])), *P((b[2], b[3]))), fill=col)
        elif e["kind"] == "arc":
            b = e["box"]
            d.arc((*P((b[0], b[1])), *P((b[2], b[3]))), e["a"][0], e["a"][1], fill=col, width=max(1, round(e["w"] * s)))
        elif e["kind"] == "text" and font is not None:
            d.text(P(e["xy"]), e["ch"], font=font, fill=col)
        for n in leds:
            centres.setdefault(n, []).append(P(e["c"]))
    return centres
