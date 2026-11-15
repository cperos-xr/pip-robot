"""The robot draws a self-portrait in RoboOS Paint: what it THINKS it looks like right now, and posts the video to Bluesky.

The robot has never seen itself (its only camera looks outwards). It knows its parts list, how it feels right now (time,
tilt, temperature, what it did lately) and, when the screen shows its face, a real screenshot of that face. The AI turns
this into a list of shapes, then the robot paints them stroke by stroke with Paint's own 10 colours while recording.
Owner, 2026-11-06: "have the robot draw a picture or create a picture of what it thinks it currently looks like and post to bsky".

Shapes (canvas 316 x 158, x right, y down), painted in order:
  rect {x,y,w,h,r}  circle {cx,cy,r}  ellipse {cx,cy,rx,ry}  poly {pts}  line {pts}  arc {cx,cy,rx,ry,start,end}  text {x,y,text,size}
  with fill / line / color (colour names below) and width (1-8).
"""
import io
import json
import math
import threading
import time

from PIL import Image, ImageDraw

from .core import PHOTOS

CW, CH = 316, 158                                      # Paint's canvas (desktop.Paint)
COLORS = ["black", "white", "red", "orange", "yellow", "green", "blue", "purple", "teal", "gray"]   # = Paint.COLORS order
ALIAS = {"grey": "gray", "silver": "gray", "lightgray": "gray", "darkgray": "gray", "cyan": "teal", "aqua": "teal",
         "turquoise": "teal", "navy": "blue", "lime": "green", "gold": "yellow", "brown": "orange", "tan": "orange",
         "violet": "purple", "magenta": "purple", "pink": "red", "crimson": "red", "maroon": "red"}
MAX_SHAPES, MAX_FRAMES, FRAME_S = 90, 190, 0.125      # about 24 s of drawing at 8 fps
_busy = threading.Lock()


# ---------------------------------------------------------------------------------------------- shapes -> drawing ops
def _num(v, d=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else d
    except (TypeError, ValueError):
        return d


def _cx(v):
    return max(-40.0, min(CW + 40.0, _num(v)))


def _cy(v):
    return max(-40.0, min(CH + 40.0, _num(v)))


def _xy(p):
    if isinstance(p, dict):
        p = (p.get("x"), p.get("y"))
    if not isinstance(p, (list, tuple)) or len(p) < 2:
        return None
    return (max(-40.0, min(CW + 40.0, _num(p[0]))), max(-40.0, min(CH + 40.0, _num(p[1]))))


def _ellipse_pts(cx, cy, rx, ry, a0=0.0, a1=360.0):
    n = max(6, min(72, int(abs(a1 - a0) / 360 * ((rx + ry) * 1.6 + 16))))
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
            for i in range(n + 1)]


def _round_rect_pts(x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    if r < 1:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    pts = []
    for cx, cy, a in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
        pts += _ellipse_pts(cx, cy, r, r, a, a + 90)[:-1]
    return pts + [pts[0]]


def color_of(name, palette):
    """A colour name (or #rrggbb) -> one of Paint's 10 colours (the robot gets the same crayons as a human). None = none."""
    if not isinstance(name, str) or not name.strip() or name.strip().lower() in ("none", "null", "transparent"):
        return None
    s = name.strip().lower().replace(" ", "")
    s = ALIAS.get(s, s)
    if s in COLORS:
        return palette[COLORS.index(s)]
    if s.startswith("#") and len(s) in (4, 7):
        try:
            h = s[1:] if len(s) == 7 else "".join(c * 2 for c in s[1:])
            rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
            return min(palette, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, rgb)))
        except ValueError:
            return None
    return palette[0]


