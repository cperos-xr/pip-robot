"""Built-in self tests. Each returns (ok, summary, lines, extra). Every run is stored in long-term memory."""
import io
import math
import re
import socket
import time

from PIL import Image

from .camera import fit_jpeg
from .core import PHOTOS, ROOT
from .health import usb_devices

TESTS = {}


def test(tid, name):
    def deco(fn):
        TESTS[tid] = (name, fn)
        return fn
    return deco


@test("i2c_scan", "I2C bus scan")
def t_i2c_scan(r):
    names = {0x20: "PCF8574", 0x68: "MPU-6050", 0x69: "MPU-6050(AD0)", 0x40: "PCA9685"}
    pi = r.i2c.scan()
    r.pi_i2c = pi
    resp = r.cyd.request({"t": "i2c_scan"}, "i2c", 3.0)
    cyd = [int(a, 16) for a in ((resp or {}).get("i2c") or {}).get("found", [])]
    r.devices_seen = {a: True for a in pi + cyd}
    if resp:
        r.cyd.info["i2c"] = resp.get("i2c")
    lines = [f"Pi bus 0: 0x{a:02x} {names.get(a, '?')}" for a in pi] or ["Pi bus 0: nothing (wired, free for future sensors)"]
    lines += [f"CYD CN1: 0x{a:02x} {names.get(a, '?')}" for a in cyd] or ["CYD CN1: nothing" if resp else "CYD CN1: no answer"]
    found = set(pi) | set(cyd)
    missing = {0x20, 0x68} - found
    return (not missing, f"found {len(found)} device(s)" + (f", missing {', '.join(hex(m) for m in missing)}" if missing else ""), lines, {})


@test("i2c_health", "I2C bus reliability")
def t_i2c_health(r):
    if r.imu.source == "cyd":
        h0 = (r.cyd.info.get("hb") or {})
        a = r.imu.cyd_err
        time.sleep(0.2)
        return (a < 2.0, f"CYD CN1 bus: {a:.2f}% failed IMU reads (cumulative)", [f"IMU stream {r.imu.hz:.0f} Hz from the CYD", "<2% good, 2-10% marginal wiring, >10% bad"], {"err_pct": round(a, 2)})
    ok = fail = 0
    for i in range(150):
        for a in (0x20, 0x68):
            try:
                r.i2c._do(lambda a=a: r.i2c.bus.read_byte(a), retries=1, addr=a)
                ok += 1
            except OSError:
                fail += 1
    pct = 100.0 * fail / max(1, ok + fail)
    return (pct < 2.0, f"Pi bus: {pct:.1f}% of raw transactions failed ({fail}/{ok + fail})",
            [f"cold-start warmups {r.i2c.warmups}, warmup NACKs {r.i2c.warm_fail}", "<2% good, 2-10% marginal wiring, >10% bad"], {"err_pct": round(pct, 2)})


@test("imu_check", "IMU sanity")
def t_imu(r):
    who = r.i2c.read_regs(0x68, 0x75, 1)[0] if r.imu.source != "cyd" else 0x68
    time.sleep(0.6)
    s = r.imu.snapshot()
    mag = math.sqrt(s["ax"] ** 2 + s["ay"] ** 2 + s["az"] ** 2)
    gyro = max(abs(s["gx"]), abs(s["gy"]), abs(s["gz"]))
    ok = who == 0x68 and 0.85 < mag < 1.15 and s["fresh"]
    return (ok, f"|a|={mag:.3f} g, pitch {s['p']:.1f}, roll {s['r']:.1f}",
            [f"WHO_AM_I=0x{who:02x} source={s['src']} {s['hz']} Hz", f"gyro max {gyro:.1f} dps (still: <5)", f"temp {s['tc']} C"], s)


@test("pcf_read", "PCF8574 inputs")
def t_pcf(r):
    v = r.pcf_read()
    return (True, f"pins P7..P0 = {v:08b}", ["1 = high/floating, 0 = pulled low", "nothing connected yet reads 11111111", "bus: " + ("CYD CN1" if "0x20" in r.cyd_bus() else "Pi bus 0")], {"value": v})


