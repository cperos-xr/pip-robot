"""Local computer vision with OpenCV (runs on the robot; images never leave it).
Modes: faces (+smiles), motion, qr, all. Used for the live CV demo on the CYD camera page, the web live view,
the 'eyes follow you' face behaviour, and a fast see_live() tool for the AI brain."""
import threading
import time

import cv2
import numpy as np

CASC = "/usr/share/opencv4/haarcascades/"
MODES = ["off", "faces", "gaze", "motion", "qr", "all"]
GREEN, ORANGE, CYAN, YELLOW, WHITE = (60, 220, 60), (0, 150, 255), (255, 220, 0), (0, 230, 255), (255, 255, 255)


class Vision:
    def __init__(self, robot):
        self.r = robot
        self.face = cv2.CascadeClassifier(CASC + "haarcascade_frontalface_default.xml")
        self.smile = cv2.CascadeClassifier(CASC + "haarcascade_smile.xml")
        self.eye = cv2.CascadeClassifier(CASC + "haarcascade_eye.xml")
        self.gaze = [0.0, 0.0]      # smoothed pupil offset, camera frame: +x = toward image right, +y = down
        self.gaze_t = 0
        self.bg = cv2.createBackgroundSubtractorMOG2(history=150, varThreshold=32, detectShadows=False)
        self.qr = cv2.QRCodeDetector()
        self.lock = threading.Lock()
        self.state = {"faces": 0, "smiles": 0, "motion": 0.0, "qr": "", "fps": 0.0, "boxes": [], "ts": 0}
        self.frame_i = 0
        self._fps = [time.time(), 0]
        self.last_qr, self.last_smile_react, self.last_face_seen, self.last_greet = "", 0, 0, 0

    # ---------------------------------------------------------------- config
    def cfg(self):
        c = self.r.cfg.data.setdefault("vision", {})
        c.setdefault("mode", "all")
        c.setdefault("follow", False)    # eyes follow faces on the face page (keeps the camera running)
        c.setdefault("mirror", True)     # camera faces the viewer like the screen does
        c.setdefault("reactions", True)
        c.setdefault("gaze_mirror", False)   # face page: robot eyes copy where your eyes look
        cam = self.r.cfg["camera"]
        if cam.get("live_w", 320) < 640:     # capture sharper and scale down for the screen
            cam["live_w"], cam["live_h"] = 640, 480
            self.r.cfg.save()
        return c

    @property
    def mode(self):
        return self.cfg()["mode"]

    def set_mode(self, m):
        if m in MODES:
            self.cfg()["mode"] = m
            self.r.cfg.save()
            self.r.cyd.send({"t": "vision_mode", "mode": m})
        return self.mode

    def next_mode(self):
        return self.set_mode(MODES[(MODES.index(self.mode) + 1) % len(MODES)] if self.mode in MODES else "all")

    # ---------------------------------------------------------------- processing
    def _pupil(self, eye_gray):
        """Return the pupil offset (-1..1, -1..1) inside an eye box, or None."""
        h, w = eye_gray.shape
        if h < 8 or w < 8:
            return None
        band = eye_gray[int(h * 0.3):int(h * 0.85), int(w * 0.1):int(w * 0.9)]   # skip eyebrow and lower lid
        blur = cv2.GaussianBlur(band, (5, 5), 0)
        thr = np.percentile(blur, 8)
        mask = (blur <= thr).astype(np.uint8)
        m = cv2.moments(mask)
        if m["m00"] < 3:
            return None
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        bh, bw = band.shape
        return (cx / bw) * 2 - 1, (cy / bh) * 2 - 1

    def gaze_from(self, full_gray, face_box, sx, sy, out=None, so=1.0):
        """Estimate where the person is looking from their pupils (full-resolution crop of the face)."""
        x, y, w, h = [int(v) for v in (face_box[0] * sx, face_box[1] * sy, face_box[2] * sx, face_box[3] * sy)]
        top = full_gray[y:y + int(h * 0.55), x:x + w]
        if top.size == 0:
            return None
        eyes = self.eye.detectMultiScale(top, scaleFactor=1.1, minNeighbors=6, minSize=(max(10, w // 8), max(10, w // 8)))
        offs = []
        for (ex, ey, ew, eh) in sorted(eyes, key=lambda e: -e[2] * e[3])[:2]:
            p = self._pupil(top[ey:ey + eh, ex:ex + ew])
            if p:
                offs.append(p)
                if out is not None:
                    k = so / sx
                    cv2.rectangle(out, (int((x + ex) * k), int((y + ey) * k)), (int((x + ex + ew) * k), int((y + ey + eh) * k)), CYAN, 1)
                    px = x + ex + ew * (0.1 + 0.8 * (p[0] + 1) / 2)
                    py = y + ey + eh * (0.3 + 0.55 * (p[1] + 1) / 2)
                    cv2.circle(out, (int(px * k), int(py * k)), 3, (0, 0, 255), -1)
        if not offs:
            return None
        gx = sum(o[0] for o in offs) / len(offs)
        gy = sum(o[1] for o in offs) / len(offs)
        self.gaze = [0.6 * self.gaze[0] + 0.4 * gx, 0.6 * self.gaze[1] + 0.4 * gy]
        self.gaze_t = time.time()
        return self.gaze

    @staticmethod
    def gaze_label(g):
        if not g:
            return "-"
        x, y = g
        horiz = "right" if x > 0.2 else "left" if x < -0.2 else ""      # image directions (camera view)
        vert = "down" if y > 0.25 else "up" if y < -0.25 else ""
        return (vert + " " + horiz).strip() or "center"

    def process(self, jpeg, mode=None, draw=True, out_w=320):
        """Analyse one JPEG frame; returns (annotated BGR image of width out_w, state dict)."""
        mode = mode or self.mode
        # libjpeg can decode at 1/2 scale almost for free; only gaze (pupils) and big web frames need full resolution
        fast = mode != "gaze" and out_w == 320
        full = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_COLOR_2 if fast else cv2.IMREAD_COLOR)
        if full is None:
            return None, self.state
        fh, fw = full.shape[:2]
        img = full if fw == 320 else cv2.resize(full, (320, int(fh * 320 / fw)), interpolation=cv2.INTER_AREA)   # detection size
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h0, w0 = gray.shape
        out = img if out_w == 320 else cv2.resize(full, (out_w, int(fh * out_w / fw)), interpolation=cv2.INTER_AREA)
        so = out_w / 320.0
        full_gray = cv2.cvtColor(full, cv2.COLOR_BGR2GRAY) if mode in ("gaze",) else None
        st = {"faces": 0, "smiles": 0, "motion": 0.0, "qr": self.state.get("qr", ""), "boxes": [], "w": w0, "h": h0, "gaze": None, "gaze_dir": "-"}
        with self.lock:
            self.frame_i += 1
            if mode in ("faces", "all", "gaze"):
                g = cv2.equalizeHist(gray)
                faces = self.face.detectMultiScale(g, scaleFactor=1.2, minNeighbors=5, minSize=(28, 28))
                for (x, y, w, h) in faces:
                    st["boxes"].append([int(x), int(y), int(w), int(h)])
                    roi = g[y + h // 2:y + h, x:x + w]
                    sm = self.smile.detectMultiScale(roi, scaleFactor=1.6, minNeighbors=18, minSize=(max(8, w // 4), max(6, h // 8))) if roi.size else []
                    smiling = len(sm) > 0
                    st["smiles"] += int(smiling)
                    if draw:
                        cv2.rectangle(out, (int(x * so), int(y * so)), (int((x + w) * so), int((y + h) * so)), YELLOW if smiling else GREEN, 2)
                        cv2.putText(out, "smile :)" if smiling else "face", (int(x * so), max(12, int(y * so) - 4)), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.45 * so, YELLOW if smiling else GREEN, 1, cv2.LINE_AA)
                st["faces"] = len(faces)
                if mode == "gaze" and len(faces):
                    big = max(faces, key=lambda b: b[2] * b[3])
                    gz = self.gaze_from(full_gray, big, fw / 320.0, fh / h0, out if draw else None, so)
                    st["gaze"] = [round(v, 2) for v in gz] if gz else None
                    st["gaze_dir"] = self.gaze_label(gz)
            if mode in ("motion", "all"):
                small = cv2.resize(gray, (w0 // 2, h0 // 2))
                fg = cv2.medianBlur(self.bg.apply(small), 5)
                st["motion"] = round(float(fg.mean()) / 255.0, 3)
                if draw:
                    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:4]:
                        if cv2.contourArea(c) > 80:
                            x, y, w, h = cv2.boundingRect(c)
                            cv2.rectangle(out, (int(x * 2 * so), int(y * 2 * so)), (int((x + w) * 2 * so), int((y + h) * 2 * so)), ORANGE, 1)
            if mode in ("qr", "all") and self.frame_i % 3 == 0:
                try:
                    text, pts, _ = self.qr.detectAndDecode(img)   # on the 320-wide copy
                except cv2.error:
                    text, pts = "", None
                if text:
                    st["qr"] = text
                    if draw and pts is not None:
                        cv2.polylines(out, [(pts * so).astype(int).reshape(-1, 1, 2)], True, CYAN, 2)
            if draw:
                n = self._tick()
                hud = f"CV {mode.upper()}  {n:.1f} fps  faces {st['faces']}"
                if mode in ("motion", "all"):
                    hud += f"  motion {st['motion'] * 100:.0f}%"
                if mode == "gaze":
                    hud += f"  looking: {st.get('gaze_dir', '-')}"
                ow, oh = out.shape[1], out.shape[0]
                cv2.rectangle(out, (0, 0), (ow, int(16 * so)), (0, 0, 0), -1)
                cv2.putText(out, hud, (4, int(12 * so)), cv2.FONT_HERSHEY_SIMPLEX, 0.4 * so, WHITE, 1, cv2.LINE_AA)
                if st["qr"] and mode in ("qr", "all"):
                    cv2.rectangle(out, (0, oh - int(18 * so)), (ow, oh), (0, 0, 0), -1)
                    cv2.putText(out, "QR: " + st["qr"][:40], (4, oh - int(5 * so)), cv2.FONT_HERSHEY_SIMPLEX, 0.42 * so, CYAN, 1, cv2.LINE_AA)
        st["fps"] = self.state.get("fps", 0.0)
        st["ts"] = time.time()
        self.state.update(st)
        self._react(st)
        return out, self.state

    def _tick(self):
        t, n = self._fps
        n += 1
        if time.time() - t >= 2:
            self.state["fps"] = n / (time.time() - t)
            self._fps = [time.time(), 0]
        else:
            self._fps = [t, n]
        return self.state.get("fps", 0.0)

    def _react(self, st):
        if not self.cfg()["reactions"]:
            return
        now = time.time()
        if st["qr"] and st["qr"] != self.last_qr:
            self.last_qr = st["qr"]
            self.r.toast("QR: " + st["qr"][:40], 4000)
            self.r.store.event("qr", st["qr"][:300])
        if st["smiles"] and now - self.last_smile_react > 25:
            self.last_smile_react = now
            self.r.face("happy", "I see a smile!", hold=2500)
            self.r.store.event("smile", "saw someone smile")
        if st["faces"]:
            if now - self.last_face_seen > 60 and now - self.last_greet > 120 and self.cfg()["follow"]:
                self.last_greet = now
                self.r.face("happy", "Oh hi! I see you.", hold=2000)
            self.last_face_seen = now

    @staticmethod
    def encode(img, w=320, h=220, quality=70):
        ih, iw = img.shape[:2]
        if (w, h) == (320, 220):
            if iw != 320:
                img = cv2.resize(img, (320, int(ih * 320 / iw)), interpolation=cv2.INTER_AREA)
                ih = img.shape[0]
            top = max(0, (ih - 220) // 2)
            img = img[top:top + 220]
        elif (iw, ih) != (w, h):
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    # ---------------------------------------------------------------- behaviours
    def follow_loop(self):
        """Eyes follow the largest face while the face page is showing and follow mode is on."""
        cam = self.r.cam
        started = False
        last_t = 0
        try:
            while (self.cfg()["follow"] or self.cfg()["gaze_mirror"]) and self.r.cyd.page == "face" and self.r.cyd.up:
                if not started:
                    cam.start_live("follow")
                    started = True
                frame, last_t = cam.next_frame(last_t, timeout=1.0)
                if not frame:
                    continue
                gaze_mode = self.cfg()["gaze_mirror"]
                _, st = self.process(frame, mode="gaze" if gaze_mode else "faces", draw=False)
                if gaze_mode and st.get("gaze"):
                    gx, gy = st["gaze"]
                    # mirror: your eyes move toward image-right when you look to your left, so copy that on screen
                    self.r.cyd.send({"t": "face", "look": [round(max(-1, min(1, gx * 2.2)), 2), round(max(-1, min(1, gy * 1.8)), 2)], "look_ms": 700})
                elif st["boxes"]:
                    x, y, w, h = max(st["boxes"], key=lambda b: b[2] * b[3])
                    cx, cy = (x + w / 2) / st["w"] * 2 - 1, (y + h / 2) / st["h"] * 2 - 1
                    if self.cfg()["mirror"]:
                        cx = -cx
                    self.r.cyd.send({"t": "face", "look": [round(cx, 2), round(cy * 0.8, 2)], "look_ms": 900})
                time.sleep(0.2)
        finally:
            if started:
                cam.stop_live("follow")

    def see_now(self):
        """One-shot local analysis of the live camera (for the AI brain)."""
        cam = self.r.cam
        cam.start_live("vision-see")
        try:
            res = None
            last = 0
            for _ in range(4):          # let motion detection settle over a few frames
                frame, last = cam.next_frame(last, timeout=2.0)
                if frame:
                    _, res = self.process(frame, mode="all", draw=False)
            return {k: (res or {}).get(k) for k in ("faces", "smiles", "motion", "qr")}
        finally:
            cam.stop_live("vision-see")