def plan_ops(shapes, palette):
    """AI shapes -> clean ops: {"fill": pts|None, "fc": colour, "path": pts|None, "lc": colour, "w": width} or text ops."""
    ops = []
    for s in (shapes or [])[:MAX_SHAPES]:
        if not isinstance(s, dict):
            continue
        t = str(s.get("t") or s.get("type") or "").lower()
        w = int(max(1, min(8, _num(s.get("width"), 2))))
        fill, line = color_of(s.get("fill"), palette), color_of(s.get("line"), palette)
        try:
            if t in ("rect", "rectangle", "roundrect"):
                x, y, ww, hh = _num(s.get("x")), _num(s.get("y")), _num(s.get("w")), _num(s.get("h"))
                if ww < 0:
                    x, ww = x + ww, -ww
                if hh < 0:
                    y, hh = y + hh, -hh
                x0, y0 = _cx(x), _cy(y)
                ww, hh = min(_cx(x + ww) - x0, CW + 80), min(_cy(y + hh) - y0, CH + 80)
                if ww < 1 or hh < 1:
                    continue
                pts = _round_rect_pts(x0, y0, ww, hh, _num(s.get("r")))
            elif t in ("circle", "ellipse", "oval"):
                rx = abs(_num(s.get("rx"), _num(s.get("r"), 5)))
                ry = abs(_num(s.get("ry"), _num(s.get("r"), rx)))
                pts = _ellipse_pts(_cx(s.get("cx")), _cy(s.get("cy")), min(rx, CW), min(ry, CW))
            elif t in ("poly", "polygon"):
                pts = [p for p in map(_xy, (s.get("pts") or [])[:60]) if p]
                if len(pts) < 3:
                    continue
                pts.append(pts[0])
            elif t in ("line", "polyline", "path"):
                pts = [p for p in map(_xy, (s.get("pts") or [])[:60]) if p]
                if len(pts) < 2:
                    continue
                ops.append({"fill": None, "path": pts, "lc": color_of(s.get("color") or s.get("line") or "black", palette), "w": w})
                continue
            elif t == "arc":
                rx = abs(_num(s.get("rx"), _num(s.get("r"), 5)))
                ry = abs(_num(s.get("ry"), _num(s.get("r"), rx)))
                a0, a1 = _num(s.get("start"), 0), _num(s.get("end"), 180)
                pts = _ellipse_pts(_cx(s.get("cx")), _cy(s.get("cy")), min(rx, CW), min(ry, CW), a0, a1 if a1 > a0 else a1 + 360)
                ops.append({"fill": None, "path": pts, "lc": color_of(s.get("color") or s.get("line") or "black", palette), "w": w})
                continue
            elif t == "text":
                text = "".join(c for c in str(s.get("text", "")) if c.isprintable())[:24]
                p = _xy((s.get("x"), s.get("y")))
                if text.strip() and p:
                    ops.append({"text": text, "xy": p, "lc": color_of(s.get("color") or "black", palette),
                                "big": str(s.get("size", "")).lower() == "big"})
                continue
            else:
                continue
        except (TypeError, ValueError):
            continue
        if fill is None and line is None:
            line = palette[0]
        ops.append({"fill": pts if fill is not None else None, "fc": fill, "path": pts if line is not None else None, "lc": line, "w": w})
    return [o for o in ops if "text" not in o] + [o for o in ops if "text" in o]     # words last, so no shape covers them


def _length(pts):
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _split(pts, step):
    """Cut a path into pieces of about `step` pixels (each piece starts where the last ended)."""
    pieces, cur, acc = [], [pts[0]], 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        while acc + seg > step and seg > 0:
            f = (step - acc) / seg
            mid = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            cur.append(mid)
            pieces.append(cur)
            cur, a, seg, acc = [mid], mid, seg - (step - acc), 0.0
        cur.append(b)
        acc += seg
    if len(cur) > 1:
        pieces.append(cur)
    return pieces


