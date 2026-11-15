"""Wi-Fi keeper: lets the owner's computers on the home Wi-Fi find the robot although broadcasts do not get through.

Found 2026-11-13: on some home Wi-Fi routers broadcast and multicast frames do not arrive in EITHER direction
between the robot and the owner's laptop (the robot's xradio chip also logs "[RX] Receive failure: 4" = it cannot
decrypt group frames), while ordinary unicast frames work both ways. ARP ("who has <the robot's address>?") starts
with a broadcast, so the laptop never learned the robot's hardware address: no ping, SSH or web app at home, although
the robot's internet and its public tunnel address were fine.

Fix without touching the router or the laptop: every few seconds, for each known peer (config wifi.peers):
  - tell the peer our address with an ARP request sent by unicast straight to its MAC. Linux learns the sender of an
    ARP request addressed to it and keeps that entry alive with unicast checks, which do get through;
  - put the peer in our own neighbour table (as STALE, so the kernel re-checks it by unicast) when it is missing.
The real fixes are on the router (reboot, Group Key Update Period 0, WPA2-AES only) or a USB Wi-Fi adapter.
Manual: knowledge/manual/troubleshooting.md.
"""
import fcntl
import socket
import struct
import subprocess
import threading
import time

IFACE = "wlan0"
SIOCGIFADDR = 0x8915


def _mac_bytes(s):
    b = bytes.fromhex(s.replace(":", "").replace("-", "").strip())
    if len(b) != 6:
        raise ValueError(f"bad MAC {s!r}")
    return b


def _my_ipv4(iface):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        return fcntl.ioctl(s.fileno(), SIOCGIFADDR, struct.pack("256s", iface.encode()[:15]))[20:24]
    finally:
        s.close()


def arp_request_frame(my_mac, my_ip, peer_mac, peer_ip):
    """Ethernet frame: unicast to peer_mac, 'who has peer_ip? tell my_ip (at my_mac)'."""
    return (peer_mac + my_mac + b"\x08\x06"
            + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
            + my_mac + my_ip + b"\x00" * 6 + peer_ip)


class WifiKeep:
    def __init__(self, robot):
        self.r = robot
        self.sent = 0
        self.last_sent = 0.0
        self.last_error = ""
        self._logged_errors = set()

    def cfg(self):
        c = {"keep": True, "keep_period_s": 20, "peers": []}
        c.update(self.r.cfg.get("wifi", {}) or {})
        return c

    def start(self):
        c = self.cfg()
        if c["keep"] and c["peers"]:
            names = ", ".join(f"{p.get('name', '?')} {p.get('ip', '?')}" for p in c["peers"])
            self.r.log(f"Wi-Fi keeper: telling {names} our address every {c['keep_period_s']} s (broadcasts do not get through)")
        threading.Thread(target=self._loop, daemon=True, name="wifi-keep").start()

    def _loop(self):
        time.sleep(3)
        while True:
            c = self.cfg()
            if c["keep"] and c["peers"]:
                try:
                    self.tick(c["peers"])
                    self.last_error = ""
                except Exception as e:           # no address yet, interface down, bad config: try again next round
                    self._error(f"{type(e).__name__}: {e}")
            time.sleep(max(5, float(c["keep_period_s"])))

    def _error(self, msg):
        self.last_error = msg
        if msg not in self._logged_errors and len(self._logged_errors) < 20:
            self._logged_errors.add(msg)
            self.r.log(f"Wi-Fi keeper: {msg}", "warn")

    def tick(self, peers):
        my_mac = _mac_bytes(open(f"/sys/class/net/{IFACE}/address").read())
        my_ip = _my_ipv4(IFACE)
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
        try:
            s.bind((IFACE, 0x0806))
            for p in peers:
                peer_mac, peer_ip = _mac_bytes(p["mac"]), socket.inet_aton(p["ip"])
                s.send(arp_request_frame(my_mac, my_ip, peer_mac, peer_ip))
                self._ensure_neigh(p["ip"], p["mac"])
        finally:
            s.close()
        self.sent += len(peers)
        self.last_sent = time.time()

    def _ensure_neigh(self, ip, mac):
        out = subprocess.run(["ip", "neigh", "show", ip, "dev", IFACE], capture_output=True, text=True, timeout=5).stdout
        if "lladdr" in out and "FAILED" not in out and "INCOMPLETE" not in out:
            return                               # the kernel already knows it (and re-checks it by unicast)
        subprocess.run(["ip", "neigh", "replace", ip, "lladdr", mac, "dev", IFACE, "nud", "stale"],
                       capture_output=True, timeout=5)

    def status(self):
        c = self.cfg()
        neigh = subprocess.run(["ip", "neigh", "show", "dev", IFACE], capture_output=True, text=True, timeout=5).stdout
        return {"keep": c["keep"], "period_s": c["keep_period_s"], "peers": c["peers"], "sent": self.sent,
                "last_sent_s_ago": round(time.time() - self.last_sent, 1) if self.last_sent else None,
                "last_error": self.last_error, "neigh": neigh.strip().splitlines()}
