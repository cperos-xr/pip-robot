"""Wi-Fi networks: see where the robot is connected, scan, add a network (home, a phone hotspot), connect now,
forget one. Web app: Settings > Wi-Fi (owner only). Manual: knowledge/manual/software.md (Network).

Works with wpa_supplicant (wpa_cli, the robot's Armbian setup) or NetworkManager (nmcli, e.g. Raspberry Pi OS).
Passwords are never logged, returned or stored in clear: wpa_supplicant gets the 64-hex key made from them.
Changes keep the current connection (adding a network does not switch to it); "connect now" switches and falls
back to the others by itself if the new one does not come up. The network in use cannot be forgotten.

Phone hotspots: an iPhone names its hotspot with a curly apostrophe (Name’s iPhone, U+2019) that nobody types,
so a name with an apostrophe is saved in both spellings. An iPhone hotspot is 5 GHz unless "Maximize
Compatibility" is on (Settings > Personal Hotspot); 2.4 GHz-only Wi-Fi chips need it on."""
import fcntl
import hashlib
import re
import shutil
import socket
import struct
import subprocess
import threading
import time

IFACE = "wlan0"
APOS = ("'", "’")


def _ipv4(iface=IFACE):
    """The interface's IPv4 address without starting a process ('' = none)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", iface.encode()[:15]))[20:24])
    except OSError:
        return ""
    finally:
        s.close()


def _unescape(s):
    """wpa_cli prints non-ASCII SSID bytes as \\xNN."""
    if "\\" not in s:
        return s
    b = re.sub(rb"\\x([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), s.encode("latin-1", "replace"))
    return b.replace(b'\\"', b'"').replace(b"\\\\", b"\\").decode("utf-8", "replace")


def spellings(ssid):
    if any(a in ssid for a in APOS):
        return list(dict.fromkeys([ssid] + [re.sub("['’]", a, ssid) for a in APOS]))
    return [ssid]


class Wifi:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.Lock()
        self.wpa = bool(shutil.which("wpa_cli"))
        self.nm = not self.wpa and bool(shutil.which("nmcli"))
        self.note = ""                                # result of the last background "connect now"
        self._seen = (None, None)
        threading.Thread(target=self._watch, daemon=True, name="wifi-watch").start()

    # ------------------------------------------------------------------ helpers
    def _run(self, *args, timeout=10):
        try:
            return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout).stdout.strip()
        except Exception as e:
            return f"FAIL {e}"

    def _wpa(self, *args, timeout=10):
        return self._run("wpa_cli", "-i", IFACE, *[str(a) for a in args], timeout=timeout)

    def _ok(self, *args):
        out = self._wpa(*args)
        if out != "OK":
            raise RuntimeError(f"wpa_cli {args[0]}: {out[:80]}")

    def _save(self):
        self._ok("save_config")

    # ------------------------------------------------------------------ status
    def status(self):
        out = {"backend": "wpa_supplicant" if self.wpa else "NetworkManager" if self.nm else "", "ssid": "",
               "ip": _ipv4(), "signal": None, "freq": None, "networks": [], "note": self.note}
        if self.wpa:
            st = dict(l.split("=", 1) for l in self._wpa("status").splitlines() if "=" in l)
            out["ssid"] = _unescape(st.get("ssid", "")) if st.get("wpa_state") == "COMPLETED" else ""
            out["freq"] = int(st["freq"]) if st.get("freq", "").isdigit() else None
            sig = dict(l.split("=", 1) for l in self._wpa("signal_poll").splitlines() if "=" in l)
            out["signal"] = int(sig["RSSI"]) if sig.get("RSSI", "").lstrip("-").isdigit() else None
            for line in self._wpa("list_networks").splitlines()[1:]:
                p = line.split("\t")
                if len(p) >= 2:
                    flags = p[3] if len(p) > 3 else ""
                    out["networks"].append({"id": p[0], "ssid": _unescape(p[1]), "current": "CURRENT" in flags,
                                            "disabled": "DISABLED" in flags})
        elif self.nm:
            for line in self._run("nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show").splitlines():
                name, _, rest = line.partition(":")
                typ, _, dev = rest.partition(":")
                if "wireless" in typ:
                    out["networks"].append({"id": name, "ssid": name, "current": dev == IFACE, "disabled": False})
                    if dev == IFACE:
                        out["ssid"] = name
        return out

    def scan(self):
        """Networks in range, strongest first: [{ssid, signal, freq, secure}]."""
        seen = {}
        if self.wpa:
            self._wpa("scan")
            time.sleep(4)
            for line in self._wpa("scan_results").splitlines()[1:]:
                p = line.split("\t")
                if len(p) >= 5 and p[4]:
                    ssid = _unescape(p[4])
                    item = {"ssid": ssid, "signal": int(p[2]), "freq": int(p[1]), "secure": "WPA" in p[3] or "WEP" in p[3]}
                    if ssid not in seen or item["signal"] > seen[ssid]["signal"]:
                        seen[ssid] = item
        elif self.nm:
            for line in self._run("nmcli", "-t", "-f", "SSID,SIGNAL,FREQ,SECURITY", "dev", "wifi", "list", "--rescan", "yes",
                                  timeout=20).splitlines():
                p = re.split(r"(?<!\\):", line)
                if len(p) >= 4 and p[0]:
                    sig = int(p[1]) // 2 - 100 if p[1].isdigit() else -90         # nmcli: 0-100 % -> about dBm
                    item = {"ssid": p[0].replace("\\:", ":"), "signal": sig, "freq": int(re.sub(r"\D", "", p[2]) or 0),
                            "secure": bool(p[3].strip())}
                    if item["ssid"] not in seen or sig > seen[item["ssid"]]["signal"]:
                        seen[item["ssid"]] = item
        return sorted(seen.values(), key=lambda x: -x["signal"])

    # ------------------------------------------------------------------ changes
    def add(self, ssid, password="", prefer=False):
        """Save a network (or change a saved one's password). Does not switch to it."""
        ssid = str(ssid or "").strip()
        password = str(password or "")
        if not 0 < len(ssid.encode()) <= 32:
            raise ValueError("the network name is 1-32 characters")
        if password and not 8 <= len(password) <= 63:
            raise ValueError("a Wi-Fi password is 8-63 characters (leave it empty for an open network)")
        names = spellings(ssid)
        with self.lock:
            if self.nm:
                for n in names[:1]:
                    self._run("nmcli", "connection", "delete", n)
                    args = ["nmcli", "connection", "add", "type", "wifi", "ifname", IFACE, "con-name", n, "ssid", n,
                            "connection.autoconnect-priority", "10" if prefer else "0"]
                    if password:
                        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
                    out = self._run(*args)
                    if "successfully" not in out:
                        raise RuntimeError("nmcli: " + out[:80])
            elif self.wpa:
                saved = {n["ssid"]: n["id"] for n in self.status()["networks"]}
                for n in names:
                    nid = saved.get(n) or self._wpa("add_network")
                    if not nid.isdigit():
                        raise RuntimeError("wpa_cli add_network: " + nid[:80])
                    self._ok("set_network", nid, "ssid", n.encode().hex())
                    if password:
                        key = hashlib.pbkdf2_hmac("sha1", password.encode(), n.encode(), 4096, 32).hex()
                        self._ok("set_network", nid, "key_mgmt", "WPA-PSK")
                        self._ok("set_network", nid, "psk", key)
                    else:
                        self._ok("set_network", nid, "key_mgmt", "NONE")
                    self._ok("set_network", nid, "scan_ssid", "1")
                    self._ok("set_network", nid, "priority", "10" if prefer else "0")
                    self._ok("enable_network", nid)
                self._save()
            else:
                raise RuntimeError("no Wi-Fi tool found (wpa_cli or nmcli)")
        both = " (saved in both apostrophe spellings)" if len(names) > 1 else ""
        self.r.log(f"Wi-Fi network saved by the owner: {ssid}{both}")
        return {"ok": True, "msg": f"saved {ssid}{both}. Pip joins it when it is in range and the current network is not."}

    def forget(self, nid):
        with self.lock:
            nets = self.status()["networks"]
            net = next((n for n in nets if n["id"] == str(nid)), None)
            if not net:
                raise ValueError("no such saved network")
            if net["current"]:
                raise ValueError("Pip is connected through this network: connect to another one first")
            if self.nm:
                self._run("nmcli", "connection", "delete", net["id"])
            else:
                self._ok("remove_network", net["id"])
                self._save()
        self.r.log(f"Wi-Fi network forgotten by the owner: {net['ssid']}")
        return {"ok": True, "msg": f"forgot {net['ssid']}"}

    def connect(self, nid):
        """Switch to a saved network now (in the background). If it is not up within 40 s, every saved network is
        allowed again and Pip goes back to whatever is in range. Nothing is saved while switching."""
        net = next((n for n in self.status()["networks"] if n["id"] == str(nid)), None)
        if not net:
            raise ValueError("no such saved network")

        def go():
            with self.lock:
                self.note = f"switching to {net['ssid']}..."
                if self.nm:
                    out = self._run("nmcli", "connection", "up", net["id"], timeout=45)
                    self.note = f"connected to {net['ssid']}" if "successfully" in out else f"could not join {net['ssid']}"
                    return
                self._wpa("select_network", net["id"])            # (disables the others until enable_network all)
                end, ok = time.time() + 40, False
                while time.time() < end:
                    time.sleep(2)
                    st = self.status()
                    if st["ssid"] == net["ssid"] and st["ip"]:
                        ok = True
                        break
                self._wpa("enable_network", "all")
                if not ok:
                    self._wpa("reassociate")
                self.note = f"connected to {net['ssid']} at {_ipv4()}" if ok else f"could not join {net['ssid']}: back on the others"
                self.r.log("Wi-Fi: " + self.note)
        threading.Thread(target=go, daemon=True, name="wifi-connect").start()
        return {"ok": True, "msg": f"switching to {net['ssid']}: this page may lose Pip for a moment"}

    # ------------------------------------------------------------------ new network -> say where Pip is
    def _watch(self):
        """When the address changes (another network, e.g. a phone hotspot at an event), show it on the face, so
        the owner can open the web app at http://<address>:8080. Reads the address without starting a process."""
        time.sleep(20)
        while True:
            ip = _ipv4()
            if ip and ip != self._seen[1]:
                first = self._seen[1] is None
                ssid = self.status()["ssid"] if (self.wpa or self.nm) else ""
                self._seen = (ssid, ip)
                if not first:
                    port = self.r.cfg["api"]["port"]
                    self.r.toast(f"Wi-Fi {ssid}: {ip}:{port}", 8000)
                    self.r.log(f"Wi-Fi: now on {ssid or 'a network'} at {ip}")
            time.sleep(10)