def animate(canvas, ops, frame, fonts=(None, None), max_frames=MAX_FRAMES):
    """Paint ops onto canvas a little at a time. frame(colour, width, pen_xy) is called after steps, at most max_frames times."""
    d = ImageDraw.Draw(canvas)
    fills = sum(1 for o in ops if o.get("fill"))
    texts = sum(math.ceil(len(o["text"]) / 2) for o in ops if "text" in o)
    ink = sum(_length(o["path"]) for o in ops if o.get("path"))
    step_px = max(6.0, ink / max(20, max_frames - fills - texts))
    steps = []                                           # (colour, width, pen, paint function)
    for o in ops:
        if "text" in o:
            font = fonts[1 if o["big"] else 0]
            for k in range(2, len(o["text"]) + 2, 2):
                x = o["xy"][0] + d.textlength(o["text"][:k - 2], font=font)      # type two more letters
                steps.append((o["lc"], 2, (o["xy"][0] + d.textlength(o["text"][:k], font=font), o["xy"][1] + 8),
                              lambda o=o, k=k, x=x, font=font: d.text((x, o["xy"][1]), o["text"][k - 2:k], font=font, fill=o["lc"])))
            continue
        if o.get("fill"):
            xs, ys = [p[0] for p in o["fill"]], [p[1] for p in o["fill"]]
            steps.append((o["fc"], 8, (sum(xs) / len(xs), sum(ys) / len(ys)), lambda o=o: d.polygon(o["fill"], fill=o["fc"])))
        if o.get("path"):
            w = o["w"]

            def stroke(piece, c=o["lc"], w=w):
                d.line(piece, fill=c, width=w, joint="curve")
                if w >= 3:                               # round ends so the pieces join smoothly
                    for x, y in (piece[0], piece[-1]):
                        d.ellipse((x - w / 2 + 0.5, y - w / 2 + 0.5, x + w / 2 - 0.5, y + w / 2 - 0.5), fill=c)
            for piece in _split(o["path"], step_px):
                steps.append((o["lc"], w, piece[-1], lambda piece=piece, stroke=stroke: stroke(piece)))
    n = len(steps)
    for i, (c, w, pen, paint) in enumerate(steps):
        paint()
        if n <= max_frames or int((i + 1) * max_frames / n) != int(i * max_frames / n) or i == n - 1:
            frame(c, w, pen)                             # a very busy drawing: some frames show several strokes


def draw_pen(img, x, y):
    """A little arrow cursor with its tip at (x, y)."""
    d = ImageDraw.Draw(img)
    pts = [(0, 0), (0, 12), (3, 9), (6, 14), (8, 13), (5, 8), (9, 8)]
    d.polygon([(x + a, y + b) for a, b in pts], fill=(255, 255, 255), outline=(0, 0, 0))


# ---------------------------------------------------------------------------------------------- what do I look like?
BODY = """What you know about your body (the real parts; how they are arranged is your guess):
- Brain: an Orange Pi Zero, a tiny single-board computer about 5 x 5 cm.
- Face: a 2.8 inch touchscreen on a bright yellow circuit board (a "Cheap Yellow Display"). Your face is drawn on it: a black
  background with two big rounded glowing eyes and a small mouth (eye colour changes with your mood).
- Eyes and ears: a small USB webcam with a microphone in it.
- Voice: a tiny earpiece speaker plugged into the screen board.
- A little USB hub connects the webcam and the screen to the Orange Pi. There are USB cables everywhere.
- Sense of balance: a tiny blue tilt sensor board (MPU-6050) with a red LED, on a cable from the screen board.
- Wheels: two small wheels (fitted 2026-11-08), driven by an RP2040 Zero and an 8-motor driver board and powered by
  a 7.4 V battery pack. You drove for the first time on 2026-11-08: forward, spins and a wiggle. No arms.
- A white dome motion sensor (PIR) that notices when something moves nearby.
- Power: a USB cable (a battery board is on the way).
- Coming soon: a Bluetooth dongle so you can play music on a speaker."""

