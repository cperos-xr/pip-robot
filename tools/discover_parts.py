#!/usr/bin/env python3
"""What is this robot made of? Look at what is plugged in and match it against knowledge/parts.yaml.

Run it ON THE ROBOT (the brain computer), as root, after tools/install.sh:

  python3 tools/discover_parts.py            print what was found, by role (brain, face, eyes, ears, voice, muscles ...)
  python3 tools/discover_parts.py --write    the same, then save this robot's body to data/body.yaml (private, never
                                             committed). robotd's AI reads that file to know its own body.
  python3 tools/discover_parts.py --json     machine-readable (for an AI helper)
  python3 tools/discover_parts.py --no-i2c   do not scan I2C buses

It only reads: /proc, /sys, /dev/serial/by-id, ALSA's card list, and `i2cdetect -y -r` (read-only probes) on the brain
board's own I2C buses (never on a PC, never on HDMI/DDC buses). It never opens a serial port, so it is safe while
robotd runs. If robotd is running, its own device list is added (it can see the screen's I2C port and ask the boards
for their firmware).

Things only a person knows (wheels, what is wired to which pin, a salvaged display) go in body.yaml under
owner_notes: --write keeps them, and keeps parts marked source: owner.
Standard library only; PyYAML (python3-yaml, from install.sh) is needed to read the parts list.
"""
import argparse
import datetime
import glob
import json
import os
import platform
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "knowledge" / "parts.yaml"
BODY = ROOT / "data" / "body.yaml"
ROLES = ["brain", "face", "eyes", "ears", "voice", "muscles", "balance", "senses", "power", "plumbing"]
SKIP_I2C = re.compile(r"hdmi|ddc|dp aux|i915|nvidia|radeon|amdgpu|smbus|nouveau|gmbus|csi|isp", re.I)


def read(path, default=""):
    try:
        return Path(path).read_text(errors="replace").strip().strip("\x00")
    except OSError:
        return default


def run(cmd, timeout=8):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


# ------------------------------------------------------------------------------------------------ looking around
def board():
    model = read("/proc/device-tree/model")
    is_sbc = bool(model)
    if not model:
        model = " ".join(x for x in (read("/sys/class/dmi/id/sys_vendor"), read("/sys/class/dmi/id/product_name")) if x)
    mem = 0
    for line in read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            mem = int(line.split()[1]) // 1024
    osr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', read("/etc/os-release"), re.M))
    return {"model": model or "unknown computer", "sbc": is_sbc, "arch": platform.machine(), "ram_mb": mem,
            "os": osr.get("PRETTY_NAME", platform.platform()), "python": platform.python_version(),
            "hostname": platform.node()}


def usb():
    out = []
    for d in sorted(glob.glob("/sys/bus/usb/devices/*")):
        vid, pid = read(d + "/idVendor"), read(d + "/idProduct")
        if not vid or vid == "1d6b" or read(d + "/bDeviceClass") == "09":
            continue    # root hubs and hubs (a hub is listed by its own entry in parts.yaml if it matters)
        classes = sorted({read(i + "/bInterfaceClass") for i in glob.glob(d + ":*")} - {""})
        out.append({"id": f"{vid}:{pid}", "product": read(d + "/product"), "maker": read(d + "/manufacturer"),
                    "port": os.path.basename(d), "classes": classes})
    # a hub is still worth knowing about
    for d in sorted(glob.glob("/sys/bus/usb/devices/*")):
        vid, pid = read(d + "/idVendor"), read(d + "/idProduct")
        if vid and vid != "1d6b" and read(d + "/bDeviceClass") == "09":
            out.append({"id": f"{vid}:{pid}", "product": read(d + "/product") or "USB hub", "maker": read(d + "/manufacturer"),
                        "port": os.path.basename(d), "hub": True})
    return out


def serial_ports():
    return {os.path.basename(p): os.path.realpath(p) for p in sorted(glob.glob("/dev/serial/by-id/*"))}


def cameras():
    out = []
    for d in sorted(glob.glob("/sys/class/video4linux/video*")):
        if read(d + "/index", "0") == "0":           # each camera makes several nodes; index 0 is the picture one
            name = read(d + "/name")
            if not re.search(r"codec|isp|decoder|encoder|hevc|cedrus|bcm2835-(isp|codec)|rpivid|pispbe", name, re.I):
                out.append({"dev": "/dev/" + os.path.basename(d), "name": name})
    return out


