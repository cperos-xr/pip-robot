#!/usr/bin/env python3
"""Find the robot's brain computer from your PC: "I plugged it in, where is it?"

Run it ON YOUR PC (Windows, macOS or Linux; Python 3 standard library only):

  python tools/find_brain.py                 look for it by name, on a direct Ethernet cable, on a shared connection,
                                             and among the computers your PC has talked to recently
  python tools/find_brain.py --lan           also knock on every address of your home network (port 22 only)
  python tools/find_brain.py --host NAME     check one name or address
  python tools/find_brain.py --json          machine-readable (for an AI helper)

How a brain is usually reachable:
  - Same Wi-Fi as the PC (set up when the SD card was written): by name, e.g. pip.local or raspberrypi.local.
  - An Ethernet cable straight into the PC: by name over the cable (link-local), or, with internet sharing turned on
    for that cable (Windows "Internet Connection Sharing", macOS "Internet Sharing", Linux "Shared to other
    computers"), on the shared network: the brain then also gets internet through the PC.
  - A Raspberry Pi Zero (2) W on a USB data cable in "USB gadget" mode: shows up on the PC as a network adapter,
    reachable as raspberrypi.local (or the name you gave it).

It only opens TCP connections to port 22 (SSH: it reads the greeting line, which names the OS) and 8080 (robotd's web
app: if that answers, Pip is already alive there). It never logs in and never sends a password.
"""
import argparse
import concurrent.futures as cf
import ipaddress
import json
import platform
import re
import socket
import subprocess
import sys

NAMES = ["pip", "raspberrypi", "orangepizero", "orangepi", "armbian", "rockpi", "radxa", "debian", "ubuntu", "dietpi",
         "robot", "imalittlerobot"]
# The networks a PC hands out when it shares its connection over a cable, and the first Pip's fixed cable network.
SHARED = {
    "192.168.137.0/24": "Windows internet sharing",         # privacy-scan: ok (documented default, not a home address)
    "192.168.2.0/24": "macOS internet sharing",             # privacy-scan: ok
    "10.42.0.0/24": "Linux (NetworkManager) shared",        # privacy-scan: ok
    "192.168.77.0/24": "the first Pip's cable network",     # privacy-scan: ok
}
PI_MACS = ("b8:27:eb", "dc:a6:32", "e4:5f:01", "28:cd:c1", "d8:3a:dd", "2c:cf:67")   # Raspberry Pi makers' prefixes


def sh(cmd, timeout=6):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace").stdout
    except Exception:
        return ""


def my_networks():
    """(address, network) pairs of this PC's IPv4 interfaces."""
    out = []
    if platform.system() == "Windows":
        txt = sh(["ipconfig"])
        for ip, mask in re.findall(r"IPv4[^:\n]*:\s*([\d.]+)(?:\(\w+\))?\s*\n\s*Subnet Mask[^:\n]*:\s*([\d.]+)", txt):
            out.append((ip, mask))
    else:
        txt = sh(["ip", "-4", "-o", "addr"]) or sh(["ifconfig"])
        for ip, pre in re.findall(r"inet ([\d.]+)/(\d+)", txt):
            out.append((ip, pre))
        for ip, mask in re.findall(r"inet ([\d.]+) netmask (0x[0-9a-f]+|[\d.]+)", txt):
            if mask.startswith("0x"):
                mask = str(ipaddress.IPv4Address(int(mask, 16)))
            out.append((ip, mask))
    nets = []
    for ip, m in out:
        try:
            net = ipaddress.IPv4Network(f"{ip}/{m}", strict=False)
        except ValueError:
            continue
        if not net.is_loopback:
            nets.append((ip, net))
    return nets


def neighbours():
    """Addresses (and hardware addresses) the PC has talked to recently, from its ARP table."""
    txt = sh(["arp", "-a"])
    found = {}
    for ip, mac in re.findall(r"\(?(\d+\.\d+\.\d+\.\d+)\)?\s+(?:at\s+)?([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})", txt):
        mac = ":".join(x.zfill(2) for x in re.split(r"[:-]", mac.lower()))
        if not ip.endswith(".255") and not mac.startswith(("ff:ff", "01:00:5e", "33:33")):
            found[ip] = mac
    return found


def resolve(name):
    try:
        return sorted({a[4][0] for a in socket.getaddrinfo(name, 22, socket.AF_INET)})
    except OSError:
        return []