@test("camera_snap", "Camera photo")
def t_cam(r):
    path, img = r.cam.save_photo(PHOTOS, "test")
    ext = img.getextrema()
    spread = sum(hi - lo for lo, hi in ext) / 3
    jpg = fit_jpeg(img, 320, 220, min(40000, r.cyd.info.get("img_max", 49152) - 1024))
    r.cyd.send_image(jpg, x=0, y=20, w=320, h=220, slot="show", caption=path.name)
    return (spread > 20, f"{img.width}x{img.height} saved {path.name}", [f"brightness spread {spread:.0f} (0 = blank image)"], {"path": str(path)})


@test("rp2040_ping", "RP2040 ping")
def t_ping(r):
    t0 = time.time()
    p = r.motor.ping()
    ms = (time.time() - t0) * 1000
    info = r.motor.get_info() if p else {}
    return (bool(p), (p or "no answer")[:60], [f"round trip {ms:.0f} ms", f"uptime {info.get('uptime_ms', 0) // 1000} s, cmds {info.get('cmds')}, bad {info.get('bad')}"], info)


@test("motor_failsafe", "Motor failsafe")
def t_failsafe(r):
    # duty 1/127 on motor 1 (harmless even with a motor attached), then stop talking and watch the firmware zero it
    if not r.motor.cmd("M 1 0 0 0 0 0 0 0"):
        return (False, "RP2040 not answering", [], {})
    on = r.motor.get_info()
    time.sleep(0.9)
    off = r.motor.get_info()
    ok = on.get("m", [0])[0] == 1 and off.get("failsafe") and not any(off.get("m", [1]))
    return (ok, "outputs auto-zeroed after silence" if ok else "failsafe did NOT trip", [f"during: {on.get('m')}", f"after 0.9 s: {off.get('m')} failsafe={off.get('failsafe')}"], {})


@test("usb_inventory", "USB devices")
def t_usb(r):
    devs = usb_devices()
    want = {"05e3:0610": "hub", "3938:1300": "webcam", "1a86:7523": "CYD"}
    ids = {d["id"] for d in devs}
    rp = any(i.startswith("2e8a:") for i in ids)
    missing = [n for i, n in want.items() if i not in ids] + ([] if rp else ["RP2040"])
    return (not missing, f"{len(devs)} USB devices" + (f", missing {', '.join(missing)}" if missing else ", all expected present"),
            [f"{d['id']} {d['product'][:28]} {d['speed']}M" for d in devs], {})


@test("network", "Network + internet")
def t_net(r):
    ips = r.sys.ips()
    try:
        t0 = time.time()
        socket.create_connection(("1.1.1.1", 53), timeout=3).close()
        rtt = (time.time() - t0) * 1000
        dns = socket.gethostbyname("github.com")
        ok, s = True, f"online, {rtt:.0f} ms to 1.1.1.1"
    except OSError as e:
        ok, s, dns = False, f"offline: {e}", "-"
    return (ok, s, [f"{k}: {v}" for k, v in ips.items()] + [f"github.com -> {dns}"], {})


@test("cyd_link", "Display link speed")
def t_link(r):
    img = Image.new("RGB", (320, 220))
    px = img.load()
    for y in range(220):
        for x in range(0, 320, 2):
            px[x, y] = ((x * 7) & 255, (y * 5) & 255, ((x + y) * 3) & 255)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    jpg = buf.getvalue()
    t0 = time.time()
    oks = sum(r.cyd.send_image(jpg, x=0, y=20, w=320, h=220, slot="none") for _ in range(3))
    dt = (time.time() - t0) / 3
    kbps = len(jpg) * 8 / dt / 1000 if dt else 0
    return (oks == 3, f"{oks}/3 images accepted, {len(jpg) // 1024} KB each in {dt * 1000:.0f} ms ({kbps:.0f} kbit/s)", ["checks CRC + ack path used by camera and photos"], {"kbps": round(kbps)})