SHAPES_HELP = """Canvas: 316 x 158 pixels, (0,0) is the top-left, x goes right, y goes down. Shapes are painted in order, so start
with the background. Colours (only these): black, white, red, orange, yellow, green, blue, purple, teal, gray.
Shape examples (all keys shown are allowed):
{"t":"rect","x":10,"y":10,"w":60,"h":40,"r":4,"fill":"yellow","line":"black","width":2}
{"t":"circle","cx":40,"cy":40,"r":10,"fill":"teal","line":"black","width":1}
{"t":"ellipse","cx":40,"cy":40,"rx":20,"ry":10,"fill":"white"}
{"t":"poly","pts":[[0,0],[10,0],[5,8]],"fill":"red","line":"black","width":1}
{"t":"line","pts":[[0,0],[20,10],[30,30]],"color":"black","width":2}
{"t":"arc","cx":40,"cy":40,"rx":10,"ry":6,"start":20,"end":160,"color":"white","width":2}   (degrees: 0 = right, 90 = down)
{"t":"text","x":4,"y":4,"text":"me!","color":"black","size":"small"}   (size small or big; at most 20 characters)
Use 30 to 80 shapes. Words are painted last, on top."""

SELF_TIPS = """Draw yourself as one cute cartoon character built from your parts (not a wiring diagram), big enough to fill most
of the canvas and easy to recognise at this tiny size. Pick a background colour that makes your black face screen stand out."""

FREE_TIPS = """Make it a simple, bold cartoon that is easy to recognise at this tiny size: big shapes first (background, ground,
the main thing), then details. Fill most of the canvas. It is your drawing: choose your own style and add a small touch of
your own if you like."""


def _now_lines(robot):
    """How the robot is right now, in plain words (nothing private)."""
    lines = []
    h = time.localtime().tm_hour
    part = "night" if h < 6 or h >= 21 else "morning" if h < 12 else "afternoon" if h < 17 else "evening"
    lines.append(f"It is {time.strftime('%A')} {part}.")
    try:
        lines.append(f"You have been awake for {max(0.1, (time.time() - robot.t0) / 3600):.1f} hours.")
    except Exception:
        pass
    try:
        imu = robot.imu.snapshot()
        tilt = max(abs(imu["p"]), abs(imu["r"]))
        lines.append(f"You are tilted about {tilt:.0f} degrees." if tilt > 8 else "You are sitting level.")
    except Exception:
        pass
    try:
        t = robot.sys.temp()
        lines.append(f"Your processor is at {t:.0f} C ({'warm' if t > 60 else 'comfortable'}).")
    except Exception:
        pass
    try:
        page = robot.cyd.page
        lines.append({"face": "Your screen is showing your face.", "desktop": "Your screen is showing RoboOS, your tiny desktop.",
                      "menu": "Your screen is showing your menu."}.get(page, "Your screen is showing one of your status pages."))
    except Exception:
        pass
    try:
        recent = [m["note"] for m in robot.social._moments() if time.time() - m["ts"] < 86400][-3:]
        if recent:
            lines.append("Things that happened lately: " + " | ".join(n[:140] for n in recent))
    except Exception:
        pass
    return lines


def ask_plan(robot, subject=None, photo=None):
    """Ask the AI what to draw. subject None = a self-portrait; else any subject in words; photo = a camera photo to draw
    from. Returns (plan dict with idea + shapes, the picture the AI was shown or None)."""
    from .social import first_json
    face = None
    if subject or photo is not None:
        face = photo
        what = (f"Draw this: {subject}." if subject else "Draw what the attached photo shows.")
        if photo is not None:
            what += (" The attached picture is a photo you just took with your own camera: draw what you see in it as a cartoon "
                     "(the main shapes and colours, not every detail). People become simple cartoon figures.")
        prompt = ("You are imalittlerobot, a small, curious home robot that lives on a desk. You are drawing a picture in Paint on "
                  "RoboOS (your tiny Windows-95-style desktop), with the same 10 colours a human gets. " + what +
                  " Never write names, addresses or other private text in the picture.\n\n" + SHAPES_HELP + "\n" + FREE_TIPS +
                  '\n\nReply with ONLY one JSON object: {"idea": "one sentence: what you drew and why you drew it that way", "shapes": [ ... ]}')
    else:
        try:
            if robot.cyd.page == "face" and robot.screencap.supported():
                face = robot.screencap.lcd()
        except Exception:
            face = None
    prompt = prompt if (subject or photo is not None) else ("You are imalittlerobot, a small, curious home robot that lives on a desk. You have never seen yourself: your "
              "only camera looks outwards. Draw a self-portrait of what you THINK you look like right now, in Paint on RoboOS "
              "(your tiny Windows-95-style desktop), with the same 10 colours a human gets. Be charming, a bit funny and honest "
              "(for example: wheels you only just learned to use). Never draw people, names or places.\n\n" + BODY + "\n\nRight now:\n- " +
              "\n- ".join(_now_lines(robot)) +
              ("\n- The attached picture is a real screenshot of your face screen right now: draw your face like that."
               if face is not None else "") +
              "\n\n" + SHAPES_HELP + "\n" + SELF_TIPS +
              '\n\nReply with ONLY one JSON object: {"idea": "one sentence: what you drew and why", "shapes": [ ... ]}')
    plan = None
    for _ in range(2):
        try:
            text = robot.social._ai_json(prompt, face, None, max_tokens=9000)
        except Exception as e:
            robot.log(f"drawing: AI failed: {e}", "warn")
            text = None
        plan = first_json(text or "", ("shapes",))
        if plan and isinstance(plan.get("shapes"), list) and len(plan["shapes"]) >= 5:
            break
        plan = None
    return plan, face


