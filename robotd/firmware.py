"""Flash firmware onto the robot's microcontrollers from the robot itself - always with owner approval
on the touchscreen and a backup of what was there before."""
import glob
import io
import subprocess
import time
import zipfile
from pathlib import Path

import serial

from .core import DATA, ROOT

BACKUPS = DATA / "firmware_backups"
ESPTOOL = str(ROOT / "venv/bin/esptool.py")


def _run(cmd, timeout=300):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"") + (e.stderr or b"")
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        return 124, (out + f"\n(timed out after {timeout} s)")[-1500:]
    return p.returncode, (p.stdout + p.stderr)[-1500:]


def _cyd_reset():
    """Leave the CYD running its app again (a failed esptool run can leave it in the bootloader = black screen)."""
    return _run([ESPTOOL, "--chip", "esp32", "--port", "/dev/ttyUSB0", "--baud", "115200", "--after", "hard_reset", "chip_id"], 60)


def flash_cyd(robot, app_bin, reason="", approve=True):
    """Write an application image (cyd_dashboard.ino.bin) at 0x10000. Settings in NVS are kept."""
    app_bin = Path(app_bin)
    if not app_bin.exists():
        return False, f"{app_bin} not found"
    if approve:
        ans = robot.api.ask_owner("Flash the screen?", f"Install {app_bin.name} ({app_bin.stat().st_size // 1024} KB) on the CYD. {reason}"[:300],
                                  ["Flash", "Cancel"], 90)
        if ans != "Flash":
            return False, f"owner did not approve ({ans})"
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / time.strftime("cyd_app_%Y%m%d_%H%M%S.bin")
    robot.cyd.pause(True)
    try:
        time.sleep(1.0)
        # 921600 baud is fastest, but the CH340 link sometimes corrupts long reads there (2026-11-14:
        # "Corrupt data, expected 0x1000 bytes"): fall back to slower speeds, then write at the speed that worked
        # 2026-11-14 (CYD on the Pi's 13-pin header USB): 921600 baud corrupted or hung long reads; 230400 is reliable
        for baud in ("230400", "115200"):
            rc, out = _run([ESPTOOL, "--chip", "esp32", "--port", "/dev/ttyUSB0", "--baud", baud, "read_flash", "0x10000",
                            str(app_bin.stat().st_size + 65536), str(backup)], timeout=150)
            if rc == 0:
                break
            robot.log(f"CYD flash: backup read failed at {baud} baud", "warn")
            _cyd_reset()
            time.sleep(1.0)
        if rc != 0:
            _cyd_reset()
            return False, "backup failed, nothing flashed: " + out[-300:]
        rc, out = _run([ESPTOOL, "--chip", "esp32", "--port", "/dev/ttyUSB0", "--baud", baud, "write_flash", "-z", "0x10000", str(app_bin)],
                       timeout=240)
        if rc != 0:                          # a half-written app does not boot: the backup can be written back the same way
            robot.log(f"CYD flash FAILED during the write - old app is in {backup.name}", "warn")
            _cyd_reset()
    finally:
        robot.cyd.pause(False)
    ok = rc == 0 and "Hash of data verified" in out
    robot.log(f"CYD flash {'OK' if ok else 'FAILED'}: {app_bin.name} (backup {backup.name})", "info" if ok else "warn")
    robot.store.test("cyd_flash", ok, app_bin.name, backup=str(backup))
    if ok:
        robot.social.moment("firmware", f"I just installed new firmware on my touchscreen face ({app_bin.name}). {reason}", score=2)
    return ok, out[-400:]