@test("system_health", "Brain health")
def t_sys(r):
    st = r.sys.status()
    free_mb = st["mem_total"] - st["mem_used"]
    disk_free = st["disk_total"] - st["disk_used"]
    probs = []
    if st["temp"] > 70:
        probs.append("hot")
    if free_mb < 80:
        probs.append("low RAM")
    if disk_free < 1:
        probs.append("low disk")
    return (not probs, "healthy" if not probs else ", ".join(probs),
            [f"temp {st['temp']} C, cpu {st['cpu']}%", f"RAM free {free_mb} MB", f"disk free {disk_free:.1f} GB", f"uptime {st['up'] // 60} min"], {})


@test("roboos_portable", "RoboOS stays robot-free")
def t_roboos_portable(r):
    """RoboOS (roboos/) also runs without a robot and takes apps from other people, so it must never import robotd or
    reach for the robot object. Robot features go through the Host API (roboos/host.py, robotd/desktop_host.py)."""
    bad = []
    for p in sorted((ROOT / "roboos").rglob("*.py")):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            code = line.split("#")[0]
            if (re.search(r"^\s*(from|import)\s+(robotd|\.\.)", code)
                    or re.search(r"\b(self|desk|app)\.r\.\w|\bself\.d\.r\.\w|\.robot\.\w", code)):
                bad.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:60]}")
    n = len(list((ROOT / "roboos").rglob("*.py")))
    return (not bad, f"{n} files, robot-free" if not bad else f"{len(bad)} places reach for the robot", bad[:8], {"files": n})


@test("scripts_valid", "Demo scripts are valid")
def t_scripts(r):
    """Every demo script (routines/scripts/ and data/scripts/) loads and passes the checks in robotd/scripts.py."""
    files = r.scripts.files()
    bad = []
    for name in files:
        try:
            errs = r.scripts.load(name)[2]
        except Exception as e:
            errs = [str(e)]
        bad += [f"{name}: {e}" for e in errs]
    return (not bad, f"{len(files)} scripts, all valid" if not bad else f"{len(bad)} problems", bad[:8], {"scripts": len(files)})


def run_test(r, tid):
    name, fn = TESTS[tid]
    r.log(f"test {tid} started")
    t0 = time.time()
    try:
        ok, summary, lines, extra = fn(r)
    except Exception as e:
        ok, summary, lines, extra = False, f"error: {e}", [], {}
    dt = time.time() - t0
    r.store.test(tid, ok, summary, lines=lines, secs=round(dt, 2), **{k: v for k, v in extra.items() if isinstance(v, (int, float, str, bool))})
    r.log(f"test {tid} {'PASS' if ok else 'FAIL'}: {summary}")
    return {"t": "test_result", "id": tid, "name": name, "ok": ok, "sum": summary, "lines": lines[:8],
            "when": time.strftime("%H:%M"), "show": tid != "camera_snap"}


def tests_list(r):
    last = r.store.last_tests()
    items = []
    for tid, (name, _) in TESTS.items():
        l = last.get(tid)
        items.append({"id": tid, "n": name, "last": ("pass" if l["ok"] else "fail") if l else "",
                      "when": time.strftime("%m-%d %H:%M", time.localtime(l["ts"])) if l else ""})
    return items


# ---------------------------------------------------------------- voice / vision tests
@test("voice_speak", "Speech output (screen earpiece)")
def t_speak(r):
    t0 = time.time()
    wav = r.voice.tts_wav("Hello! This is my voice test.")
    pcm = r.voice.wav_to_u8(wav, 8000)
    ok = r.cyd.send_pcm(pcm, 8000)
    return (ok and len(pcm) > 4000, f"spoke {len(pcm) / 8000:.1f}s of audio via CYD speaker" if ok else "display did not accept audio",
            [f"TTS + transfer {time.time() - t0:.1f}s", "volume follows Settings > Volume on the screen"], {})


