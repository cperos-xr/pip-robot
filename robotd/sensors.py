"""I2C bus with retries + health stats, MPU-6050 orientation (complementary filter), PCF8574."""
import math
import threading
import time
from collections import deque

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:  # allows running parts of robotd on a laptop
    SMBus = None


class I2CBus:
    def __init__(self, num=0):
        self.num = num
        self.bus = SMBus(num) if SMBus else None
        self.lock = threading.Lock()
        self.window = deque(maxlen=1000)
        self.total_ok = self.total_fail = 0
        # Observed 2026-11-05: after >~50 ms idle the first ~10-18 transactions NACK, then 100% OK.
        # So after an idle gap we 'warm up' with quick writes to a known-good address first.
        self.known = set()
        self.last_t = 0.0
        self.warm_fail = 0
        self.warmups = 0

    def _rec(self, ok):
        self.window.append(1 if ok else 0)
        if ok:
            self.total_ok += 1
        else:
            self.total_fail += 1

    def err_pct(self):
        if not self.window:
            return 0.0
        return 100.0 * (len(self.window) - sum(self.window)) / len(self.window)

    def _warm(self, addr):
        if time.time() - self.last_t < 0.005:
            return
        target = addr if addr in self.known else (min(self.known) if self.known else None)
        if target is None:
            return
        self.warmups += 1
        good = 0
        for _ in range(40):
            try:
                self.bus.write_quick(target)
                good += 1
                if good >= 2:
                    break
            except OSError:
                good = 0
                self.warm_fail += 1

    def _do(self, fn, retries=4, addr=None, count=True):
        if not self.bus:
            raise OSError("no i2c bus")
        with self.lock:
            self._warm(addr)
            try:
                for i in range(retries):
                    try:
                        r = fn()
                        if count:
                            self._rec(True)
                        if addr is not None:
                            self.known.add(addr)
                        return r
                    except OSError:
                        if count:
                            self._rec(False)
                        time.sleep(0.001 * (i + 1))
            finally:
                self.last_t = time.time()
        raise OSError(f"i2c transaction failed {retries}x")

    def read_byte(self, addr, retries=4):
        return self._do(lambda: self.bus.read_byte(addr), retries, addr)

    def write_byte(self, addr, v, retries=4):
        return self._do(lambda: self.bus.write_byte(addr, v), retries, addr)

    def write_reg(self, addr, reg, v, retries=4):
        return self._do(lambda: self.bus.write_byte_data(addr, reg, v), retries, addr)

    def read_regs(self, addr, reg, n, retries=4):
        def f():
            w, r = i2c_msg.write(addr, [reg]), i2c_msg.read(addr, n)
            self.bus.i2c_rdwr(w, r)
            return bytes(r)
        return self._do(f, retries, addr)

    def present(self, addr, tries=3, count=True):
        for _ in range(tries):
            try:
                self._do(lambda: self.bus.write_quick(addr), retries=1, addr=addr, count=count)
                return True
            except OSError:
                pass
        return False

    def scan(self):
        # warm the bus on any responder first, then probe everything
        for _ in range(30):
            try:
                with self.lock:
                    self.bus.write_quick(0x20)
                break
            except OSError:
                pass
        # an empty address not answering is the expected result of a scan, not a bus error: do not count it
        # (2026-11-08: the Pi bus is empty now, so every boot scan left the status page at "100% errors")
        return [a for a in range(0x08, 0x78) if self.present(a, tries=6, count=False)]


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