def knock(ip, port=22, timeout=0.6):
    """Port open? For SSH, also the greeting line ("SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3")."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            if port != 22:
                return True, ""
            s.settimeout(1.5)
            try:
                return True, s.recv(256).decode(errors="replace").strip()
            except OSError:
                return True, ""
    except OSError:
        return False, ""


def guess_os(banner):
    b = banner.lower()
    for key, name in (("raspbian", "Raspberry Pi OS"), ("ubuntu", "Ubuntu"), ("debian", "Debian / Armbian / Raspberry Pi OS"),
                      ("dropbear", "a small Linux (dropbear SSH)"), ("windows", "Windows (not a robot brain)")):
        if key in b:
            return name
    return "Linux?" if banner else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lan", action="store_true", help="also try every address on the PC's own networks (port 22)")
    ap.add_argument("--host", action="append", default=[], help="a name or address to check (repeatable)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cand = {}                                    # ip -> {"names": set, "why": set, "mac": str}

    def add(ip, why, name=None, mac=""):
        c = cand.setdefault(ip, {"names": set(), "why": set(), "mac": ""})
        c["why"].add(why)
        if name:
            c["names"].add(name)
        if mac:
            c["mac"] = mac

    names = a.host + [n + ".local" for n in NAMES] + ([] if a.host else NAMES)
    with cf.ThreadPoolExecutor(16) as ex:
        for name, ips in zip(names, ex.map(resolve, names)):
            for ip in ips:
                add(ip, "name", name)
    nets = my_networks()
    arp = neighbours()
    for ip, mac in arp.items():
        add(ip, "seen recently", mac=mac)
    to_scan = []
    for my_ip, net in nets:
        shared = next((label for cidr, label in SHARED.items() if ipaddress.IPv4Network(cidr).overlaps(net)), None)
        if (shared or a.lan) and net.num_addresses <= 1024:
            to_scan += [(str(h), shared or "home network") for h in net.hosts() if str(h) != my_ip]
    for ip, why in to_scan:
        cand.setdefault(ip, {"names": set(), "why": set(), "mac": ""})["why"].add(why)
    mine = {ip for ip, _ in nets}

    def check(ip):
        ssh, banner = knock(ip, 22)
        web = knock(ip, 8080)[0] if ssh or "name" in cand[ip]["why"] else False
        return ip, ssh, banner, web

    rows = []
    with cf.ThreadPoolExecutor(64) as ex:
        for ip, ssh, banner, web in ex.map(check, [ip for ip in cand if ip not in mine]):
            c = cand[ip]
            if not (ssh or web):
                continue
            pi = c["mac"].startswith(PI_MACS)
            rows.append({"address": ip, "names": sorted(c["names"]), "ssh": ssh, "os": guess_os(banner), "banner": banner,
                         "pip_alive": web, "raspberry_pi": pi, "how": sorted(c["why"])})
    score = lambda r: (not r["pip_alive"], not r["names"], not r["raspberry_pi"], "Windows" in r["os"], r["address"])
    rows.sort(key=score)

    if a.json:
        print(json.dumps({"found": rows, "pc_networks": [str(n) for _, n in nets]}, indent=2))
        return
    if not rows:
        print("No robot brain found.\n")
        print("Things to check:")
        print("  - Is it powered and booted? First boot can take 2-5 minutes (it resizes the SD card).")
        print("  - Same Wi-Fi as this PC? Only if Wi-Fi was set when the SD card was written (2.4 GHz for an Orange Pi).")
        print("  - Ethernet cable straight to this PC? Turn on internet sharing for that cable, wait a minute, run this again.")
        print("  - Try --lan to knock on every address of your home network, or look in your router's device list.")
        print("  - Your networks: " + (", ".join(str(n) for _, n in nets) or "none found"))
        sys.exit(1)
    print("Possible robot brains (best first):\n")
    for r in rows:
        tag = "PIP IS ALREADY ALIVE HERE (web app on :8080)" if r["pip_alive"] else ("SSH open" if r["ssh"] else "")
        print(f"  {r['address']:15}  {', '.join(r['names']) or '-':28}  {tag}")
        extra = [x for x in (r["os"], "Raspberry Pi (by its network chip)" if r["raspberry_pi"] else "", ", ".join(r["how"])) if x]
        print(f"  {'':15}  {' | '.join(extra)}")
    best = rows[0]
    who = next((n for n in best["names"] if n.endswith(".local")), best["names"][0] if best["names"] else best["address"])
    print(f"\nNext: ssh into it, e.g.  ssh root@{who}   (Armbian)   or   ssh <your-user>@{who}   (Raspberry Pi OS)")


if __name__ == "__main__":
    main()