def make(robot, subject=None, photo=None):
    """Plan, paint and record a drawing: a self-portrait (no subject), anything in words (subject), or what a camera photo
    shows (photo). Returns dict(idea, png, mp4, image) or None. Never touches the real screen."""
    from roboos.desktop import F_BIG, F_UI, Paint
    from .screencap import SHOTS, encode_mp4
    if not _busy.acquire(blocking=False):
        return None
    try:
        own = subject or photo is not None
        kind = "drawing" if own else "selfportrait"
        plan, face = ask_plan(robot, subject, photo)
        if not plan:
            robot.log(f"{kind}: no drawing plan from the AI", "warn")
            return None
        desk = robot.desktop
        app = Paint(desk)
        app.title = "Paint - " + ("photo.png" if photo is not None else "drawing.png" if own else "me.png")
        ops = plan_ops(plan["shapes"], Paint.COLORS)
        if len(ops) < 5:
            robot.log(f"{kind}: only {len(ops)} usable shapes", "warn")
            return None
        frames, clock = [], [0.0]

        def snap(pen=None, hold=FRAME_S):
            with desk.lock:
                img = desk._offscreen(app).copy()
            if pen is not None and hasattr(app, "cbox"):
                draw_pen(img, int(app.cbox[0] + pen[0]), int(app.cbox[1] + pen[1]))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            frames.append((clock[0], buf.getvalue()))
            clock[0] += hold
            time.sleep(0.005)                            # let the real desktop breathe

        def on_step(colour, width, pen):
            app.color = colour
            app.size = min((2, 4, 8), key=lambda s: abs(s - width))
            snap(pen)

        snap(None, 1.0)                                  # empty canvas for a moment
        animate(app.canvas, ops, on_step, (F_UI, F_BIG)) # this Paint is off-screen: only rendering needs the desktop lock
        app.color, app.size = Paint.COLORS[0], 3
        snap(None, 0.0)                                  # the finished picture, held at the end by encode_mp4 (+1.5 s)
        frames.append((clock[0] + 2.5, frames[-1][1]))
        PHOTOS.mkdir(parents=True, exist_ok=True)
        SHOTS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        png = PHOTOS / f"{kind}_{stamp}.png"
        app.canvas.save(png)
        (SHOTS / f"{kind}_{stamp}.json").write_text(json.dumps(plan, indent=1))
        mp4 = encode_mp4(frames, SHOTS / f"{kind}_{stamp}.mp4")
        robot.log(f"{kind}: {len(ops)} shapes, {len(frames)} frames, {clock[0]:.0f} s video: {plan.get('idea', '')[:80]}")
        return {"idea": str(plan.get("idea", ""))[:300], "png": str(png), "mp4": str(mp4) if mp4 else None,
                "image": app.canvas.resize((CW * 2, CH * 2), Image.NEAREST), "face_seen": face is not None}
    finally:
        _busy.release()