def flash_rp2040(robot, uf2, reason="", approve=True):
    """Reboot the RP2040 into its bootloader (1200-baud touch) and load a .uf2 with picotool."""
    uf2 = Path(uf2)
    if not uf2.exists():
        return False, f"{uf2} not found"
    if approve:
        ans = robot.api.ask_owner("Flash the motor controller?", f"Install {uf2.name} on the RP2040. Motors will stop. {reason}"[:300],
                                  ["Flash", "Cancel"], 90)
        if ans != "Flash":
            return False, f"owner did not approve ({ans})"
    robot.motor.pause(True)
    try:
        robot.motor.stop()
        port = (glob.glob("/dev/serial/by-id/usb-*RP2040*") or [None])[0]
        if port:
            try:
                serial.Serial(port, 1200).close()   # arduino-pico: 1200 baud open/close = reboot to BOOTSEL
            except Exception:
                pass
        # the Waveshare board has the USB id 2e8a:0003 both running and in its bootloader, so look for what really
        # changes: the serial port goes away and picotool can talk to the chip
        for _ in range(30):
            time.sleep(0.5)
            if not glob.glob("/dev/serial/by-id/usb-*RP2040*") and _run(["picotool", "info"], 20)[0] == 0:
                break
        else:
            robot.log("RP2040 flash: the board did not enter its bootloader - nothing flashed", "warn")
            return False, "the RP2040 did not enter its bootloader (nothing flashed)"
        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS / time.strftime("rp2040_%Y%m%d_%H%M%S.uf2")
        _run(["picotool", "save", "-a", str(backup)], 120)
        rc, out = _run(["picotool", "load", "-x", str(uf2)], 180)
        if rc != 0:                              # do not leave it sitting in the bootloader with the motors dead
            _run(["picotool", "reboot"], 30)
    finally:
        time.sleep(3)
        robot.motor.pause(False)
    ok = rc == 0
    robot.log(f"RP2040 flash {'OK' if ok else 'FAILED'}: {uf2.name}", "info" if ok else "warn")
    robot.store.test("rp2040_flash", ok, uf2.name, backup=str(backup))
    return ok, out[-400:]


def trusted_build(run, repo_id, branch):
    """True if a GitHub Actions run (the `workflow_run` of an artifact) built this repo's own default branch:
    not a fork's pull request, not some other branch."""
    run = run or {}
    return bool(repo_id) and run.get("repository_id") == repo_id and run.get("head_repository_id") == repo_id \
        and run.get("head_branch") == branch


def fetch_ci_firmware(robot):
    """Download the newest firmware artifact built by GitHub Actions (.github/workflows/firmware.yml).
    Needs data/github.env with GITHUB_REPO=owner/name and GITHUB_TOKEN=<fine-grained token, Actions: read>."""
    import requests
    env = {}
    f = DATA / "github.env"
    if f.exists():
        for line in f.read_text().splitlines():
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    import os
    repo, token = env.get("GITHUB_REPO") or os.environ.get("GITHUB_REPO"), env.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return False, "add GITHUB_REPO and GITHUB_TOKEN to data/github.env", []
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    # Only a build of THIS repo's own default branch is firmware we trust. On a public repo a pull request from a
    # stranger's fork can also leave an artifact called firmware-...: it must never be offered for flashing.
    me = requests.get(f"https://api.github.com/repos/{repo}", headers=h, timeout=30).json()
    repo_id, branch = me.get("id"), me.get("default_branch") or "main"
    if not repo_id:
        return False, f"could not look up {repo} on GitHub (check GITHUB_REPO and GITHUB_TOKEN)", []
    arts = requests.get(f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=50", headers=h, timeout=30).json().get("artifacts", [])
    arts = [a for a in arts if a["name"].startswith("firmware-") and not a.get("expired") and trusted_build(a.get("workflow_run"), repo_id, branch)]
    if not arts:
        return False, f"no firmware artifacts from this repo's {branch} branch yet (push a firmware change to trigger the build)", []
    a = max(arts, key=lambda x: x["created_at"])
    z = requests.get(a["archive_download_url"], headers=h, timeout=120)
    z.raise_for_status()
    out = DATA / "firmware_ci" / a["name"]
    out.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(z.content)).extractall(out)
    files = sorted(str(p.relative_to(ROOT)) for p in out.rglob("*") if p.is_file())
    robot.log(f"downloaded CI firmware {a['name']} ({len(files)} files)")
    return True, a["name"], files
