"""Screenshots and short screen videos of the robot's own touchscreen (for show-off posts).

- lcd(): the real picture. The screen (firmware 0.7.0+) reads its LCD back and sends it row by row
  ({"t":"shot","y":..,"d":base64 RGB565} ... {"t":"shot_end"}). Takes about 2.5 s; the screen freezes meanwhile.
- When the screen shows RoboOS, the Pi already knows the picture (it draws it), so desktop() needs no screen time.
- encode_mp4(): frames -> small H.264 MP4 (2x pixel-doubled so the tiny screen looks crisp on a phone).

Privacy: only some pages and RoboOS apps may be shown in public (SAFE_PAGES / SAFE_APPS). Status pages, logs,
terminal, files, the camera and the web browser can show IP addresses, file names or people, so they are never posted.
"""
import base64
import io
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from .core import DATA

SHOTS = DATA / "social"
SAFE_PAGES = {"face", "menu", "imu", "desktop"}          # screen pages that never show private details
SAFE_APPS = {None, "games", "mines", "munch", "tictactoe", "checkers", "paint", "about", "music", "camera"}   # RoboOS (None = bare desktop)


class ScreenCap:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.Lock()
        self.rows, self.size, self.done = {}, None, threading.Event()
        self.byteswap = True                 # LovyanGFX readRect(uint16_t*) gives big-endian RGB565 (checked on the real panel)
        robot.bus.on("cyd:shot", self._row)
        robot.bus.on("cyd:shot_end", self._end)

    # ---- the real LCD
    def _row(self, m):
        try:
            self.rows[int(m["y"])] = base64.b64decode(m["d"])
        except Exception:
            pass

    def _end(self, m):
        self.size = (int(m.get("w", 320)), int(m.get("h", 240)))
        self.done.set()

    def supported(self):
        return bool(self.r.cyd.info.get("shot")) and self.r.cyd.up

    def lcd(self, timeout=10):
        """The picture on the real screen right now (PIL RGB 320x240), or None."""
        if not self.supported():
            return None
        with self.lock:
            self.rows, self.size = {}, None
            self.done.clear()
            if not self.r.cyd.send({"t": "shot"}):
                return None
            if not self.done.wait(timeout):
                return None
            w, h = self.size
            raw = bytearray(w * h * 2)
            for y, data in self.rows.items():
                if 0 <= y < h and len(data) == w * 2:
                    raw[y * w * 2:(y + 1) * w * 2] = data
            missing = h - len(self.rows)
        px = np.frombuffer(bytes(raw), dtype=">u2" if self.byteswap else "<u2").reshape(h, w).astype(np.uint32)
        rgb = np.dstack([((px >> 11) & 31) * 255 // 31, ((px >> 5) & 63) * 255 // 63, (px & 31) * 255 // 31]).astype(np.uint8)
        if missing:
            self.r.log(f"screenshot: {missing} rows missing", "warn", persist=False)
        return Image.fromarray(rgb, "RGB")

    # ---- what is safe to show
    def page_is_public(self):
        page = self.r.cyd.page
        if page not in SAFE_PAGES:
            return False
        if page == "desktop":
            w = self.r.desktop.focused()
            return (w.name if w else None) in SAFE_APPS
        return True

    def public_screenshot(self):
        """A screenshot that is OK to post (or None if the screen shows something private)."""
        if self.r.cam.privacy and self.r.cyd.page == "camera":
            return None
        if not self.page_is_public():
            return None
        img = self.lcd()
        if img is None and self.r.cyd.page == "desktop":
            img = self.r.desktop.render()
        return img

    def save(self, img, name):
        SHOTS.mkdir(parents=True, exist_ok=True)
        p = SHOTS / name
        img.save(p)
        return p


def encode_mp4(frames, out, fps=8, scale=2):
    """frames: list of (timestamp, JPEG or PNG bytes, or a PIL image). Resampled to a steady fps. Returns out or None."""
    if not frames or not shutil.which("ffmpeg"):
        return None
    t0, t1 = frames[0][0], frames[-1][0] + 1.5
    tmp = Path(tempfile.mkdtemp(prefix="robo_clip_"))
    try:
        first = frames[0][1]
        size = (first.size if isinstance(first, Image.Image) else Image.open(io.BytesIO(first)).size)
        j, n = 0, 0
        cache = {}
        for k in range(max(1, int((t1 - t0) * fps))):
            t = t0 + k / fps
            while j + 1 < len(frames) and frames[j + 1][0] <= t:
                j += 1
            src = tmp / f"src{j:05d}.png"
            if j not in cache:
                f = frames[j][1]
                (f if isinstance(f, Image.Image) else Image.open(io.BytesIO(f))).convert("RGB").save(src)
                cache[j] = src
            (tmp / f"f{n:05d}.png").hardlink_to(cache[j])
            n += 1
        w, h = size[0] * scale, size[1] * scale
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(tmp / "f%05d.png"),
               "-vf", f"scale={w - w % 2}:{h - h % 2}:flags=neighbor", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True, timeout=240, capture_output=True)
        return out
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
