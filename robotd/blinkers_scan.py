"""Scanner for the BLINKERS display wired straight to Orange Pi GPIO lines (run by robotd/blinkers.py, one process).

A GPIO line can be HIGH, LOW or OFF (input, high impedance), so the display is driven the classic charlieplexing way:
one step at a time, a few contacts HIGH (anodes), one or more LOW (cathodes), the rest OFF; the steps are repeated
fast enough that the eye sees them all at once. Running in its own process keeps the timing steady (robotd's other
threads cannot hold it up).

Usage: python blinkers_scan.py /dev/gpiochip0 1,7,0,3,19,18,2,10
stdin, one JSON object per line:
  {"steps": [[[high offsets], [low offsets]], ...], "dwell_us": 2000}   show these steps (an empty list = all off)
  {"stats": true}                                                        print {"steps_per_s": ...} on stdout
On end of input (robotd stopped) every line goes back to input and is released.
"""
import json
import sys
import threading
import time

import gpiod
from gpiod.line import Direction, Value

OFF = gpiod.LineSettings(direction=Direction.INPUT)
HI = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)
LO = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)


def main():
    chip, offsets = sys.argv[1], [int(x) for x in sys.argv[2].split(",")]
    req = gpiod.request_lines(chip, consumer="blinkers", config={tuple(offsets): OFF})
    print(json.dumps({"ready": True}), flush=True)           # robotd waits for this before it shows anything
    lock, changed, alive = threading.Lock(), threading.Event(), [True]
    state = {"cfgs": [], "dwell": 0.0012, "n": 0, "t0": time.perf_counter()}

    def reader():
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "steps" in msg:
                cfgs = []
                for hi, lo in msg["steps"]:
                    hi, lo = set(hi), set(lo)
                    cfgs.append({o: (HI if o in hi else LO if o in lo else OFF) for o in offsets})
                with lock:
                    state["cfgs"] = cfgs
                    state["dwell"] = max(0.0002, float(msg.get("dwell_us", 1200)) / 1e6)
                changed.set()
            if msg.get("stats"):
                with lock:
                    dt = time.perf_counter() - state["t0"]
                    rate = state["n"] / dt if dt > 0 else 0
                    state["n"], state["t0"] = 0, time.perf_counter()
                print(json.dumps({"steps_per_s": round(rate), "steps": len(state["cfgs"])}), flush=True)
        alive[0] = False
        changed.set()

    threading.Thread(target=reader, daemon=True).start()
    all_off = {o: OFF for o in offsets}
    try:
        while alive[0]:
            with lock:
                cfgs, dwell = state["cfgs"], state["dwell"]
            changed.clear()
            if not cfgs:
                req.reconfigure_lines(all_off)
                changed.wait()
            elif len(cfgs) == 1:                     # one step: set it once and wait for the next command
                req.reconfigure_lines(cfgs[0])
                state["n"] += 1
                changed.wait()
            else:
                while alive[0] and not changed.is_set():
                    for c in cfgs:
                        req.reconfigure_lines(c)
                        state["n"] += 1
                        time.sleep(dwell)               # one plain sleep: steady enough, and light on the CPU
    finally:
        try:
            req.reconfigure_lines(all_off)
        finally:
            req.release()


if __name__ == "__main__":
    main()
