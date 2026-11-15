"""System status, hardware inventory (self-model vs. what is actually present) and suggestions."""
import glob
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

KNOWN_USB = {
    "05e3:0610": "USB hub (Genesys GL850)",
    "3938:1300": "onn. webcam",
    "1a86:7523": "CH340 serial (CYD display)",
    "33fa:0001": "USB Bluetooth dongle (speaker)",
    "2e8a:0003": "RP2040 in BOOTSEL mode",
}


class SysInfo:
    def __init__(self):
        self._prev = None
        self._net = (False, 0)

    def cpu_pct(self):
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        idle, total = v[3] + v[4], sum(v)
        if not self._prev:
            self._prev = (idle, total)
            return 0.0
        di, dt = idle - self._prev[0], total - self._prev[1]
        self._prev = (idle, total)
        return 100.0 * (1 - di / dt) if dt else 0.0

    @staticmethod
    def temp():
        try:
            return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000.0
        except OSError:
            return 0.0

    @staticmethod
    def mem():
        m = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            m[k] = int(v.split()[0])
        return (m["MemTotal"] - m["MemAvailable"]) / 1024, m["MemTotal"] / 1024

    _ips = ({}, 0.0)

    @staticmethod
    def ips():
        """The robot's IPv4 addresses. Cached for 20 s: every web status poll (each open tab, every 2.5 s) used to start
        an `ip` process, which means forking the whole 150 MB robotd."""
        cached, t = SysInfo._ips
        if cached and time.time() - t < 20:
            return dict(cached)
        out = {}
        try:
            txt = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=2).stdout
            for line in txt.splitlines():
                p = line.split()
                if p[1] != "lo":
                    out[p[1]] = p[3].split("/")[0]
        except Exception:
            pass
        SysInfo._ips = (dict(out), time.time())
        return out

    def internet(self):
        ok, t = self._net
        if time.time() - t > 60:
            try:
                socket.create_connection(("1.1.1.1", 53), timeout=2).close()
                ok = True
            except OSError:
                ok = False
            self._net = (ok, time.time())
        return ok

    def status(self):
        mu, mt = self.mem()
        du = shutil.disk_usage("/")
        return {
            "host": socket.gethostname(), "cpu": round(self.cpu_pct(), 1), "temp": round(self.temp(), 1),
            "mem_used": round(mu), "mem_total": round(mt),
            "disk_used": round(du.used / 1e9, 2), "disk_total": round(du.total / 1e9, 2),
            "load": [round(x, 2) for x in os.getloadavg()],
            "up": int(float(open("/proc/uptime").read().split()[0])), "ips": self.ips(), "net": self.internet(),
        }


def usb_devices():
    devs = []
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            vid = open(d + "/idVendor").read().strip()
            pid = open(d + "/idProduct").read().strip()
        except OSError:
            continue
        if vid == "1d6b":
            continue  # root hubs
        prod = ""
        try:
            prod = open(d + "/product").read().strip()
        except OSError:
            pass
        devs.append({"id": f"{vid}:{pid}", "product": prod, "path": os.path.basename(d),
                     "speed": open(d + "/speed").read().strip() if os.path.exists(d + "/speed") else ""})
    return devs