@test("speaker_pi", "Orange Pi speaker")
def t_speaker_pi(r):
    from .voice import PI_CARD, _playback_cards
    if PI_CARD not in _playback_cards():
        return False, "Pi line out not found (analog-codec overlay off?)", ["needs overlays=... analog-codec in /boot/armbianEnv.txt"], {}
    t0 = time.time()
    ok = r.voice.play(r.voice.tts_wav("Testing my Orange Pi speaker. One, two, three."), f"plughw:CARD={PI_CARD},DEV=0")
    if not ok:
        return False, "aplay failed on the Pi line out", [], {}
    ans = r.api.ask_owner("Speaker test", "Did you hear me from the speaker on the Orange Pi?", ["Yes", "No"], 60)
    vol = r.voice.cfg()["pi_volume"]
    info = [f"line out volume {vol}/31, played in {time.time() - t0:.1f}s", "speaker across 13-pin header pins 7 + 8 (Mono Differential)"]
    if ans == "No":
        return False, "played, but you could not hear it", info + ["check the two wires on pins 7 and 8, then raise the volume in the web app"], {}
    return True, "you heard it" if ans == "Yes" else "played (nobody answered)", info, {"heard": ans}


@test("voice_ears", "Speech recognition")
def t_ears(r):
    import os
    import re
    phrase = "hello robot please read the sign"
    wav = r.voice.tts_wav(phrase, speed=140)
    want = set(phrase.split())
    engines = ["vosk"] + (["cloud"] if r.voice.cfg().get("stt") == "cloud" and os.environ.get("OPENROUTER_API_KEY") else [])
    lines, scores = [f"said:  {phrase}"], {}
    for eng in engines:              # the one TALK uses (cloud when set up) decides pass / fail
        t0 = time.time()
        try:
            heard = r.voice.transcribe_wav(wav, eng)
        except Exception as e:
            heard = f"(failed: {e})"
        got = set(re.sub(r"[^a-z ]", "", heard.lower()).split())
        scores[eng] = len(want & got) / len(want)
        name = "offline Vosk" if eng == "vosk" else f"cloud {r.voice.stt_model()}"
        lines.append(f"{name}: '{heard}' ({scores[eng] * 100:.0f}%, {time.time() - t0:.1f} s)")
    main = engines[-1]
    return (scores[main] >= 0.6, f"understood {scores[main] * 100:.0f}% of a test phrase ({'cloud' if main == 'cloud' else 'offline'})",
            lines, {"score": round(scores[main], 2), **{f"score_{k}": round(v, 2) for k, v in scores.items()}})


@test("mic_check", "Microphone")
def t_mic(r):
    if not r.voice.mic_present:
        return (False, "webcam microphone not found", [], {})
    mean, peak = r.voice.mic_level(1)
    return (peak > 5, f"mic open, level avg {mean:.0f} peak {peak:.0f}", ["1 s level check only - audio is not stored", "0 means the mic is dead or muted"], {})


