"""RoboOS Bluetooth app (robot only): pair and connect the speaker the robot's voice plays on."""
import re
import shutil
import subprocess
import threading
import time

from roboos.desktop import DARK, F_BOLD, F_UI, FACE, ListApp, NAVY, SHADOW, button, fit_text, inside


class Bluetooth(ListApp):
    """Bluetooth speaker setup. Needs a USB Bluetooth dongle on the Pi plus BlueZ (bluetoothctl).
    Until both are there it only explains what is missing. Pairing is done with bluetoothctl in the background."""
    name, title, icon = "bluetooth", "Bluetooth", "bluetooth"
    row_h = 22

    def __init__(self, desk):
        super().__init__(desk)
        self.devices, self.busy, self.note = [], "", ""
        self._check = 0
        self.refresh()

    def cfg(self):
        return self.r.voice.bt_cfg()

    def refresh(self):
        import glob
        self.tool = shutil.which("bluetoothctl")
        self.adapters = sorted(p.rsplit("/", 1)[-1] for p in glob.glob("/sys/class/bluetooth/hci*"))
        self._check = time.time()

    def ready(self):
        return bool(self.tool and self.adapters)

    def btctl(self, *args, timeout=20):
        try:
            return subprocess.run([self.tool, *args], capture_output=True, text=True, timeout=timeout).stdout
        except Exception as e:
            return str(e)

    def _bg(self, label, fn):
        if self.busy:
            return
        self.busy = label

        def go():
            try:
                self.note = fn() or ""
            except Exception as e:
                self.note = str(e)
            self.busy = ""
            self.d.dirty = True
        threading.Thread(target=go, daemon=True).start()

    def scan(self):
        self.btctl("--timeout", "12", "scan", "on", timeout=20)
        out = self.btctl("devices")
        self.devices = [(m.group(1), m.group(2)) for m in re.finditer(r"Device ([0-9A-F:]{17}) (.+)", out)]
        return f"found {len(self.devices)} device(s)"

    def _wait(self, mac, text, secs):
        end = time.time() + secs
        while time.time() < end:
            if text in self.btctl("info", mac, timeout=5):
                return True
            time.sleep(1)
        return False

    def connect(self, mac, name):
        if "Paired: yes" not in self.btctl("info", mac, timeout=5):
            # Pairing a speaker (learned 2026-11-09): the speaker must be found by a scan in the SAME bluetoothctl
            # session, an agent must answer, and it has to be connected right after pairing or the speaker drops the
            # key ("br-connection-key-missing"). So: one session that scans, trusts, pairs and connects at once.
            p = subprocess.Popen([self.tool], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
            say = lambda line: (p.stdin.write(line + "\n"), p.stdin.flush())
            try:
                say("agent NoInputNoOutput"); say("default-agent"); say("scan on")
                if not self._wait(mac, "Alias:", 20):        # ("Device <mac> not available" until it is seen)
                    return f"{name} not found: switch it on and put it in pairing mode, then try again"
                say(f"trust {mac}"); say(f"pair {mac}")
                if not self._wait(mac, "Paired: yes", 25):
                    return f"pairing with {name} failed: put it in pairing mode and try again"
                say(f"connect {mac}")
                self._wait(mac, "Connected: yes", 15)
                say("scan off"); say("quit")
            finally:
                try:
                    p.wait(5)
                except Exception:
                    p.kill()
        if "Connected: yes" not in self.btctl("info", mac, timeout=5):
            out = self.btctl("--timeout", "15", "connect", mac, timeout=25)
            if "Connected: yes" not in self.btctl("info", mac, timeout=5):
                return f"could not connect to {name}: {out.strip()[-80:]} (is it on, and not playing for a phone?)"
        self.cfg()["speaker"] = {"name": name, "mac": mac}
        self.r.cfg.save()
        self.r.voice.bt_speaker(fresh=True)
        if self.r.voice.cfg()["output"] == "cyd":
            self.r.voice.set_output("auto")          # use it
        threading.Thread(target=self.r.voice.speak, args=(f"Hello! I am talking through {name} now.", "happy"), daemon=True).start()
        return f"connected to {name}"

    def rows(self):
        return self.devices

    def tick(self):
        if time.time() - self._check > 5:
            before = (self.tool, tuple(self.adapters))
            self.refresh()
            return before != (self.tool, tuple(self.adapters))
        return bool(self.busy)

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        spk = self.cfg()["speaker"]
        on = bool(spk["mac"]) and self.r.voice.bt_speaker() == spk["mac"]
        lines = [("Adapter", ", ".join(self.adapters) or "none - plug the USB Bluetooth dongle into the hub"),
                 ("Software", "BlueZ ready" if self.tool else "BlueZ not installed (apt install bluez bluez-alsa-utils)"),
                 ("Speaker", f"{spk['name'] or 'none paired yet'} - " + ("CONNECTED" if on else "not connected"))]
        for i, (k, v) in enumerate(lines):
            d.text((x0 + 6, y0 + 4 + i * 15), k, font=F_BOLD, fill=DARK)
            d.text((x0 + 62, y0 + 4 + i * 15), fit_text(d, v, x1 - x0 - 68), font=F_UI, fill=DARK)
        self.scan_b = (x0 + 6, y0 + 50, x0 + 96, y0 + 70)
        self.conn_b = (x0 + 100, y0 + 50, x1 - 6, y0 + 70)
        en = self.ready() and not self.busy
        button(d, self.scan_b, "Scan" if not self.busy.startswith("scan") else "Scanning...", color=DARK if en else SHADOW)
        button(d, self.conn_b, fit_text(d, f"Connect {spk['name']}", self.conn_b[2] - self.conn_b[0] - 8), color=DARK if en else SHADOW)
        status = self.busy or self.note or (("The robot's voice and sounds play on the speaker." if on else
                                              "Switch the speaker on: it reconnects within a minute, or tap Connect. New speaker: Scan, then tap it.")
                                             if self.ready() else "Plug the USB Bluetooth dongle into the hub. The voice uses the screen earpiece meanwhile.")
        for i, line in enumerate(re.findall(r".{1,52}(?:\s|$)", status)[:2]):
            d.text((x0 + 6, y0 + 74 + i * 13), line.strip(), font=F_UI, fill=NAVY)
        self.box = (x0, y0 + 102, x1, y1)
        if self.devices:
            self.draw_list(d, self.box)

    def draw_row(self, d, box, row, i):
        mac, name = row
        d.text((box[0] + 6, box[1] + 5), fit_text(d, name, 170), font=F_BOLD, fill=DARK)
        d.text((box[0] + 182, box[1] + 5), mac, font=F_UI, fill=SHADOW)

    def click(self, x, y):
        if not self.ready():
            self.refresh()
            self.d.toast("no Bluetooth adapter yet" if not self.adapters else "BlueZ is not installed yet")
            return
        if inside(self.scan_b, x, y):
            self._bg("scanning (12 s)...", self.scan)
        elif inside(self.conn_b, x, y):
            spk = self.cfg()["speaker"]
            self._bg(f"connecting {spk['name']}...", lambda: self.connect(spk["mac"], spk["name"]))
        elif self.devices and y >= self.box[1]:
            i = self.list_click(x, y)
            if i is not None and i < len(self.devices):
                mac, name = self.devices[i]
                self._bg(f"connecting {name}...", lambda: self.connect(mac, name))