def inventory(robot):
    """Build the device list shown on the dashboard. Status: ok / warn / missing / planned / unknown."""
    usb = {d["id"]: d for d in usb_devices()}
    last = robot.store.last_tests()
    items = []

    keys = {"Orange Pi": "pi", "USB hub": "hub", "Webcam": "cam", "Touch display": "cyd", "RP2040": "rp", "L293D": "l293",
            "PCF8574": "pcf", "MPU-6050": "imu", "Speaker": "spk", "Battery": "power", "PCA9685": "pca", "Motors": "motors",
            "Servos": "servos", "Wheels": "wheels", "CYD CN1": "i2c", "Pi speaker": "pispk"}

    def add(name, status, detail, info=""):
        k = next((v for kk, v in keys.items() if name.startswith(kk)), "")
        items.append({"n": name, "s": status, "d": detail, "info": info, "k": k})

    st = robot.sys.status()
    add("Orange Pi Zero (brain)", "ok", f"H2+ 512MB, {st['temp']:.0f}C, load {st['load'][0]}",
        "Allwinner H2+, Armbian. I2C bus 0 on header pins 3/5 at 25 kHz.")
    add("USB hub", "ok" if "05e3:0610" in usb else "missing", "Genesys 05e3:0610 on main USB port")
    cam_ok = "3938:1300" in usb and robot.cam.present
    add("Webcam", "ok" if cam_ok else "missing", "onn. 3938:1300 /dev/video0, up to 2592x1944 + mic")
    ci = robot.cyd.info
    add("Touch display (CYD)", "ok" if robot.cyd.up else ("warn" if "1a86:7523" in usb else "missing"),
        f"{ci.get('fw', '?')} {ci.get('ver', '')} {ci.get('panel', '')[:7]}".strip(),
        f"ESP32 CH340 at 921600 baud. Heap {ci.get('heap', '?')}. Images sent {robot.cyd.stats['tx_img']}.")
    mi = robot.motor
    add("RP2040 motor controller", "ok" if mi.up else "missing", (mi.fw or "no answer")[:46],
        "Header-13 USB. Firmware rp2040_motor_usb with 500 ms failsafe.")
    add("L293D 8-motor board", "unknown", "cperos-xr/l293d - cannot self-test without motors",
        "Motor n: fwd GPIO 2(n-1), rev GPIO 2(n-1)+1 on the RP2040.")
    cydbus = robot.cyd_bus()
    e = robot.i2c.err_pct()
    if robot.pi_i2c:
        add("Pi I2C bus 0", "ok" if e < 0.5 else "warn", f"{e:.1f}% raw errors (retried), 25 kHz",
            "Errors are hidden by retries. Reseat wires or move sensors to CYD CN1 if this stays high.")
    else:
        add("Pi I2C bus 0", "planned", "wired (header pins 3/5), nothing connected yet - free for future sensors")
    where = "CYD CN1" if "0x20" in cydbus else "Pi bus 0"
    pcf = robot.devices_seen.get(0x20)
    add("PCF8574 I/O expander", "ok" if pcf else "missing", f"0x20 on {where}, 8 quasi-bidirectional pins",
        f"Last read: {format(robot.pcf.last, '08b') if robot.pcf.last is not None else '-'}")
    imu = robot.imu
    imu_st = "ok" if imu.source and time.time() - imu.last < 2 else ("warn" if robot.devices_seen.get(0x68) else "missing")
    add("MPU-6050 IMU (GY-521)", imu_st, f"0x68 via {imu.source or '-'}, {imu.hz:.0f} Hz",
        f"pitch {imu.pitch:.1f} roll {imu.roll:.1f}; leveled={robot.cfg['imu']['leveled']}; power-loss resets since start: {imu.resets}")
    if cydbus:
        ce = robot.imu.cyd_err
        add("CYD CN1 I2C bus", "ok" if ce < 1 else "warn", "IO27 SDA / IO22 SCL, found " + " ".join(cydbus) + (f", {ce:.1f}% read errors" if robot.imu.source == "cyd" else ""))
    spk = last.get("cyd_tone")
    add("Speaker / earpiece", ("ok" if spk["ok"] else "warn") if spk else "unknown",
        "CYD DAC GPIO26 -> amp" + ("" if spk else " - run Tests > Speaker"))
    from .voice import PI_CARD, _playback_cards
    ps = last.get("speaker_pi")
    if PI_CARD not in _playback_cards():
        add("Pi speaker (line out)", "missing", "analog-codec overlay is off")
    elif not robot.voice.cfg().get("pi_speaker", True):
        add("Pi speaker (line out)", "planned", "no speaker connected; line out ready on 13-pin pins 7+8")
    else:
        vo = robot.voice.cfg()
        add("Pi speaker (line out)", ("ok" if ps["ok"] else "warn") if ps else "unknown",
            f"13-pin pins 7+8, volume {vo['pi_volume']}/31" + (", voice uses it" if robot.voice.output_device()[0] == "pi" else "")
            + ("" if ps else " - run Tests > Orange Pi speaker"))
    for k, v in st["ips"].items():
        add(f"Network {k}", "ok", v)
    bt = getattr(robot, "battery", None)
    if bt:
        add("Battery (3S3P, INA219 0x40)", {"ok": "ok", "charging": "ok", "low": "warn", "crit": "warn", "external": "warn"}.get(bt.state, "missing"),
            bt.short(), "Nine 260 mAh vape cells as 3S3P (780 mAh) + 3S protection board; INA219 on Pi I2C bus 0 measures the "
            "whole robot. Settings > Battery; black box in data/battery/*.csv.")
    wh = robot.cfg.data.get("wheels") or {}
    if wh.get("left") and wh.get("right"):
        add("Wheel motors (2)", "ok", f"left = motor {wh['left']['ch']}, right = motor {wh['right']['ch']}; "
            + ("enabled" if robot.cfg["motors_enabled"] else "locked"),
            f"Wheels test {wh.get('tested', '')}: they turn from {wh['left']['min_duty'] * 100 // 127}% power (wheels up). "
            "Motor power: 7.4 V airsoft battery on the L293D board's EXT VOLTAGE terminal (unplug when not driving).")
    else:
        add("Wheel motors (2)", "unknown", "on U5; run Tests > Wheel setup (needs you)",
            "Fitted by the owner 2026-11-08. Motor power: 7.4 V airsoft battery on the EXT VOLTAGE terminal.")
    pir = getattr(robot, "pir", None)
    add("PIR motion sensor", ("ok" if pir.ok else "warn") if pir else "unknown",
        (pir.describe() if pir else "not started") + " - Pi pin 7 (PA6), VCC 5 V",
        "White dome; its 2 knobs set sensitivity and hold time. Cup test 2026-11-08: silent when covered, detects waving. "
        "On 3.3 V it triggered itself, so keep VCC on 5 V.")
    for n in ("PCA9685 servo driver", "Servos"):
        add(n, "planned", "not installed yet")
    known = set(KNOWN_USB) | {"2e8a:000a", "2e8a:00c0", "2e8a:f00a"}
    for vid, d in usb.items():
        if vid not in known and not vid.startswith("2e8a:"):
            add(f"Unknown USB {vid}", "warn", d["product"] or "unrecognised device", "New hardware? The robot does not know this one yet.")
    return items


