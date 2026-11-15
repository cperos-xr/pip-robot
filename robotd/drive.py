"""Onboard steering and remote control (owner, 2026-11-08: "build the onboard steering, as well as remote control steering").

The control loops run here, on the robot, next to the IMU (yaw at 50 Hz) and the motor link. Steered from a laptop over
Wi-Fi, 90 degree turns came out as 100-182 degrees; here every yaw sample is seen, so turns stop where they should.

POST /api/drive with one move or {"seq": [moves...]} (runs in the background; GET /api/drive shows progress):
  {"cmd": "turn", "deg": 90}                     spin in place, + = left (IMU closed loop)
  {"cmd": "forward" | "back", "secs": 1, "speed": 100}   straight, holding the heading with the IMU
  {"cmd": "arc", "left": 45, "right": 120, "secs": 1}     raw wheel powers
  {"cmd": "wiggle", "n": 6, "speed": 110, "secs": 0.2}
  {"cmd": "wait", "secs": 1}   {"cmd": "say", "text": "..."}   {"cmd": "ask", "q": "...", "opts": ["Start", "Cancel"]}
  {"cmd": "stop"}              (also cancels a running sequence)
POST /api/drive/rc {"x": -1..1 (+ = right), "y": -1..1 (+ = forward), "max": 0-127}: send every 100-200 ms while
driving; the wheels stop 0.4 s after the last one (and the motor firmware 0.5 s after that). Driving straight holds
the heading with the IMU.
Everything needs motors_enabled (owner only) and is capped by motor_max.
"""
import threading
import time

TURN_MAX = 95      # spin power cap: faster spins pass the MPU-6050's 250 deg/s range and the yaw undercounts
TURN_MIN = 62      # below this a spin often does not start (the front skids drag)
TOL = 4.0          # degrees: close enough
COAST = 0.10       # seconds of turning that still happen after the power goes off (tuned on 2026-11-08)
KH = 2.5           # heading hold: power per degree of heading error


def wrap(d):
    return (d + 180) % 360 - 180