class Imu(threading.Thread):
    """Reads an MPU-6050 on the Orange Pi I2C bus, or forwarded raw by the CYD (CN1 port), or - since 2026-11-14 -
    forwarded by the RP2040 motor board (GPIO26/27; firmware 0.2+ reads it 500 times a second for wheelies and sends
    50 samples a second here).
    Output orientation is in the robot frame: x forward, y left, z up. pitch>0 nose up, roll>0 right side down."""
    ADDR = 0x68

    def __init__(self, i2c, cfg, bus, log):
        super().__init__(daemon=True, name="imu")
        self.i2c, self.cfg, self.bus, self.log = i2c, cfg, bus, log
        self.lock = threading.Lock()
        self.source = None          # "pi" | "cyd" | "rp2040" | None
        self.rp_pitch = None        # the motor board's own pitch (deg), when it knows the mount
        self.on_mount = None        # called after the mount changed (the motor board needs it too)
        self.pitch = self.roll = self.yaw = 0.0
        self.acc = [0.0, 0.0, 1.0]  # robot frame, g
        self.gyro = [0.0, 0.0, 0.0]  # robot frame, deg/s
        self.temp = 0.0
        self.hz = 0.0
        self.last = 0
        self.raw_hist = deque(maxlen=200)  # sensor-frame (acc, gyro) for calibration
        self._t_prev = None
        self._count = 0
        self._count_t = time.time()
        self._R = None
        self.cyd_err = 0.0
        self.resets = 0
        self._last_reset_log = 0
        self._reset_times = []
        self.power_fault = False
        self.update_mount()
        bus.on("cyd:imu_raw", self._on_cyd_raw)

    # ---------- mounting ----------
    def update_mount(self):
        c = self.cfg["imu"]
        z = _norm(c["up"])
        f = c["forward"]
        d = sum(f[i] * z[i] for i in range(3))
        x = _norm([f[i] - d * z[i] for i in range(3)])
        if sum(abs(v) for v in x) < 1e-6:
            x = _norm(_cross([0, 1, 0], z))
        y = _cross(z, x)
        self._R = [x, y, z]  # rows: robot axes in sensor coordinates
        if getattr(self, "on_mount", None):
            self.on_mount()

    def _to_robot(self, v):
        return [sum(self._R[r][i] * v[i] for i in range(3)) for r in range(3)]

    def level_here(self):
        """Owner says the robot is sitting flat: use the current gravity vector as 'up'."""
        with self.lock:
            hist = list(self.raw_hist)[-50:]
        if len(hist) < 10:
            return False, "not enough IMU samples yet"
        a = [sum(h[0][i] for h in hist) / len(hist) for i in range(3)]
        self.cfg["imu"]["up"] = _norm(a)
        self.cfg["imu"]["leveled"] = True
        self.cfg.save()
        self.update_mount()
        self.pitch = self.roll = self.yaw = 0.0
        return True, "up vector = [%.2f %.2f %.2f] (sensor frame)" % tuple(self.cfg["imu"]["up"])

    def forward_here(self):
        """Owner holds the FRONT of the robot up (like a wheelie): the part of gravity that is not along 'up' points
        forward. Needs 'level' first."""
        with self.lock:
            hist = list(self.raw_hist)[-50:]
        if len(hist) < 10:
            return False, "not enough IMU samples yet"
        a = _norm([sum(h[0][i] for h in hist) / len(hist) for i in range(3)])
        u = _norm(self.cfg["imu"]["up"])
        d = sum(a[i] * u[i] for i in range(3))
        tilt = math.degrees(math.acos(max(-1, min(1, d))))
        if tilt < 15:
            return False, "the front is only %.0f degrees up: lift it higher" % tilt
        self.cfg["imu"]["forward"] = _norm([a[i] - d * u[i] for i in range(3)])
        self.cfg["imu"]["forward_set"] = True
        self.cfg.save()
        self.update_mount()
        self.pitch = self.roll = 0.0
        return True, "forward = [%.2f %.2f %.2f] (sensor frame), front was %.0f degrees up" % (*self.cfg["imu"]["forward"], tilt)

    def tilt_from_up(self):
        """Degrees between gravity now and the saved 'up' (any direction), from the last ~0.4 s of samples."""
        with self.lock:
            hist = list(self.raw_hist)[-20:]
        if len(hist) < 5:
            return None
        a = _norm([sum(h[0][i] for h in hist) / len(hist) for i in range(3)])
        u = _norm(self.cfg["imu"]["up"])
        return math.degrees(math.acos(max(-1, min(1, sum(a[i] * u[i] for i in range(3))))))

    def gyro_cal(self, seconds=2.0):
        time.sleep(seconds)
        with self.lock:
            hist = list(self.raw_hist)[-int(seconds * 40):]
        if len(hist) < 20:
            return False, "not enough samples"
        g = [sum(h[1][i] for h in hist) / len(hist) for i in range(3)]
        spread = max(max(h[1][i] for h in hist) - min(h[1][i] for h in hist) for i in range(3))
        if spread > 6:
            return False, "robot moved during calibration (spread %.1f dps); keep it still and retry" % spread
        old = self.cfg["imu"]["gyro_bias"]
        self.cfg["imu"]["gyro_bias"] = [old[i] + g[i] for i in range(3)]
        self.cfg.save()
        self.yaw = 0.0
        if self.on_mount:
            self.on_mount()
        return True, "gyro bias [%.2f %.2f %.2f] dps" % tuple(self.cfg["imu"]["gyro_bias"])

    # ---------- fusion ----------
    def _ingest(self, a_s, g_s_raw, temp, source, t):
        bias = self.cfg["imu"]["gyro_bias"]
        g_s = [g_s_raw[i] - bias[i] for i in range(3)]
        with self.lock:
            self.raw_hist.append((a_s, g_s))
        a = self._to_robot(a_s)
        g = self._to_robot(g_s)
        dt = 0.02 if self._t_prev is None else max(0.001, min(0.2, t - self._t_prev))
        self._t_prev = t
        roll_acc = math.degrees(math.atan2(a[1], a[2]))
        pitch_acc = math.degrees(math.atan2(a[0], math.sqrt(a[1] ** 2 + a[2] ** 2)))
        mag = math.sqrt(sum(v * v for v in a))
        alpha = 0.98 if 0.8 < mag < 1.2 else 0.995   # trust gyro more while accelerating
        self.roll = alpha * (self.roll + g[0] * dt) + (1 - alpha) * roll_acc
        self.pitch = alpha * (self.pitch - g[1] * dt) + (1 - alpha) * pitch_acc
        self.yaw = (self.yaw + g[2] * dt + 180) % 360 - 180
        self.acc, self.gyro, self.temp, self.source, self.last = a, g, temp, source, t
        self._count += 1
        if t - self._count_t >= 2:
            self.hz = self._count / (t - self._count_t)
            self._count, self._count_t = 0, t
        self.bus.emit("imu", self)

    def _on_cyd_raw(self, m):
        if self.source == "pi":
            return
        a = [v / 16384.0 for v in m["a"]]
        g = [v / 131.0 for v in m["g"]]
        ok, fail = m.get("ok", 0), m.get("fail", 0)
        self.cyd_err = 100.0 * fail / max(1, ok + fail)
        self._ingest(a, g, m.get("tr", 0) / 340.0 + 36.53, "cyd", time.time())

    def on_rp2040(self, v):
        """One "I" line from the motor board: mg x3, 0.1 deg/s x3 (sensor axes, bias not removed), 0.1 C, 0.01 deg."""
        if self.source == "pi":
            return
        self.rp_pitch = v[7] / 100.0
        self._ingest([v[0] / 1000.0, v[1] / 1000.0, v[2] / 1000.0], [v[3] / 10.0, v[4] / 10.0, v[5] / 10.0],
                     v[6] / 10.0, "rp2040", time.time())

    def _init_mpu(self):
        self.i2c.write_reg(self.ADDR, 0x6B, 0x01)  # wake, PLL
        self.i2c.write_reg(self.ADDR, 0x19, 0x09)  # 100 Hz
        self.i2c.write_reg(self.ADDR, 0x1A, 0x03)  # DLPF 44 Hz
        self.i2c.write_reg(self.ADDR, 0x1B, 0x00)  # 250 dps
        self.i2c.write_reg(self.ADDR, 0x1C, 0x00)  # 2 g
        who = self.i2c.read_regs(self.ADDR, 0x75, 1)[0]
        return who

    def _reset_seen(self, why):
        """The MPU-6050 has no reset pin: finding it asleep again means it lost power."""
        self.resets += 1
        now = time.time()
        if now - self._last_reset_log > 10:
            self._last_reset_log = now
            self.log(f"IMU reset detected #{self.resets} ({why}) - GY-521 power/ground glitch?", "warn")
        self.bus.emit("imu_reset", self.resets)
        self._reset_times.append(now)
        recent = [t for t in self._reset_times if now - t < 30]
        self._reset_times = recent
        if len(recent) > 15:
            self.power_fault = True
            self.log(f"IMU power fault: {len(recent)} resets in 30 s - pausing IMU for 20 s (check GY-521 VCC/GND)", "warn")
            self.source = None
            time.sleep(20)
            self._reset_times = []
        else:
            time.sleep(0.05)
        self._init_mpu()
        self.source = "pi"

    def snapshot(self):
        return {"p": round(self.pitch, 2), "r": round(self.roll, 2), "y": round(self.yaw, 1),
                "ax": round(self.acc[0], 3), "ay": round(self.acc[1], 3), "az": round(self.acc[2], 3),
                "gx": round(self.gyro[0], 1), "gy": round(self.gyro[1], 1), "gz": round(self.gyro[2], 1),
                "tc": round(self.temp, 1), "hz": round(self.hz, 1), "src": self.source or "-",
                "err": round(self.i2c.err_pct() if self.source == "pi" else self.cyd_err, 1), "resets": self.resets, "power_fault": self.power_fault,
                "fresh": time.time() - self.last < 1.5}

    def run(self):
        fails = 0
        while True:
            if self.cfg["imu"]["source"] == "cyd" or not self.i2c.bus or (self.source in ("cyd", "rp2040") and time.time() - self.last < 2):
                time.sleep(1)
                continue
            try:
                who = self._init_mpu()
                self.log(f"IMU on Orange Pi I2C (WHO_AM_I=0x{who:02x})")
                self.source = "pi"
                fails = 0
                period = 0.01   # 100 Hz keeps the bus awake (it goes 'cold' after short idle gaps)
                nxt = time.time()
                n = 0
                while True:
                    n += 1
                    if n % 50 == 0 and self.i2c.read_regs(self.ADDR, 0x6B, 1, retries=6)[0] & 0x40:
                        self._reset_seen("PWR_MGMT back to sleep (0x40)")
                        continue
                    d = self.i2c.read_regs(self.ADDR, 0x3B, 14, retries=6)
                    if not any(d[:6]):
                        self._reset_seen("all-zero accelerometer data")
                        continue
                    v = [int.from_bytes(d[i:i + 2], "big", signed=True) for i in range(0, 14, 2)]
                    self._ingest([v[0] / 16384.0, v[1] / 16384.0, v[2] / 16384.0],
                                 [v[4] / 131.0, v[5] / 131.0, v[6] / 131.0], v[3] / 340.0 + 36.53, "pi", time.time())
                    nxt += period
                    time.sleep(max(0, nxt - time.time()))
                    if time.time() - nxt > 0.5:
                        nxt = time.time()
            except OSError as e:
                fails += 1
                if self.source in ("cyd", "rp2040"):
                    time.sleep(1)
                    continue
                if self.source == "pi":
                    self.log(f"IMU read failing on Pi bus: {e}", "warn")
                self.source = None if self.source == "pi" else self.source
                time.sleep(min(30, 2 * fails))