def ideas(robot):
    out = []
    last = robot.store.last_tests()

    def idea(i, title, detail, p="info", action=False):
        out.append({"id": i, "title": title, "detail": detail, "p": p, "action": action})

    e = robot.i2c.err_pct()
    if e > 1 and robot.pi_i2c:
        idea("i2c_wiring", f"I2C bus is noisy ({e:.0f}% raw errors)",
             "Retries keep it working, but: push all jumpers in firmly, power the PCF8574 from 3.3V (pin 1), share GND, "
             "keep I2C wires short and away from USB cables. Or move the sensors to the CYD CN1 port.", "high")
    if robot.imu.resets:
        idea("imu_power", f"My IMU lost power {robot.imu.resets} times",
             "The MPU-6050 keeps waking up in its power-on state, so its supply is dropping out. Check the GY-521 VCC and GND "
             "jumpers (and the PCF8574 it is chained through). Pushing them in firmly or using the CYD CN1 cable should fix it.", "high")
    if not robot.cfg["imu"]["leveled"]:
        idea("imu_level", "Teach me which way is level",
             "Put the robot on a flat surface, open IMU and tap LEVEL. Then tap GYRO 0 while it is still.", "med")
    for t, name in (("cyd_colors", "screen colours"), ("cyd_touch", "touch accuracy"), ("cyd_tone", "speaker")):
        if t not in last:
            idea("run_" + t, f"Check my {name}", f"Open Tests and run '{name}' so I know it works.", "med")
    if not robot.service_enabled():
        idea("autostart", "Start my software automatically at boot?",
             "Installs and enables the robotd systemd service so the face and dashboard come up after every boot.", "med", True)
    if robot.motor.up:
        idea("motors_next", "Motor controller ready",
             "Two wheels are on U5. Next: lift the wheels off the ground; after you approve I pulse each channel briefly to learn "
             "which wheel it is and which way is forward.")
    idea("pca9685", "Add the PCA9685 servo board", "Address 0x40 on the same I2C bus; servos need their own 5-6V supply.")
    idea("self_view", "Let me see my own body",
         "Detach the brain unit (Pi + hub + camera) and point the camera at the robot; I can learn what my parts look like.")
    idea("agent", "Give me a thinking brain",
         "Connect an LLM agent (Claude API, or Hermes on a bigger computer) through my HTTP API for conversation and self-programming.")
    return out
