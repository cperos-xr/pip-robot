"""Webcam: full-resolution stills and a low-res live MJPEG stream, both via v4l2-ctl (no extra deps)."""
import io
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

DEV = "/dev/video0"
SOI = b"\xff\xd8\xff"
EOI = b"\xff\xd9"


def split_frames(buf):
    """Return (complete_frames, remainder) from a concatenated MJPEG byte stream."""
    frames = []
    while True:
        s = buf.find(SOI)
        if s < 0:
            return frames, b""
        n = buf.find(SOI, s + 3)
        if n < 0:
            return frames, buf[s:]
        f = buf[s:n]
        e = f.rfind(EOI)
        if e > 0:
            frames.append(f[:e + 2])
        buf = buf[n:]


def fit_jpeg(img, w, h, max_bytes, quality=80, pad=True):
    """Letterbox `img` into w x h and JPEG-encode under max_bytes."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((w, h), Image.BILINEAR)
    if pad and img.size != (w, h):
        bg = Image.new("RGB", (w, h), (0, 0, 0))
        bg.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
        img = bg
    q = quality
    while True:
        out = io.BytesIO()
        img.save(out, "JPEG", quality=q, optimize=False)
        data = out.getvalue()
        if len(data) <= max_bytes or q <= 25:
            return data
        q -= 10


class Camera:
    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log
        self.lock = threading.Lock()   # only one user of the device at a time
        self.live_proc = None
        self.latest = None             # latest live frame (raw MJPEG bytes)
        self.latest_t = 0
        self.frame_cv = threading.Condition()
        self.live_owners = set()       # who is using the live stream ("web", "screen", "follow", ...)

    @property
    def present(self):
        return Path(DEV).exists()

    # ---------- live stream ----------
    def _live_reader(self, proc):
        buf = b""
        while proc.poll() is None:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            frames, buf = split_frames(buf)
            if frames:
                with self.frame_cv:
                    self.latest, self.latest_t = frames[-1], time.time()
                    self.frame_cv.notify_all()

    @property
    def privacy(self):
        """Privacy mode: the owner switched the camera off. Nothing may stream or take photos."""
        return bool(self.cfg.data.setdefault("camera", {}).get("privacy", False))

    def set_privacy(self, on):
        self.cfg.data.setdefault("camera", {})["privacy"] = bool(on)
        self.cfg.save()
        with self.lock:
            if on:
                self._kill_live()              # owners stay registered; they resume when privacy is turned off
            elif self.live_owners:
                self._spawn_live()

    def start_live(self, owner="anon"):
        """Start (or join) the live stream. Each user names itself, so a double start can never leak."""
        with self.lock:
            self.live_owners.add(owner)
            if not self.privacy:
                self._spawn_live()

    def _spawn_live(self):
        if self.live_proc and self.live_proc.poll() is None:
            return
        c = self.cfg["camera"]
        cmd = ["v4l2-ctl", "-d", DEV, f"--set-fmt-video=width={c['live_w']},height={c['live_h']},pixelformat=MJPG",
               f"--set-parm={c['live_fps']}", "--stream-mmap=3", "--stream-to=-"]
        self.live_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._live_reader, args=(self.live_proc,), daemon=True).start()

    def _kill_live(self):
        if self.live_proc:
            self.live_proc.terminate()
            try:
                self.live_proc.wait(2)
            except subprocess.TimeoutExpired:
                self.live_proc.kill()
            self.live_proc = None

    def stop_live(self, owner="anon", force=False):
        """Leave the live stream; the camera (and its light) turns off when nobody is left."""
        with self.lock:
            if force:
                self.live_owners.clear()
            else:
                self.live_owners.discard(owner)
            if not self.live_owners:
                self._kill_live()

    @property
    def live_wanted(self):
        return len(self.live_owners)

    def next_frame(self, after, timeout=1.0):
        with self.frame_cv:
            end = time.time() + timeout
            while self.latest_t <= after and time.time() < end:
                self.frame_cv.wait(end - time.time())
            return (self.latest, self.latest_t) if self.latest_t > after else (None, after)

    # ---------- stills ----------
    def still(self, w=None, h=None, skip=6):
        """Capture one frame at (w,h); returns a PIL image. Pauses the live stream if it is running."""
        c = self.cfg["camera"]
        w, h = w or c["still_w"], h or c["still_h"]
        if self.privacy:
            raise RuntimeError("the camera is switched off (privacy mode) - the owner can turn it back on")
        with self.lock:
            was_live = bool(self.live_owners)
            self._kill_live()                  # pause the stream; the owners stay registered
        try:
            with self.lock:
                cmd = ["v4l2-ctl", "-d", DEV, f"--set-fmt-video=width={w},height={h},pixelformat=MJPG",
                       "--stream-mmap=3", f"--stream-count={skip}", "--stream-to=-"]
                out = subprocess.run(cmd, capture_output=True, timeout=20).stdout
            frames, _ = split_frames(out + SOI)
            if not frames:
                raise RuntimeError("camera returned no frames")
            img = Image.open(io.BytesIO(frames[-1]))
            img.load()
            return img
        finally:
            if was_live:
                with self.lock:
                    if self.live_owners and not self.privacy:
                        self._spawn_live()

    def save_photo(self, folder, prefix="img"):
        img = self.still()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / time.strftime(f"{prefix}_%Y%m%d_%H%M%S.jpg")
        img.save(path, "JPEG", quality=90)
        return path, img