class Pcf8574:
    def __init__(self, i2c, addr=0x20):
        self.i2c, self.addr = i2c, addr
        self.last = None

    def read(self):
        self.last = self.i2c.read_byte(self.addr)
        return self.last

    def write(self, v):
        self.i2c.write_byte(self.addr, v & 0xFF)


class Pir(threading.Thread):
    """White-dome PIR motion sensor (HC-SR501 style) on the Orange Pi 26-pin header: OUT -> pin 7 (PA6 =
    /dev/gpiochip0 line 6, 3.3 V logic), VCC -> 5 V (on 3.3 V it triggered itself), GND. Fitted by the owner 2026-11-08.
    OUT goes high for 1-5 s while something warm moves in front of it; its two knobs set sensitivity and hold time.
    Edges come from the kernel (gpiod), so even a short pulse is never missed. Emits bus event "motion" on each start."""
    CHIP, LINE = "/dev/gpiochip0", 6

    def __init__(self, bus, log):
        super().__init__(daemon=True, name="pir")
        self.bus, self.log = bus, log
        self.ok, self.err = False, ""
        self.active, self.last, self.count = False, 0.0, 0
        self.day, self.today = time.strftime("%Y-%m-%d"), 0

    def run(self):
        from datetime import timedelta
        try:
            import gpiod
            from gpiod.line import Edge, Value
        except ImportError as e:
            self.err = f"python gpiod missing: {e}"
            return
        logged = False
        while True:
            try:
                req = gpiod.request_lines(self.CHIP, consumer="robotd-pir", config={self.LINE: gpiod.LineSettings(edge_detection=Edge.BOTH)})
            except Exception as e:
                self.ok, self.err = False, str(e)
                if not logged:
                    self.log(f"motion sensor (PA6) not readable: {e}", "warn")
                    logged = True
                time.sleep(15)
                continue
            self.ok, self.err, logged = True, "", False
            try:
                self.active = req.get_value(self.LINE) == Value.ACTIVE
                while True:
                    if not req.wait_edge_events(timedelta(seconds=1)):
                        continue
                    for e in req.read_edge_events():
                        if e.event_type == e.Type.RISING_EDGE:
                            now = time.time()
                            day = time.strftime("%Y-%m-%d")
                            if day != self.day:
                                self.day, self.today = day, 0
                            self.active, self.last = True, now
                            self.count += 1
                            self.today += 1
                            self.bus.emit("motion", self)
                        else:
                            self.active = False
            except Exception as e:
                self.ok, self.err = False, str(e)
                self.log(f"motion sensor read error: {e}", "warn")
            finally:
                try:
                    req.release()
                except Exception:
                    pass
            time.sleep(2)

    def ago(self):
        return time.time() - self.last if self.last else None

    def snapshot(self):
        a = self.ago()
        return {"ok": self.ok, "active": self.active, "last_s": round(a, 1) if a is not None else None,
                "count": self.count, "today": self.today, "err": self.err}

    def describe(self):
        if not self.ok:
            return f"not readable ({self.err or 'starting'})"
        a = self.ago()
        if self.active:
            return f"motion now; {self.today} today"
        if a is None:
            return "no motion seen since start"
        when = f"{a:.0f} s" if a < 90 else f"{a / 60:.0f} min" if a < 5400 else f"{a / 3600:.1f} h"
        return f"last motion {when} ago; {self.today} today"