class Drive:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.Lock()                     # one move or sequence at a time
        self.cancel = threading.Event()
        self.state = {"busy": False, "step": "", "log": [], "error": ""}
        self.rc_until, self.rc_on, self.rc_hold = 0.0, False, None
        self._last_out = (None, 0.0)
        threading.Thread(target=self._rc_watch, daemon=True, name="drive-rc").start()

    # ------------------------------------------------------------------ wheels
    def wheels(self):
        w = self.r.cfg.data.get("wheels") or {}
        if not (w.get("left") and w.get("right")):
            raise RuntimeError("run the wheels test first (Tests > Wheel setup)")
        return w

    def out(self, left, right, keep=0.25, force=False):
        """Wheel powers -127..127 (+ = forward). robotd keeps them on for `keep` s unless something newer arrives."""
        w = self.wheels()
        cap = min(127, int(self.r.cfg["motor_max"]))
        m = max(abs(left), abs(right))
        if m > cap:                                      # scale both down together: clipping each wheel on its own
            left, right = left * cap / m, right * cap / m   # threw away the steering at full power (2026-11-08: 44 deg drift)
        vals = [0] * 8
        for side, v in (("left", left), ("right", right)):
            c = w[side]
            vals[c["ch"] - 1] = int(max(-127, min(127, round(v * (c.get("dir") or 1)))))
        now = time.time()
        if not force and self._last_out[0] == vals and now - self._last_out[1] < 0.08:
            return                                       # same command very recently: the keepalive covers it
        self._last_out = (vals, now)
        self.r.motor.drive(vals, keep)                   # PermissionError when the motors are locked

    def halt(self):
        self._last_out = (None, 0.0)
        self.r.motor.stop()

    def stop(self):
        self.cancel.set()
        self.rc_until, self.rc_on = 0.0, False
        self.halt()

    def _imu_ok(self):
        if time.time() - self.r.imu.last > 0.5:
            raise RuntimeError("no IMU data: cannot steer blind")

    # ------------------------------------------------------------------ moves
    def turn(self, deg, speed=None):
        """Spin in place by deg (+ = left) using the IMU. Returns the degrees actually turned."""
        speed = max(TURN_MIN, min(int(speed or TURN_MAX), TURN_MAX))
        imu = self.r.imu
        self._imu_ok()
        last, turned = imu.yaw, 0.0
        t0, still_since, boost = time.time(), None, 0
        limit = 2.5 + abs(deg) / 60
        try:
            while not self.cancel.is_set() and time.time() - t0 < limit:
                self._imu_ok()
                y = imu.yaw
                turned += wrap(y - last)
                last = y
                rate = imu.gyro[2]                       # deg/s, + = turning left
                err = deg - turned
                if abs(err) <= TOL and abs(rate) < 15:
                    break
                ahead = err - rate * COAST               # what is left after coasting
                if abs(ahead) <= TOL:
                    p = 0
                else:
                    p = min(speed, TURN_MIN + boost + 0.6 * abs(ahead))
                    p = p if ahead > 0 else -p
                if p and abs(rate) < 10:                 # pushing but not turning: skids stuck, push harder
                    still_since = still_since or time.time()
                    if time.time() - still_since > 0.25:
                        boost, still_since = min(boost + 6, 40), None
                else:
                    still_since = None
                self.out(-p, p, keep=0.15)
                time.sleep(0.01)
        finally:
            self.halt()
        time.sleep(0.25)
        turned += wrap(imu.yaw - last)
        return turned

    def straight(self, secs, speed=100, back=False):
        """Drive straight for secs, holding the heading with the IMU. Returns the heading error at the end (degrees)."""
        w = self.wheels()
        trim = int(w.get("trim", 10))                    # + = the right wheel needs more power to go straight
        s = -1 if back else 1
        imu = self.r.imu
        self._imu_ok()
        last, turned = imu.yaw, 0.0
        t0 = time.time()
        try:
            while not self.cancel.is_set():
                el = time.time() - t0
                if el >= secs:
                    break
                y = imu.yaw
                turned += wrap(y - last)
                last = y
                corr = max(-35, min(35, -KH * turned))   # turned left (+) -> push the left wheel more
                base = s * speed * min(1.0, 0.4 + el / 0.25)   # soft start
                self.out(base - corr, base + corr + s * trim, keep=0.15)
                time.sleep(0.02)
        finally:
            self.halt()
        time.sleep(0.2)
        turned += wrap(imu.yaw - last)
        return turned

    def timed(self, left, right, secs):
        t0 = time.time()
        try:
            while not self.cancel.is_set() and time.time() - t0 < secs:
                self.out(left, right, keep=0.15)
                time.sleep(0.05)
        finally:
            self.halt()

    def run_step(self, m):
        c = str(m.get("cmd", "")).lower()
        f = lambda k, d: float(m.get(k, d))
        if c == "turn":
            return f"turned {self.turn(f('deg', 90), m.get('speed')):+.0f} of {f('deg', 90):+.0f} deg"
        if c in ("forward", "back"):
            err = self.straight(min(f("secs", 0.6), 5), int(f("speed", 100)), back=c == "back")
            return f"{c} {f('secs', 0.6):.1f}s, heading drift {err:+.0f} deg"
        if c == "arc":
            self.timed(f("left", 60), f("right", 100), min(f("secs", 1), 5))
            return "arc done"
        if c == "wiggle":
            sp, dt = f("speed", 110), f("secs", 0.2)
            for i in range(int(f("n", 6))):
                if self.cancel.is_set():
                    break
                k = 1 if i % 2 else -1
                self.timed(-k * sp, k * sp, dt)
            return "wiggled"
        if c == "wait":
            end = time.time() + min(f("secs", 1), 30)
            while time.time() < end and not self.cancel.is_set():
                time.sleep(0.05)
            return "waited"
        if c == "say":
            threading.Thread(target=self.r.voice.speak, args=(str(m.get("text", ""))[:200], m.get("mood")), daemon=True).start()
            return "said"
        if c == "ask":                                   # e.g. "Start recording, then tap Start"
            opts = m.get("opts") or ["Start", "Cancel"]
            ans = self.r.api.ask_owner(m.get("title", "Drive"), str(m.get("q", "Ready?")), opts, f("timeout", 300))
            if ans != opts[0]:
                self.cancel.set()
            return f"owner answered {ans}"
        if c == "stop":
            self.halt()
            return "stopped"
        raise ValueError(f"unknown move {c!r}")

    def start(self, steps):
        """Run moves in the background (one sequence at a time). Returns (ok, message)."""
        self.wheels()
        if not self.r.cfg["motors_enabled"]:
            raise PermissionError("motors are locked (owner: unlock them first)")
        if not self.lock.acquire(blocking=False):
            return False, "already driving (send stop first)"
        self.cancel.clear()
        self.rc_on = False
        self.state = {"busy": True, "step": "", "log": [], "error": "", "t0": time.time()}

        def go():
            try:
                for i, m in enumerate(steps[:100]):
                    if self.cancel.is_set():
                        self.state["log"].append("cancelled")
                        break
                    self.state["step"] = f"{i + 1}/{len(steps)} {m.get('cmd')}"
                    t = time.time()
                    res = self.run_step(m)
                    self.state["log"].append(f"{m.get('cmd')}: {res} ({time.time() - t:.1f}s)")
            except Exception as e:
                self.state["error"] = str(e)
                self.r.log(f"drive: {e}", "warn")
            finally:
                self.halt()
                self.state["busy"], self.state["step"] = False, ""
                self.lock.release()
        threading.Thread(target=go, daemon=True, name="drive-seq").start()
        return True, f"driving: {len(steps)} move(s)"

    # ------------------------------------------------------------------ remote control
    def rc(self, x, y, maxp=None):
        """Joystick: x + = right, y + = forward (-1..1). Must be repeated every 100-200 ms; stops 0.4 s after the last."""
        if not self.r.cfg["motors_enabled"]:
            raise PermissionError("motors are locked (owner: unlock them first)")
        if self.state.get("busy"):
            self.cancel.set()                            # the joystick wins over a running sequence
        x, y = max(-1.0, min(1.0, float(x))), max(-1.0, min(1.0, float(y)))
        cap = min(int(maxp or self.r.cfg["motor_max"]), self.r.cfg["motor_max"])
        if abs(x) < 0.08 and abs(y) < 0.08:
            self.rc_on, self.rc_hold = False, None
            self.halt()
            return 0, 0
        L, R = y + x, y - x
        m = max(1.0, abs(L), abs(R))
        L, R = L / m * cap, R / m * cap
        if abs(y) > 0.3 and abs(x) < 0.12:               # driving straight: hold the heading like straight() does
            imu = self.r.imu
            if self.rc_hold is None:
                self.rc_hold = [imu.yaw, imu.yaw, 0.0]  # [start yaw, last yaw, turned]
            h = self.rc_hold
            h[2] += wrap(imu.yaw - h[1])
            h[1] = imu.yaw
            corr = max(-30, min(30, -KH * h[2]))
            s = 1 if y > 0 else -1
            L, R = L - corr, R + corr + s * int(self.wheels().get("trim", 10))
        else:
            self.rc_hold = None
            if abs(y) < 0.3:                             # mostly spinning: keep it inside the gyro's range
                L, R = max(-TURN_MAX, min(TURN_MAX, L)), max(-TURN_MAX, min(TURN_MAX, R))
        self.rc_on, self.rc_until = True, time.time() + 0.4
        self.out(L, R, keep=0.3, force=True)
        return int(L), int(R)

    def _rc_watch(self):
        while True:
            time.sleep(0.05)
            if self.rc_on and time.time() > self.rc_until:   # the phone stopped sending: stop the wheels
                self.rc_on, self.rc_hold = False, None
                try:
                    self.halt()
                except Exception:
                    pass

    def status(self):
        return {**{k: v for k, v in self.state.items() if k != "t0"}, "rc": self.rc_on,
                "motors_enabled": self.r.cfg["motors_enabled"], "motor_max": self.r.cfg["motor_max"],
                "yaw": round(self.r.imu.yaw, 1), "wheels": bool((self.r.cfg.data.get("wheels") or {}).get("left"))}