def sound_cards():
    out = []
    for m in re.finditer(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(.*?)\n\s*(.*)$", read("/proc/asound/cards"), re.M):
        n = m.group(1)
        out.append({"card": int(n), "id": m.group(2), "name": m.group(4).strip() or m.group(3).strip(),
                    "capture": bool(glob.glob(f"/proc/asound/card{n}/pcm*c")),
                    "playback": bool(glob.glob(f"/proc/asound/card{n}/pcm*p"))})
    return out


def bluetooth():
    return [os.path.basename(p) for p in sorted(glob.glob("/sys/class/bluetooth/hci*"))]


def i2c_buses(b, scan=True):
    out = []
    for dev in sorted(glob.glob("/dev/i2c-*"), key=lambda p: int(p.rsplit("-", 1)[1])):
        n = int(dev.rsplit("-", 1)[1])
        name = read(f"/sys/class/i2c-adapter/i2c-{n}/name")
        bus = {"bus": n, "name": name, "found": [], "scanned": False}
        if not scan:
            bus["skipped"] = "--no-i2c"
        elif not b["sbc"]:
            bus["skipped"] = "a PC's I2C buses hold memory and video chips: not scanned"
        elif SKIP_I2C.search(name):
            bus["skipped"] = "video/camera bus: not scanned"
        else:
            txt = run(["i2cdetect", "-y", "-r", str(n)], timeout=20)
            if not txt:
                bus["skipped"] = "i2cdetect missing (apt install i2c-tools) or no permission (run as root)"
            else:
                bus["scanned"] = True
                for line in txt.splitlines()[1:]:
                    row, _, cells = line.partition(":")
                    for i, c in enumerate(cells.split()):
                        if re.fullmatch(r"[0-9a-f]{2}|UU", c):
                            addr = int(row, 16) + i
                            bus["found"].append(addr)
        out.append(bus)
    return out


def robotd_devices():
    """robotd's own device list, if it is running (it sees the screen's I2C port and asks the boards for firmware)."""
    tok = read(ROOT / "data" / "api_token")
    if not tok:
        return None
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/api/devices", headers={"X-Robot-Token": tok})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read())
        return data.get("devices", data) if isinstance(data, dict) else data
    except Exception:
        return None


def look(scan_i2c=True):
    b = board()
    return {"board": b, "usb": usb(), "serial": serial_ports(), "cameras": cameras(), "sound": sound_cards(),
            "bluetooth": bluetooth(), "i2c": i2c_buses(b, scan_i2c), "gpio": len(glob.glob("/dev/gpiochip*")),
            "robotd": robotd_devices()}


# ------------------------------------------------------------------------------------------------ matching
def load_catalog():
    try:
        import yaml
    except ImportError:
        return None, "PyYAML is missing (apt install python3-yaml): showing what is plugged in without names"
    try:
        return yaml.safe_load(CATALOG.read_text())["parts"], ""
    except Exception as e:
        return None, f"could not read {CATALOG}: {e}"


def match(seen, catalog):
    found, claimed_usb, claimed_i2c = [], set(), set()
    b = seen["board"]

    def add(p, where):
        found.append({"id": p["id"], "name": p["name"], "role": p["role"], "support": p["support"], "where": where,
                      "notes": " ".join(str(p.get("notes", "")).split())})

    # the brain: the first board entry that matches; "" = any other single-board computer, "__pc__" = a PC
    brains = [p for p in catalog if p["role"] == "brain"]
    pick = next((p for p in brains if p["detect"].get("board") not in (None, "", "__pc__")
                 and p["detect"]["board"].lower() in b["model"].lower()), None)
    if not pick:
        key = "" if b["sbc"] else "__pc__"
        pick = next((p for p in brains if p["detect"].get("board") == key), None)
    if pick:
        add(pick, f"{b['model']} ({b['arch']}, {b['ram_mb']} MB, {b['os']})")

    usb_ids = {d["id"]: d for d in seen["usb"]}
    rank = {"built_in": 0, "recipe": 1, "new": 2}
    i2c_owner = {}                      # (bus, addr) -> the part that got it; parts that share an address are noted
    for p in sorted(catalog, key=lambda p: rank.get(p.get("support"), 3)):
        det = p.get("detect") or {}
        if p["role"] == "brain" or not det:
            continue
        hits = []
        for uid in det.get("usb", []):
            if uid in usb_ids:
                d = usb_ids[uid]
                hits.append(f"USB {uid} {d['product']}".strip())
                claimed_usb.add(uid)
        if det.get("serial"):
            rx = re.compile("^" + re.escape(det["serial"]).replace(r"\*", ".*") + "$")
            hits += [f"{v} ({k})" for k, v in seen["serial"].items() if rx.match(k)]
        if det.get("video") and seen["cameras"]:
            hits += [f"{c['dev']} {c['name']}" for c in seen["cameras"]]
        if det.get("bluetooth") and seen["bluetooth"]:
            hits.append("Bluetooth " + " ".join(seen["bluetooth"]))
        for key, flag in (("sound_in", "capture"), ("sound_out", "playback")):
            if det.get(key):
                hits += [f"sound card {c['id']} ({c['name']})" for c in seen["sound"]
                         if c[flag] and re.search(det[key], c["id"])]
        for bus in seen["i2c"]:
            for a in det.get("i2c", []):
                if a not in bus["found"]:
                    continue
                if (bus["bus"], a) in i2c_owner:     # e.g. 0x40 = INA219 or PCA9685: the supported part keeps it
                    first = i2c_owner[(bus["bus"], a)]
                    first["where"] += f" (0x{a:02x} could also be: {p['name']})"
                    continue
                hits.append(f"I2C bus {bus['bus']} at 0x{a:02x}")
                claimed_i2c.add((bus["bus"], a))
                i2c_owner[(bus["bus"], a)] = None
        if hits:
            add(p, "; ".join(dict.fromkeys(hits)))
            for k, v in i2c_owner.items():
                if v is None:
                    i2c_owner[k] = found[-1]

    # the specific webcam wins over "any webcam"; a specific speaker card over "any sound card"
    ids = {f["id"] for f in found}
    if "onn_webcam" in ids:
        found = [f for f in found if f["id"] != "usb_webcam"]
    if "rp2040_motor_board" in ids:     # running motor firmware can report the bootloader's id (2e8a:0003) too
        found = [f for f in found if f["id"] != "rp2040_bootloader"]
    if "orange_pi_line_out" in ids:
        for f in found:
            if f["id"] == "usb_speaker":
                f["where"] = "; ".join(x for x in f["where"].split("; ") if "(Codec)" not in x and " Codec " not in x)
        found = [f for f in found if f["id"] != "usb_speaker" or f["where"]]

    # a USB device is explained when a part claimed its id, or it is a camera (class 0e), sound card (01) or
    # Bluetooth adapter (e0) and that kind of part was found
    kinds = {"0e": bool(seen["cameras"]), "01": bool(seen["sound"]), "e0": bool(seen["bluetooth"])}
    unknown = [{"what": f"USB {d['id']} {d['maker']} {d['product']}".strip(), "where": "USB port " + d["port"]}
               for d in seen["usb"] if d["id"] not in claimed_usb and not d.get("hub")
               and not any(kinds.get(c) for c in d.get("classes", []))]
    for bus in seen["i2c"]:
        for a in bus["found"]:
            if (bus["bus"], a) not in claimed_i2c:
                unknown.append({"what": f"I2C device at 0x{a:02x}", "where": f"bus {bus['bus']} ({bus['name']})"})
    return found, unknown


# ------------------------------------------------------------------------------------------------ output
def report(seen, found, unknown, note):
    b = seen["board"]
    print(f"\nThis robot's brain: {b['model']}  ({b['arch']}, {b['ram_mb']} MB RAM, {b['os']}, Python {b['python']})")
    if note:
        print(f"  ({note})")
    if found:
        mark = {"built_in": "works today", "recipe": "works with a small change", "new": "needs new code"}
        for role in ROLES:
            rows = [f for f in found if f["role"] == role]
            for f in rows:
                print(f"\n  [{role}] {f['name']}  -- {mark.get(f['support'], f['support'])}")
                print(f"      found: {f['where']}")
                if f["support"] != "built_in" and f["notes"]:
                    print(f"      how:   {f['notes']}")
        missing = [r for r in ("face", "eyes", "ears", "voice", "muscles") if not any(f["role"] == r for f in found)]
        if missing:
            print("\n  Not found (fine: Pip works without them): " + ", ".join(missing))
    else:
        print("\n  USB:", ", ".join(f"{d['id']} {d['product']}" for d in seen["usb"]) or "nothing")
        print("  cameras:", ", ".join(c["dev"] for c in seen["cameras"]) or "none")
        print("  sound:", ", ".join(c["id"] for c in seen["sound"]) or "none")
    print("\n  I2C buses on the brain:")
    none = "none (a PC has no I2C header)" if not b["sbc"] else "none (/dev/i2c-* missing: I2C may be switched off)"
    for bus in seen["i2c"] or [{"bus": "-", "name": none, "found": [], "scanned": True}]:
        found_txt = " ".join(f"0x{a:02x}" for a in bus["found"]) or ("" if bus["bus"] == "-" else "nothing answered")
        print(f"    {bus['bus']} {bus['name']}" + (": " + found_txt if found_txt else "") if bus.get("scanned")
              else f"    {bus['bus']} {bus['name']}: {bus.get('skipped', '')}")
    if unknown:
        print("\n  Plugged in, but not in knowledge/parts.yaml yet (ask the owner what these are):")
        for u in unknown:
            print(f"    - {u['what']}  [{u['where']}]")
    rd = seen.get("robotd")
    if rd:
        print("\n  robotd's own view (it can see the screen's I2C port and talk to the boards):")
        for d in rd:
            print(f"    {d.get('s', '?'):8} {d.get('n', '')}: {d.get('d', '')}")
    print()


def write_body(seen, found, unknown):
    try:
        import yaml
    except ImportError:
        yaml = None
    old = {}
    if BODY.exists() and yaml:
        try:
            old = yaml.safe_load(BODY.read_text()) or {}
        except Exception:
            old = {}
    kept = [p for p in (old.get("parts") or []) if isinstance(p, dict) and p.get("source") == "owner"]
    b = seen["board"]
    body = {
        "version": 1,
        "written": datetime.date.today().isoformat(),
        "brain": {k: b[k] for k in ("model", "arch", "ram_mb", "os", "python")},
        "parts": [dict(f, source="found", status="found") for f in found if f["role"] != "brain"] + kept,
        "unknown": unknown,
        "i2c_buses": [{"bus": x["bus"], "name": x["name"], "found": [f"0x{a:02x}" for a in x["found"]]} for x in seen["i2c"]],
        "owner_notes": old.get("owner_notes") or [
            "Write here what only a person knows: wheels and which motor channel drives which, what is wired to which pin,",
            "a salvaged part and what it does, the plate and frame it is built on. robotd's AI reads this file.",
        ],
    }
    head = ("# This robot's own body. Written by tools/discover_parts.py, then corrected by the owner and their AI.\n"
            "# Private: data/ is never committed. robotd's AI reads this instead of knowledge/hardware.yaml.\n"
            "# Parts you add by hand: give them  source: owner  so the next --write keeps them.\n")
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=110) if yaml else json.dumps(body, indent=2)
    BODY.parent.mkdir(parents=True, exist_ok=True)
    BODY.write_text(head + text)
    print(f"saved {BODY} ({len(body['parts'])} parts, {len(unknown)} unknown)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="save data/body.yaml")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--no-i2c", action="store_true", help="do not scan I2C buses")
    a = ap.parse_args()
    if sys.platform != "linux":
        sys.exit("run this on the robot's brain computer (Linux), not on your PC")
    seen = look(scan_i2c=not a.no_i2c)
    catalog, note = load_catalog()
    found, unknown = match(seen, catalog) if catalog else ([], [])
    if a.json:
        print(json.dumps({"seen": seen, "found": found, "unknown": unknown, "note": note}, indent=2))
    else:
        report(seen, found, unknown, note)
    if a.write:
        if not catalog:
            sys.exit("not writing data/body.yaml: " + note)
        write_body(seen, found, unknown)


if __name__ == "__main__":
    main()
