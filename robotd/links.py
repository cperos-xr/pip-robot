"""USB serial links: the CYD display (JSON lines + JPEG frames) and the RP2040 motor controller."""
import glob
import json
import queue
import re
import threading
import time
import zlib

import serial


def find_port(pattern):
    m = sorted(glob.glob(pattern))
    return m[0] if m else None


class CydLink(threading.Thread):
    PATTERN = "/dev/serial/by-id/usb-1a86_USB_Serial*"

    def __init__(self, bus, log):
        super().__init__(daemon=True, name="cyd")
        self.bus, self.log = bus, log
        self.ser = None
        self.wlock = threading.Lock()
        self.img_lock = threading.Lock()
        self.acks = {}
        self.ack_cv = threading.Condition()
        self.next_id = 1
        self.page = "face"
        self.info = {}
        self.last_rx = 0
        self.stats = {"tx_img": 0, "img_fail": 0, "rx": 0}
        self.waiters = {}
        self.wait_cv = threading.Condition()
        self.paused = threading.Event()
        self.hello_seen = False     # the screen's hello carries its I2C devices (IMU on CN1): much waits for it
        self._hello_ask = 0.0

    @property
    def up(self):
        return self.ser is not None and time.time() - self.last_rx < 6

    def send(self, obj):
        t = obj.get("t")
        if t in ("beep", "ask", "listen_state") or (t == "face" and obj.get("chime") is not None):
            self.bus.emit("cyd_sound", obj)     # the voice plays it on the Bluetooth speaker when that is the output
        ser = self.ser
        if not ser:
            return False
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        try:
            with self.wlock:
                ser.write(data)
            return True
        except Exception:
            return False

    def send_pcm(self, pcm, rate=8000, chunk=2048):
        """Stream unsigned 8-bit mono audio to the CYD speaker; paced by the display's acks."""
        for i in range(0, len(pcm), chunk):
            if not self.send_image(pcm[i:i + chunk], timeout=4.0, kind="pcm", rate=rate):
                return False
        return True

    def send_image(self, jpeg, timeout=3.0, kind="img", **meta):
        """Send a binary payload (JPEG image or PCM audio) and wait for the display's ack."""
        if not self.ser or len(jpeg) > self.info.get("img_max", 48 * 1024):
            return False
        with self.img_lock:
            iid = self.next_id
            self.next_id += 1
            hdr = {"t": kind, "id": iid, "len": len(jpeg), "crc": zlib.crc32(jpeg) & 0xFFFFFFFF}
            hdr.update(meta)
            try:
                with self.wlock:
                    self.ser.write((json.dumps(hdr, separators=(",", ":")) + "\n").encode())
                    self.ser.write(jpeg)
            except Exception:
                return False
            with self.ack_cv:
                end = time.time() + timeout
                while iid not in self.acks and time.time() < end:
                    self.ack_cv.wait(end - time.time())
                ack = self.acks.pop(iid, None)
            ok = bool(ack and ack.get("ok"))
            self.stats["tx_img" if ok else "img_fail"] += 1
            return ok

    def pause(self, on):
        """Release the serial port (e.g. for esptool) and wait; pause(False) reconnects."""
        if on:
            self.paused.set()
            for _ in range(40):
                if self.ser is None:
                    break
                time.sleep(0.1)
        else:
            self.paused.clear()

    def request(self, msg, reply_type, timeout=2.0):
        """Send a message and wait for the first reply of type reply_type."""
        with self.wait_cv:
            self.waiters[reply_type] = None
        if not self.send(msg):
            return None
        with self.wait_cv:
            end = time.time() + timeout
            while self.waiters.get(reply_type) is None and time.time() < end:
                self.wait_cv.wait(end - time.time())
            return self.waiters.pop(reply_type, None)

    def _on_msg(self, msg):
        t = msg.get("t")
        if t in self.waiters:
            with self.wait_cv:
                self.waiters[t] = msg
                self.wait_cv.notify_all()
        self.last_rx = time.time()
        self.stats["rx"] += 1
        if t == "ack":
            with self.ack_cv:
                self.acks[msg.get("id")] = msg
                self.ack_cv.notify_all()
            return
        if t == "hello":
            self.hello_seen = True
            self.info = msg
            self.page = msg.get("page", "face")
            self.log(f"CYD hello: {msg.get('fw')} {msg.get('ver')} panel={msg.get('panel')} heap={msg.get('heap')} i2c={msg.get('i2c')}")
            self.send({"t": "hello_ack"})
            self.bus.emit("cyd_hello", msg)
            return
        if t in ("hb", "page"):
            new = msg.get("page") if t == "hb" else msg.get("name")
            if new and new != self.page:
                self.page = new
                self.bus.emit("page", new)
            if t == "hb":
                self.info["hb"] = msg
                if not self.hello_seen and time.time() - self._hello_ask > 3:
                    # the one "hello?" sent when the port opens can get lost (seen after a Pi reboot, 2026-11-08:
                    # no IMU stream, screen said "waiting for IMU..."): keep asking while the screen is alive
                    self._hello_ask = time.time()
                    self.log("CYD is alive but has not said hello yet: asking again")
                    self.send({"t": "hello?"})
                self.send({"t": "time", "hm": time.strftime("%H:%M")})   # keep the screen's link indicator alive
            return
        self.bus.emit("cyd:" + str(t), msg)

    def run(self):
        while True:
            if self.paused.is_set():
                time.sleep(0.3)
                continue
            port = find_port(self.PATTERN)
            if not port:
                time.sleep(2)
                continue
            try:
                ser = serial.Serial(port, 921600, timeout=0.05)
            except Exception as e:
                self.log(f"CYD port open failed: {e}", "warn")
                time.sleep(3)
                continue
            self.ser = ser
            self.log(f"CYD link open on {port}")
            self.hello_seen, self._hello_ask = False, time.time()
            self.send({"t": "hello?"})
            buf = b""
            try:
                while not self.paused.is_set():
                    chunk = ser.read(ser.in_waiting or 1)
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if line.startswith(b"{"):
                            try:
                                self._on_msg(json.loads(line))
                            except ValueError:
                                pass
                    if len(buf) > 65536:
                        buf = b""
            except Exception as e:
                self.log(f"CYD link lost: {e}", "warn")
            finally:
                self.ser = None
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1)


