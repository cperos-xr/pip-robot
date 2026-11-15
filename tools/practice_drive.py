#!/usr/bin/env python3
"""Practice drive: run ONE slow routine from routines/practice_drive.json, log the IMU, stop on any alarm.

Separate from robotd on purpose: nothing here runs at boot and robotd's behaviour does not change.
It talks to the robot's own HTTP API on 127.0.0.1 (the same calls the owner's app uses).

  venv/bin/python tools/practice_drive.py straight_forward --dry-run   # check the plan, moves nothing
  venv/bin/python tools/practice_drive.py straight_forward             # real run

Rules:
- The motors must already be unlocked by the owner (this script never unlocks them).
- Before any wheel turns, the owner taps Start on the touchscreen. Anything else cancels the run.
- It stops (POST /api/stop) on: a tilt alarm, a stale IMU, the owner's STOP, the run ending, or Ctrl-C.
- Duty and time limits come from the routines file and are checked before anything runs.
- Log: data/drive_logs/practice_<routine>_<time>.csv (time, step, yaw, pitch, roll, gyro z, tilt).
"""
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8080"
LOG_DIR = ROOT / "data" / "drive_logs"
MOVES = {"forward", "back", "arc", "wiggle", "wait"}


def token():
    return (ROOT / "data" / "api_token").read_text().strip()


def call(method, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Robot-Token": token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def check_plan(name, plan):
    lim = plan["limits"]
    routine = plan["routines"].get(name)
    if not routine:
        raise SystemExit(f"no routine called {name!r}. Choose: {', '.join(plan['routines'])}")
    total = 0.0
    for s in routine["steps"]:
        cmd = s.get("cmd")
        if cmd not in MOVES:
            raise SystemExit(f"step {cmd!r} is not allowed in a practice routine")
        duty = max(abs(float(s.get(k, 0))) for k in ("speed", "left", "right") if k in s) if any(
            k in s for k in ("speed", "left", "right")) else 0
        if duty > lim["max_duty"]:
            raise SystemExit(f"duty {duty} is above the practice limit {lim['max_duty']}")
        secs = float(s.get("secs", 0))
        if cmd != "wait" and secs > lim["max_secs_per_move"]:
            raise SystemExit(f"{secs} s is above the per-move limit {lim['max_secs_per_move']} s")
        total += secs * (2 * int(s.get("n", 1)) if cmd == "wiggle" else 1)
    if total > lim["max_run_secs"]:
        raise SystemExit(f"run is {total:.1f} s, above the limit {lim['max_run_secs']} s")
    return routine, total


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        raise SystemExit(__doc__)
    name = args[0]
    plan = json.loads((ROOT / "routines" / "practice_drive.json").read_text())
    routine, total = check_plan(name, plan)
    print(f"Routine: {routine['title']} ({total:.1f} s of wheel time, duty at most {plan['limits']['max_duty']}/127)")
    for s in routine["steps"]:
        print("  ", s)
    code, st = call("GET", "/api/drive")
    if code != 200:
        raise SystemExit(f"cannot read drive status (HTTP {code})")
    if st.get("busy"):
        raise SystemExit("already driving: send STOP first")
    if not st.get("motors_enabled"):
        print("Motors are LOCKED. The owner must unlock them first. Nothing was run.")
        return
    if dry:
        print("Dry run: nothing moved. Run again without --dry-run after the owner unlocks the motors.")
        return

    seq = [{"cmd": "ask", "title": "Practice drive", "timeout": 120, "opts": ["Start", "Cancel"],
            "q": f"{routine['title']}: is the floor clear, about 3 feet before a wall? Tap Start to run."}]
    seq += routine["steps"] + [{"cmd": "stop"}]
    code, reply = call("POST", "/api/drive", {"seq": seq})
    if code != 200:
        raise SystemExit(f"drive refused (HTTP {code}): {reply}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"practice_{name}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    lim = plan["limits"]
    stop_reason, p0, r0, max_tilt, yaw0, yaw_last, start = "", None, None, 0.0, None, 0.0, time.time()
    seen_busy = False
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "yaw", "pitch", "roll", "gyro_z", "tilt"])
        try:
            while True:
                time.sleep(0.1)
                code, imu = call("GET", "/api/imu", timeout=3)
                _, st = call("GET", "/api/drive", timeout=3)
                if code != 200 or not imu.get("fresh", False):
                    stop_reason = "IMU not fresh (no data)"
                    break
                if p0 is None:
                    p0, r0, yaw0 = imu["p"], imu["r"], imu["y"]
                tilt = max(abs(imu["p"] - p0), abs(imu["r"] - r0))
                max_tilt = max(max_tilt, tilt)
                yaw_last = imu["y"]
                w.writerow([round(time.time() - start, 2), imu["y"], imu["p"], imu["r"], imu["gz"], round(tilt, 1)])
                if tilt > lim["tilt_stop_deg"]:
                    stop_reason = f"tilt alarm {tilt:.0f} deg"
                    break
                if st.get("busy"):
                    seen_busy = True
                elif seen_busy or time.time() - start > 150:
                    stop_reason = "run ended"
                    break
                elif time.time() - start > 130:
                    stop_reason = "owner did not tap Start"
                    break
        except KeyboardInterrupt:
            stop_reason = "Ctrl-C"
        finally:
            call("POST", "/api/stop")                 # always: STOP the wheels
    _, st = call("GET", "/api/drive")
    cancelled = any("answered Cancel" in line for line in st.get("log", []))
    errors = st.get("error") or ""
    print(f"Stopped: {stop_reason or 'run ended'}" + (" (owner cancelled)" if cancelled else ""))
    if yaw0 is not None:
        print(f"Heading change (veer): {yaw_last - yaw0:+.1f} deg, +=left. Largest tilt: {max_tilt:.1f} deg.")
    print("Distance: not measured (no wheel encoders yet). Measure the move with a tape if you need it.")
    print(f"Errors: {errors or 'none'}. Log: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
