"""Where does robotd's CPU time go? A tiny built-in sampling profiler (no extra packages).

GET /api/debug/threads?secs=10 (owner): for every thread, the CPU time it used in that window (from
/proc/self/task/<tid>/stat) and the code it was seen in most often (sys._current_frames sampled 20 times a second).
Use it to find a thread that is busy when the robot should be idle. Costs about 1% CPU while it runs.
"""
import collections
import os
import sys
import threading
import time

TICK = os.sysconf("SC_CLK_TCK")
HERE = os.path.dirname(__file__)


def _ticks():
    out = {}
    for tid in os.listdir("/proc/self/task"):
        try:
            with open(f"/proc/self/task/{tid}/stat") as f:
                rest = f.read().rsplit(")", 1)[1].split()
            out[int(tid)] = int(rest[11]) + int(rest[12])        # utime + stime
        except (OSError, IndexError, ValueError):
            pass
    return out


def _where(frame):
    """The innermost frame, plus the innermost frame inside robotd (the interesting one), as 'file:line func'."""
    inner, own = None, None
    f = frame
    while f is not None:
        fn = f.f_code.co_filename
        s = f"{os.path.basename(fn)}:{f.f_lineno} {f.f_code.co_name}"
        if inner is None:
            inner = s
        if own is None and fn.startswith(HERE) and not fn.endswith("profiler.py"):
            own = s
        f = f.f_back
    return own or inner, inner


def sample(secs=10.0, hz=20):
    secs = max(1.0, min(60.0, float(secs)))
    me = threading.get_ident()
    seen = collections.defaultdict(collections.Counter)
    before, t0 = _ticks(), time.time()
    while time.time() - t0 < secs:
        for ident, frame in sys._current_frames().items():
            if ident != me:
                own, inner = _where(frame)
                seen[ident][own if own == inner else f"{own}  <-  {inner}"] += 1
        time.sleep(1.0 / hz)
    after, dt = _ticks(), time.time() - t0
    rows, known = [], set()
    for th in threading.enumerate():
        tid = th.native_id
        known.add(tid)
        cpu = (after.get(tid, 0) - before.get(tid, 0)) / TICK
        total = sum(seen[th.ident].values()) or 1
        rows.append({"thread": th.name, "tid": tid, "cpu_s": round(cpu, 2), "cpu_pct": round(100 * cpu / dt, 1),
                     "where": [f"{round(100 * n / total)}% {w}" for w, n in seen[th.ident].most_common(4)]})
    other = sum(after.get(t, 0) - before.get(t, 0) for t in after if t not in known) / TICK   # C threads (OpenCV, BLAS...)
    rows.sort(key=lambda r: -r["cpu_s"])
    return {"secs": round(dt, 1), "total_pct": round(sum(r["cpu_pct"] for r in rows) + 100 * other / dt, 1),
            "other_native_threads_pct": round(100 * other / dt, 1), "threads": rows}