@test("ocr_check", "Text reading (OCR)")
def t_ocr(r):
    from PIL import ImageDraw, ImageFont
    from .brain import tesseract
    img = Image.new("RGB", (900, 260), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.load_default(size=64)
    except TypeError:
        f = ImageFont.load_default()
    d.text((30, 40), "HELLO ROBOT 2026", fill="black", font=f)
    d.text((30, 140), "read me please", fill="black", font=f)
    t0 = time.time()
    text = tesseract(img)
    ok = "ROBOT" in text.upper() and "READ" in text.upper()
    return (ok, "local OCR read the test card" if ok else "local OCR misread the test card", [f"read: {' / '.join(text.splitlines())[:80]}", f"{time.time() - t0:.1f}s"], {})


@test("ai_brain", "AI brain (Claude)")
def t_ai(r):
    if not r.brain.online:
        return (False, "offline - no ANTHROPIC_API_KEY", ["put ANTHROPIC_API_KEY=... in /root/robot/data/anthropic.env", "then: systemctl restart robotd"], {})
    t0 = time.time()
    reply = r.brain.ask("This is a quick self-test. Reply with just the word ready.", journal=False)
    return ("ready" in reply.lower(), f"replied in {time.time() - t0:.1f}s: {reply[:50]}", [f"usage so far {r.brain.usage}"], {})



@test("gaze_check", "Eye tracking (needs you)")
def t_gaze(r):
    """Interactive: the robot asks you to look in four directions and checks what it detects."""
    import statistics
    plan = [("LEFT", 1, 0), ("RIGHT", -1, 0), ("UP", 0, -1), ("at me", 0, 0)]   # expected sign of pupil offset (camera view)
    r.cam.start_live("test")
    lines, correct = [], 0
    try:
        r.voice.speak("Eye tracking test. Sit close, face the camera, and move only your eyes.")
        last = 0
        for name, ex, ey in plan:
            r.face("curious", f"Look {name}", sec=4, look=[-ex * 0.8, ey * 0.7], look_ms=3500)
            r.voice.speak(f"Look {name.lower()}.")
            time.sleep(0.8)
            xs, ys, t_end = [], [], time.time() + 2.5
            while time.time() < t_end:
                frame, last = r.cam.next_frame(last, timeout=1.0)
                if frame:
                    _, st = r.vision.process(frame, mode="gaze", draw=False)
                    if st.get("gaze"):
                        xs.append(st["gaze"][0]); ys.append(st["gaze"][1])
            if not xs:
                lines.append(f"{name}: no eyes found"); continue
            gx, gy = statistics.median(xs), statistics.median(ys)
            ok = (ex == 0 or gx * ex > 0.1) and (ey == 0 or gy * ey > 0.1) and (ex or ey or (abs(gx) < 0.3 and abs(gy) < 0.3))
            correct += ok
            lines.append(f"{name}: x {gx:+.2f} y {gy:+.2f} {'OK' if ok else 'miss'} ({len(xs)} frames)")
    finally:
        r.cam.stop_live("test")
    return (correct >= 3, f"{correct}/4 directions detected", lines, {"score": correct / 4})


# ---------------------------------------------------------------- wheels (needs the owner at the robot)
def _pulse(r, ch, duty, secs=0.7):
    """Turn one motor channel (1-8) forward at duty (of 127) for secs, then STOP. The firmware also stops by itself 0.5 s
    after the last command, so even a crash here cannot leave a wheel running."""
    vals = [0] * 8
    vals[ch - 1] = int(max(-127, min(127, duty)))
    line = "M " + " ".join(map(str, vals))
    end = time.time() + secs
    try:
        while time.time() < end:
            r.motor.cmd(line, timeout=0.2)
            time.sleep(0.12)
    finally:
        r.motor.stop()


@test("wheels", "Wheel setup (needs you)")
def t_wheels(r):
    """Interactive: with the wheels lifted, spin one channel at a time; the owner taps which wheel turned and which way.
    Owner, 2026-11-08: "try motor test". This is the only code that turns motors while motors_enabled is false, and only
    after the owner taps Ready on the touchscreen (so someone is at the robot and the wheels are up). Each pulse is
    0.7 s forward. The answers are saved as config "wheels": {"left"/"right": {"ch", "dir", "min_duty"}}."""
    if not r.motor.up:
        return (False, "RP2040 not answering", [], {})
    ask = r.api.ask_owner
    r.voice.speak("Wheel test! Please lift me up so both wheels are off the table.")
    if ask("Wheel test", "Lift me so BOTH wheels are off the table, then tap Ready. I will spin one channel at a time "
                        "for less than a second.", ["Ready", "Cancel"], 120) != "Ready":
        return (False, "cancelled: nobody tapped Ready", [], {})
    found, lines = {}, []
    plan = [(7, (60, 95, 127)), (8, (60, 95, 127))] + [(c, (127,)) for c in (1, 2, 3, 4, 5, 6)]   # U5 = probably 7 + 8
    try:
        for ch, duties in plan:
            if len(found) == 2:
                break
            hit = None
            for duty in duties:
                pct = duty * 100 // 127
                r.face("thinking", f"Channel {ch}, {pct}% power", sec=3)
                time.sleep(0.6)
                _pulse(r, ch, duty)
                ans = ask("Wheel test", f"Channel {ch} at {pct}% power. Did a wheel turn?", ["Left wheel", "Right wheel", "Nothing"], 90)
                if ans is None:
                    lines.append(f"ch{ch} {pct}%: no answer - stopped")
                    raise TimeoutError
                if ans != "Nothing":
                    hit = (ans.split()[0].lower(), duty)
                    break
                lines.append(f"ch{ch} {pct}%: nothing")
            if not hit:
                continue
            side, duty = hit
            way = ask("Wheel test", f"If I were on the floor, would that {side} wheel push me forward or backward?",
                      ["Forward", "Backward", "Not sure"], 90)
            d = {"Forward": 1, "Backward": -1}.get(way, 0)
            lines.append(f"ch{ch}: {side} wheel at {duty * 100 // 127}%, {way or 'no answer'}")
            if side in found:
                lines.append(f"(the {side} wheel was already channel {found[side]['ch']}; keeping the first)")
                continue
            found[side] = {"ch": ch, "dir": d, "min_duty": duty}
    except TimeoutError:
        pass
    finally:
        r.motor.stop()
    if found:
        w = r.cfg.data.setdefault("wheels", {})
        w.update(found)
        w["tested"] = time.strftime("%Y-%m-%d %H:%M")
        r.cfg.save()
    ok = len(found) == 2 and all(v["dir"] for v in found.values())
    r.voice.speak("Thank you! Now I know my wheels." if ok else "Thanks. I did not learn both wheels yet.")
    summary = ", ".join(f"{s} = channel {v['ch']} ({'forward' if v['dir'] > 0 else 'reversed' if v['dir'] < 0 else 'direction ?'}, "
                        f"moves at {v['min_duty'] * 100 // 127}%)" for s, v in sorted(found.items())) or "no wheel found"
    return (ok, summary, lines, {"wheels_found": len(found)})


def _drive(r, left, right, secs):
    """Both wheels for secs (values -127..127, + = forward, using the wheel map from the wheels test), then STOP."""
    w = r.cfg.data.get("wheels") or {}
    vals = [0] * 8
    for side, v in (("left", left), ("right", right)):
        c = w.get(side)
        if c:
            vals[c["ch"] - 1] = int(max(-127, min(127, v * (c.get("dir") or 1))))
    line = "M " + " ".join(map(str, vals))
    end = time.time() + secs
    try:
        while time.time() < end:
            r.motor.cmd(line, timeout=0.2)
            time.sleep(0.12)
    finally:
        r.motor.stop()


@test("first_drive", "First drive on the floor (needs you)")
def t_first_drive(r):
    """Interactive, after the wheels test: on the floor, roll forward 0.5 s (47%, 74%, then 100% until it moves), then a
    small left turn. Owner, 2026-11-08: "lets do wheels". Like the wheels test, it runs only after the owner taps Ready on
    the touchscreen, and motors_enabled stays as it is. Saves wheels.drive_duty (the power that moved it)."""
    w = r.cfg.data.get("wheels") or {}
    if not (w.get("left") and w.get("right")):
        return (False, "run the wheels test first", [], {})
    if not r.motor.up:
        return (False, "RP2040 not answering", [], {})
    ask = r.api.ask_owner
    r.voice.speak("Drive test! Put me on the floor with room in front of me.")
    if ask("Drive test", "Put me on the FLOOR (not a table), about 1 m clear in front, cables slack. Tap Ready and I will "
                        "roll forward for half a second.", ["Ready", "Cancel"], 120) != "Ready":
        return (False, "cancelled: nobody tapped Ready", [], {})
    lines, moved = [], None
    try:
        for duty in (60, 95, 127):
            pct = duty * 100 // 127
            r.face("happy", f"Forward, {pct}% power", sec=3)
            time.sleep(1.2)
            _drive(r, duty, duty, 0.5)
            ans = ask("Drive test", f"Forward at {pct}% power. Did I move?", ["Straight", "Curved", "Not really"], 90)
            if ans is None:
                lines.append(f"forward {pct}%: no answer - stopped")
                return (False, "no answer", lines, {})
            lines.append(f"forward {pct}%: {ans.lower()}")
            if ans != "Not really":
                moved = (duty, ans)
                break
        if not moved:
            return (False, "did not move even at full power", lines, {})
        duty, how = moved
        if how == "Curved":
            side = ask("Drive test", "Which way did I curve?", ["To my left", "To my right"], 60)
            lines.append(f"curved {side or '?'}")
            w["curve"] = {"To my left": "left", "To my right": "right"}.get(side, "?")
        else:
            w["curve"] = "none"
        r.face("curious", "Now a little turn to the left", sec=3)
        time.sleep(1.5)
        _drive(r, -duty, duty, 0.4)
        turn = ask("Drive test", "Did I turn to MY left (anticlockwise seen from above)?", ["Yes", "Turned right", "Did not turn"], 90)
        lines.append(f"left turn: {(turn or 'no answer').lower()}")
    finally:
        r.motor.stop()
    w["drive_duty"] = duty
    w["drive_tested"] = time.strftime("%Y-%m-%d %H:%M")
    r.cfg.save()
    ok = turn == "Yes"
    r.voice.speak("I moved! That was my first drive." if ok else "I moved, but something was not quite right.")
    return (ok, f"rolled forward at {duty * 100 // 127}% ({how.lower()}), left turn: {(turn or 'no answer').lower()}", lines,
            {"drive_duty": duty})


# ---------------------------------------------------------------- motion sensor (PIR on the Pi's pin 7)
@test("pir_read", "Motion sensor")
def t_pir_read(r):
    p = r.pir
    if not p.ok:
        return (False, f"pin 7 (PA6) not readable: {p.err or 'starting'}", ["Check the wire on 26-pin header pin 7"], {})
    a = p.ago()
    return (True, p.describe(), [f"signal now: {'high (motion)' if p.active else 'low'}",
                                 f"detections since start: {p.count}, today: {p.today}",
                                 "VCC must be 5 V (on 3.3 V it triggers itself)"], {"today": p.today, "last_s": round(a) if a else -1})


@test("pir_check", "Motion sensor cup test (needs you)")
def t_pir_check(r):
    """Interactive: with the dome covered it must stay quiet for 30 s; uncovered it must see the owner wave."""
    p = r.pir
    if not p.ok:
        return (False, f"pin 7 (PA6) not readable: {p.err or 'starting'}", [], {})
    ask = r.api.ask_owner
    r.voice.speak("Motion sensor test. Put a cup over my white dome, then tap Ready.")
    if ask("Motion sensor test 1/2", "Cover the white dome with a cup, step back, then tap Ready. I wait 10 s, then count for 30 s.",
           ["Ready", "Cancel"], 300) != "Ready":
        return (False, "cancelled", [], {})
    time.sleep(10)
    c0 = p.count
    r.toast("Counting 30 s - keep the cup on", 2500)
    time.sleep(30)
    covered = p.count - c0
    r.voice.speak("Now take the cup off, tap Ready, and wave at me a few times.")
    if ask("Motion sensor test 2/2", "Take the cup off, tap Ready, then wave at the dome a few times for 20 s.",
           ["Ready", "Cancel"], 300) != "Ready":
        return (False, "cancelled after part 1", [f"covered: {covered} detections in 30 s"], {})
    c1 = p.count
    time.sleep(20)
    waved = p.count - c1
    ok = covered == 0 and waved >= 1
    r.voice.speak("My motion sensor works!" if ok else "Something is not right with my motion sensor.")
    lines = [f"covered 30 s: {covered} detections (want 0)", f"waving 20 s: {waved} detections (want 1 or more)"]
    if covered:
        lines.append("Fires by itself: check VCC is on 5 V, turn sensitivity down, keep it away from the Pi's Wi-Fi")
    if not waved:
        lines.append("Saw nothing: turn the sensitivity knob up, check the dome faces you")
    return (ok, f"covered {covered}, waving {waved}", lines, {"covered": covered, "waved": waved})