class MotorLink(threading.Thread):
    """RP2040 running rp2040_motor_usb (text protocol, 500 ms failsafe on the firmware side).
    Firmware 0.2+ also sends lines on its own: "I ..." (IMU samples, 50 a second) and "E ..." (a wheelie run ended),
    so one thread reads everything and hands command answers to cmd() through a queue."""
    PATTERN = "/dev/serial/by-id/usb-*RP2040*"

    def __init__(self, cfg, log):
        super().__init__(daemon=True, name="motor")
        self.cfg, self.log = cfg, log
        self.ser = None
        self.lock = threading.Lock()          # one command + its answer at a time
        self.wlock = threading.Lock()         # one write at a time (send() must never wait for an answer)
        self.replies = queue.Queue()
        self.last_pong = 0
        self.fw = ""
        self.info = {}
        self.target = [0] * 8
        self.target_until = 0
        self.paused = threading.Event()
        self.on_imu = None                    # f([ax, ay, az, gx, gy, gz, temp, pitch]) for every "I" line (ints, see firmware)
        self.on_event = None                  # f(text) for every "E" line
        self.on_note = None                   # f(text) for every "N" line (firmware 0.4+)
        self.on_connect = None                # f() when the firmware (re)started: send it the mount, the wheels, the stream rate
        self.need_cfg = True
        self.uptime = 0
        self.dump = None                      # list that collects the lines of a running DUMP
        self._dump_done = threading.Event()
        threading.Thread(target=self._reader, daemon=True, name="motor-rx").start()

    @property
    def up(self):
        return time.time() - self.last_pong < 12

    @property
    def version(self):
        """(major, minor) of the firmware, (0, 0) when unknown."""
        m = re.search(r" v(\d+)\.(\d+)", self.fw or "")
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    def _reader(self):
        buf = b""
        while True:
            ser = self.ser
            if not ser:
                buf = b""
                time.sleep(0.1)
                continue
            try:
                data = ser.read(ser.in_waiting or 1)
            except Exception as e:
                if self.ser is ser:
                    self.log(f"RP2040 read error: {e}", "warn")
                    self._close()
                time.sleep(0.2)
                continue
            if not data:
                continue
            buf += data
            if len(buf) > 65536 and b"\n" not in buf:
                buf = b""
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    self._on_line(line.decode(errors="replace").strip())
                except Exception as e:
                    self.log(f"RP2040 line handler: {e!r}", "warn", persist=False)

    def _on_line(self, line):
        if not line:
            return
        if line.startswith("I "):
            cb = self.on_imu
            if cb:
                try:
                    v = [int(x) for x in line.split()[1:]]
                except ValueError:
                    return
                if len(v) >= 8:                          # firmware 0.4 sends 11 numbers (power, mode, heading added)
                    cb(v)
            return
        if line.startswith("N "):                        # a note, e.g. "N bal 620 43.1 180" = the balancing started
            cb = self.on_note
            if cb:
                cb(line[2:])
            return
        if line.startswith("E "):
            cb = self.on_event
            if cb:
                cb(line[2:])
            return
        d = self.dump
        if d is not None and (line == "END" or line[0].isdigit()):
            if line == "END":
                self._dump_done.set()
            else:
                d.append(line)
            return
        self.replies.put(line)

    def send(self, line):
        """Write a line that has no answer (the keepalive "K")."""
        with self.wlock:
            try:
                if self.ser:
                    self.ser.write((line + "\n").encode())
                    return True
            except Exception as e:
                self.log(f"RP2040 io error: {e}", "warn")
                self._close()
        return False

    def cmd(self, line, timeout=0.6):
        with self.lock:
            if not self.ser:
                return None
            try:
                while True:                              # answers that came too late for an earlier command
                    self.replies.get_nowait()
            except queue.Empty:
                pass
            if not self.send(line):
                return None
            try:
                return self.replies.get(timeout=timeout)
            except queue.Empty:
                return None

    def read_dump(self, timeout=25):
        """The recording in the firmware (DUMP): a list of text lines, or None."""
        self._dump_done.clear()
        self.dump = rows = []
        try:
            r = self.cmd("DUMP", 1.5)
            if not r or not r.startswith("D "):
                return None
            self._dump_done.wait(timeout)
            return rows
        finally:
            self.dump = None

    def _close(self):
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None

    def ping(self):
        r = self.cmd("PING")
        if r and r.startswith("PONG"):
            self.last_pong = time.time()
            self.fw = r
            m = re.search(r"uptime_ms=(\d+)", r)
            up = int(m.group(1)) if m else 0
            if up < self.uptime:                          # it restarted: its settings are gone
                self.need_cfg = True
            self.uptime = up
            return r
        return None

    def get_info(self):
        r = self.cmd("INFO")
        if r and r.startswith("INFO "):
            try:
                self.info = json.loads(r[5:])
            except ValueError:
                pass
        return self.info

    def drive(self, values, duration=0.5, owner_ok=False):
        """Request motor outputs for `duration` seconds (kept alive below the firmware failsafe)."""
        vals = [int(max(-127, min(127, v))) for v in (list(values) + [0] * 8)[:8]]
        if any(vals) and not (self.cfg["motors_enabled"] or owner_ok):
            raise PermissionError("motors are disabled in config (motors_enabled=false); owner approval required")
        cap = self.cfg["motor_max"]
        vals = [max(-cap, min(cap, v)) for v in vals]
        self.target, self.target_until = vals, time.time() + duration
        return self.cmd("M " + " ".join(map(str, vals)))

    def stop(self):
        self.target, self.target_until = [0] * 8, 0
        return self.cmd("STOP")

    def pause(self, on):
        if on:
            self.paused.set()
            with self.lock:
                if self.ser:
                    self._close()
        else:
            self.paused.clear()

    def run(self):
        last_ping = 0
        while True:
            if self.paused.is_set():
                time.sleep(0.3)
                continue
            if not self.ser:
                port = find_port(self.PATTERN)
                if port:
                    try:
                        self.ser = serial.Serial(port, 115200, timeout=0.05)
                        time.sleep(0.2)
                        self.log(f"RP2040 link open on {port}")
                        self.need_cfg, self.uptime = True, 0
                        if self.ping():
                            self.log(f"RP2040 says: {self.fw}")
                    except Exception as e:
                        self.log(f"RP2040 open failed: {e}", "warn")
                        self.ser = None
                if not self.ser:
                    time.sleep(2)
                    continue
            now = time.time()
            if any(self.target) and now < self.target_until:
                self.cmd("M " + " ".join(map(str, self.target)), timeout=0.2)   # keepalive
                time.sleep(0.15)
                continue
            if now - last_ping > 5:
                last_ping = now
                if not self.ping():
                    self.log("RP2040 not answering PING", "warn", persist=False)
                elif self.need_cfg and self.on_connect:
                    self.need_cfg = False
                    try:
                        self.on_connect()
                    except Exception as e:
                        self.log(f"RP2040 setup failed: {e!r}", "warn")
            time.sleep(0.2)
