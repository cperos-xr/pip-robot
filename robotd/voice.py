"""Hearing and speaking.
- Ears: the webcam microphone (ALSA card "webcam") -> simple energy-based voice detection -> speech to text:
  config voice.stt = "cloud" (default when there is an OpenRouter key): the recorded clip goes to a cheap audio model
  on OpenRouter (voice.stt_model, empty = newest Gemini Flash Lite; about $0.0001 a sentence, 2026-11-08: the offline
  model rarely understood the owner). Audio leaves the robot only while it is listening after a Talk tap.
  "local" (or no internet): Vosk offline recognition (small English model, runs on the Pi). Vosk always runs
  alongside as the fallback.
- Mouth: espeak-ng text-to-speech -> one of (config voice.output):
    "pi"   the Orange Pi's own analog line out (13-pin header pins 7 + 8, "Mono Differential" so a small
           passive speaker can sit across both pins with no DC through it)
    "cyd"  streamed as 8 kHz 8-bit audio to the CYD's speaker output (the earpiece)
    "bluetooth" the paired Bluetooth speaker (USB dongle + bluez-alsa), the earpiece while it is not connected
    "auto" the Bluetooth speaker if connected, else a USB speaker, otherwise the CYD earpiece
  Bluetooth gets 48 kHz stereo only: mono made bluez-alsa switch the link to mono SBC, which the owner's speaker
  played at double speed (chipmunk voice, 2026-11-09). A keeper thread reconnects the speaker every minute while
  it is missing (config bluetooth.auto_connect, default on).
- One volume (voice.volume 0-10) and mute for everything, applied to whichever output is active (apply_audio()).
  The screen makes its own beeps and chimes (listening, questions, reminders): while another output is active the
  screen's speaker is set to 0 and the same sounds play on that output instead (on_cyd_sound, fed by CydLink.send).
- Keep speaker awake (voice.keep_awake off / 30m / 2h / always): a 2 s inaudible 20 Hz hum every 90 s after the last
  sound, so the Bluetooth speaker's own auto-off timer does not switch it off.
  When the Pi plays the sound itself, the CYD still gets a loudness envelope so the mouth moves in sync.
"""
import glob
import io
import json
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

from .core import ROOT

VOSK_MODEL = ROOT / "models" / "vosk-model-small-en-us-0.15"
MIC_DEVICE = "plughw:CARD=webcam,DEV=0"
RATE = 16000


PI_CARD = "Codec"          # the H2+ built-in analog codec (enabled by the analog-codec overlay)
OUTPUTS = ("pi", "cyd", "auto", "bluetooth")
DEFAULT_SPEAKER = {"name": "", "mac": ""}        # paired in RoboOS (Settings > Bluetooth); kept in config bluetooth.speaker
BT_RATE = 48000
_BT_PCM = re.compile(r"/org/bluealsa/hci\d+/dev_([0-9A-F_]{17})/a2dpsrc/sink")


_cards_cache = ([], 0.0)
KEEP_AWAKE = {"off": 0, "30m": 1800, "2h": 7200, "always": float("inf")}
CHIMES = {0: [(880, 70), (1175, 70), (1568, 110)], 1: [(1319, 90), (988, 120)], 2: [(392, 160), (330, 220)]}   # as the screen's


def _playback_cards():
    """ALSA card ids that can play sound, in card order (the webcam is capture-only so it is not listed). Cached 5 s."""
    global _cards_cache
    if time.time() - _cards_cache[1] < 5:
        return _cards_cache[0]
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        out = ""
    cards = []
    for cid in re.findall(r"card \d+: (\S+) \[", out):
        if cid not in cards:
            cards.append(cid)
    _cards_cache = (cards, time.time())
    return cards


_mic_cache = (MIC_DEVICE, 0.0)


def mic_device(cfg=None):
    """The ALSA device the ears record from. Order: config voice.mic (e.g. "plughw:CARD=Codec,DEV=0") if set; the
    first robot's webcam card "webcam" if present; else the first card that can record (any USB webcam or USB mic,
    whatever this robot was built from). Cached 30 s."""
    global _mic_cache
    if cfg is not None:
        want = ((cfg.get("voice") or {}).get("mic") or "").strip()
        if want:
            return want
    if time.time() - _mic_cache[1] < 30:
        return _mic_cache[0]
    dev = MIC_DEVICE
    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=3).stdout
        cards = re.findall(r"card (\d+): (\S+) \[", out)
        if cards and "webcam" not in [c[1] for c in cards]:
            # a USB card (webcam mic, USB mic) before a board's built-in codec, which usually has no microphone wired
            usb = [c for c in cards if glob.glob(f"/proc/asound/card{c[0]}/usbid")]
            dev = f"plughw:CARD={(usb or cards)[0][1]},DEV=0"
    except Exception:
        pass
    _mic_cache = (dev, time.time())
    return dev


def _tone_wav(notes, lead=0.0, amp=0.5, rate=BT_RATE):
    """[(hz, ms), ...] -> 48 kHz stereo WAV (soft edges so it does not click)."""
    parts = [np.zeros(int(rate * lead), np.float32)]
    for hz, ms in notes:
        t = np.arange(int(rate * ms / 1000)) / rate
        env = np.minimum(1, np.minimum(t, t[::-1]) / 0.006) if len(t) else t
        parts.append(np.sin(2 * np.pi * hz * t) * env * amp * 32000)
    y = np.concatenate(parts).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(np.repeat(y, 2).tobytes())
    return out.getvalue()


# The voices you can pick (Settings > Sound > Voice). All are free and run offline on espeak-ng.
# key: (espeak-ng voice, words per minute, pitch). "robot" is the original voice and the default.
TTS_VOICES = {
    "robot": ("en-us", 155, 55),
    "british": ("en-gb-x-rp", 150, 45),
    "female": ("en+f3", 150, 60),
}
TTS_LABELS = {"robot": "Robot (original)", "british": "British", "female": "Soft female"}

class Voice:
    def __init__(self, robot):
        self.r = robot
        self.model = None
        self.model_lock = threading.Lock()
        self.speak_lock = threading.Lock()
        self.listen_lock = threading.Lock()
        self.stop_listening = threading.Event()
        self.last_heard = ""
        self.stats = {"heard": 0, "spoken": 0, "cloud": 0, "cloud_fail": 0}
        self._stt_pick = (None, 0)
        self._bt = (None, 0.0)
        self.last_sound = time.time()
        self._applied = {}                   # what apply_audio() last pushed to each output
        self._keep_proc = None
        threading.Thread(target=self._bt_keeper, daemon=True, name="bt-keeper").start()
        threading.Thread(target=self._keep_awake_loop, daemon=True, name="bt-awake").start()

    # ------------------------------------------------------------------ Bluetooth speaker
    def bt_cfg(self):
        c = self.r.cfg.data.setdefault("bluetooth", {})
        c.setdefault("speaker", dict(DEFAULT_SPEAKER))
        c.setdefault("auto_connect", True)
        return c

    def bt_speaker(self, fresh=False):
        """MAC of the connected Bluetooth speaker (a bluez-alsa A2DP output), or None. Cached for 3 s."""
        mac, t = self._bt
        if fresh or time.time() - t > 3:
            mac = None
            if shutil.which("bluealsa-cli"):
                try:
                    out = subprocess.run(["bluealsa-cli", "list-pcms"], capture_output=True, text=True, timeout=3).stdout
                    m = _BT_PCM.search(out)
                    mac = m.group(1).replace("_", ":") if m else None
                except Exception:
                    pass
            self._bt = (mac, time.time())
        return mac

    def _bt_keeper(self):
        """Notice the speaker coming and going (every 10 s) and reconnect it when it is back (every 60 s)."""
        time.sleep(15)
        was, last_try = None, 0.0
        while True:
            try:
                bt = self.bt_cfg()
                mac = (bt.get("speaker") or {}).get("mac")
                now = self.bt_speaker(fresh=True)
                if now != was:
                    if was is not None or now:
                        self.r.log(f"Bluetooth speaker {'connected' if now else 'disconnected'}")
                    was = now
                    self.apply_audio()                   # route sounds (and the screen's beeper) to the right place
                    self.r.bus.emit("settings_changed")
                if (not now and mac and bt.get("auto_connect", True) and self.cfg()["output"] in ("auto", "bluetooth")
                        and time.time() - last_try > 60 and shutil.which("bluetoothctl") and glob.glob("/sys/class/bluetooth/hci*")):
                    last_try = time.time()
                    subprocess.run(["bluetoothctl", "--timeout", "10", "connect", mac], capture_output=True, timeout=25)
            except Exception:
                pass
            time.sleep(10)

    def bt_connect(self):
        """Connect the paired speaker now (settings button). -> message."""
        mac = (self.bt_cfg().get("speaker") or {}).get("mac")
        if not mac or not shutil.which("bluetoothctl"):
            return "no speaker paired yet"
        if self.bt_speaker(fresh=True):
            return "already connected"
        out = subprocess.run(["bluetoothctl", "--timeout", "12", "connect", mac], capture_output=True, text=True, timeout=25).stdout
        if self.bt_speaker(fresh=True):
            self.apply_audio()
            return "speaker connected"
        return "could not connect: is it switched on (and not playing for a phone)?" + (" (forgot the pairing: pair it again)" if "key-missing" in out else "")

    # ------------------------------------------------------------------ one volume, mute, routing
    def level(self):
        """The volume actually used, 0..10 (0 when muted)."""
        c = self.cfg()
        return 0 if c.get("mute") else max(0, min(10, int(c.get("volume", 8))))

    def apply_audio(self):
        """Push the volume / mute to every output. The screen's speaker gets 0 while another output is active, so the
        screen's own beeps (listening, questions...) stop coming out of the earpiece; those play on the active output."""
        v = self.level()
        name, dev = self.output_device()
        want = {"cyd": 0 if name != "cyd" or v == 0 else (1 if v <= 3 else 2 if v <= 6 else 3)}
        mac = self.bt_speaker()
        if mac:
            want["bt:" + mac] = round(127 * v / 10)
        if PI_CARD in _playback_cards():
            want["pi"] = round(31 * v / 10)
        for k, val in want.items():
            if self._applied.get(k) == val:
                continue
            try:
                if k == "cyd":
                    if not self.r.cyd.send({"t": "cfg", "vol": val}):
                        continue                     # screen not connected yet: try again next time
                elif k.startswith("bt:"):
                    path = f"/org/bluealsa/hci0/dev_{mac.replace(':', '_')}/a2dpsrc/sink"
                    subprocess.run(["bluealsa-cli", "volume", path, str(val)], capture_output=True, timeout=5)
                elif k == "pi":
                    subprocess.run(["amixer", "-q", "-c", PI_CARD, "sset", "Line Out", str(val), "unmute" if val else "mute"],
                                   capture_output=True, timeout=5)
                self._applied[k] = val
            except Exception:
                pass

    def on_cyd_sound(self, m):
        """The screen was told to make a sound (beep, chime, question, listening). It plays it itself only when it is
        the active output; otherwise its speaker is at 0 and the sound plays here, on the active output."""
        name, dev = self.output_device()
        if not dev or not self.level():
            return
        t = m.get("t")
        if t == "listen_state":
            if m.get("on"):
                return                               # the start beep plays before recording (listen())
            notes = [(900, 40)]
        elif m.get("chime") is not None or t == "ask":
            notes = CHIMES.get(int(m.get("chime", 1)), [(1000, 100)])
        elif t == "beep":
            notes = [(int(m.get("hz", 1000)), int(m.get("ms", 120)))]
        else:
            return
        threading.Thread(target=self.play_tones, args=(notes,), daemon=True).start()

    def play_tones(self, notes, wait=False):
        """Short tones on the active ALSA output (Bluetooth / Pi / USB)."""
        name, dev = self.output_device()
        if not dev or not self.level():
            return False
        lead = 0.15 if name == "bluetooth" else 0.0
        wav = _tone_wav(notes, lead=lead, amp=0.45)
        if not self.speak_lock.acquire(timeout=4):
            return False
        try:
            self._stop_keep()
            p = subprocess.run(["aplay", "-q", "-D", dev, "-"], input=wav, capture_output=True, timeout=10)
            self.last_sound = time.time()
            return p.returncode == 0
        finally:
            self.speak_lock.release()

    # ------------------------------------------------------------------ keep the Bluetooth speaker awake
    def _stop_keep(self):
        p, self._keep_proc = self._keep_proc, None
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass

    def _keep_awake_loop(self):
        hum = None
        last = 0.0
        while True:
            time.sleep(15)
            try:
                window = KEEP_AWAKE.get(self.cfg().get("keep_awake", "30m"), 0)
                idle = time.time() - self.last_sound
                if not window or idle > window or idle < 75 or time.time() - last < 90:
                    continue
                name, dev = self.output_device()
                if name != "bluetooth" or getattr(getattr(self.r, "desktop", None), "audio", None) and self.r.desktop.audio.playing:
                    continue
                if not self.speak_lock.acquire(blocking=False):
                    continue
                try:
                    if hum is None:                  # 2 s of a quiet 20 Hz hum: a small speaker cannot reproduce it
                        t = np.arange(2 * BT_RATE) / BT_RATE
                        y = (np.sin(2 * np.pi * 20 * t) * 120).astype(np.int16)
                        buf = io.BytesIO()
                        with wave.open(buf, "wb") as w:
                            w.setnchannels(2); w.setsampwidth(2); w.setframerate(BT_RATE)
                            w.writeframes(np.repeat(y, 2).tobytes())
                        hum = buf.getvalue()
                    self._keep_proc = subprocess.Popen(["aplay", "-q", "-D", dev, "-"], stdin=subprocess.PIPE,
                                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._keep_proc.stdin.write(hum)
                    self._keep_proc.stdin.close()
                    self._keep_proc.wait(5)
                    last = time.time()
                finally:
                    self._keep_proc = None
                    self.speak_lock.release()
            except Exception:
                pass

    @staticmethod
    def bt_wav(wav_bytes, lead=0.25):
        """Any WAV -> 48 kHz 16-bit stereo with a short silent lead-in (the speaker wakes its amplifier first)."""
        with wave.open(io.BytesIO(wav_bytes)) as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        x = np.frombuffer(raw, dtype=np.int16 if sw == 2 else np.uint8).astype(np.float32)
        if sw == 1:
            x = (x - 128) * 256
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        if sr != BT_RATE and len(x):
            x = np.interp(np.arange(len(x) * BT_RATE // sr) * (sr / BT_RATE), np.arange(len(x)), x)
        y = np.concatenate([np.zeros(int(BT_RATE * lead), np.float32), x])
        y = np.clip(y, -32768, 32767).astype(np.int16)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(BT_RATE)
            w.writeframes(np.repeat(y, 2).tobytes())
        return out.getvalue()

    # ------------------------------------------------------------------ mouth
    def cfg(self):
        c = self.r.cfg.data.setdefault("voice", {})
        c.setdefault("output", "auto")
        c.setdefault("pi_volume", 31)        # Line Out 0..31 (31 = 0 dB); unamplified speaker needs it all
        c.setdefault("stt", "cloud")
        c.setdefault("stt_model", "")
        c.setdefault("bt_boost", 3.0)        # loudness drive for the Bluetooth speaker (0 = off, 3 = loud, 5 = louder)
        c.setdefault("volume", 8)            # one volume for every output, 0..10 (apply_audio)
        c.setdefault("mute", False)
        c.setdefault("keep_awake", "30m")    # keep the Bluetooth speaker from switching itself off: off / 30m / 2h / always
        c.setdefault("tts", "robot")         # which espeak-ng voice: robot (original) / british / female
        return c

    # ------------------------------------------------------------------ cloud speech to text
    def stt_model(self):
        m = self.cfg().get("stt_model")
        if m:
            return m
        pick, ts = self._stt_pick
        if pick and time.time() - ts < 6 * 3600:
            return pick
        pick = "google/gemini-2.5-flash-lite"
        try:
            cands = [x for x in self.r.catalog.refresh() if x.get("audio") and not re.search(r":(batch|free)$|preview|exp", x["id"])]
            for rx in (r"^google/gemini-[\d.]+-flash-lite$", r"^google/gemini-[\d.]+-flash$"):
                hits = [x for x in cands if re.search(rx, x["id"])]
                if hits:
                    pick = max(hits, key=lambda x: x["created"])["id"]
                    break
        except Exception:
            pass
        self._stt_pick = (pick, time.time())
        return pick

    def transcribe_cloud(self, pcm, rate=RATE):
        """16-bit mono PCM -> text with an audio model on OpenRouter. Raises on failure (the caller falls back to Vosk)."""
        import base64
        import os
        import requests
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        model = self.stt_model()
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", timeout=25, headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "X-Title": "imalittlerobot ears"},
            json={"model": model, "max_tokens": 400, "temperature": 0, "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Transcribe the speech in this audio exactly, in the language spoken. Output only the spoken "
                                         "words, no quotes or notes. The speaker is talking to a small home robot; words like RoboOS, "
                                         "RoboDev, CYD, RP2040, Orange Pi, Bluesky, firmware may appear. If there is no clear speech, "
                                         "output nothing."},
                {"type": "input_audio", "input_audio": {"data": base64.b64encode(buf.getvalue()).decode(), "format": "wav"}}]}]})
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"].get("content") or "").strip().strip('"')
        if re.fullmatch(r"[\s.()\[\]]*(no (clear )?speech|silence|inaudible)?[\s.()\[\]]*", text, re.I):
            text = ""
        return text

    def output_device(self):
        """-> (name, ALSA device or None meaning the CYD earpiece)."""
        want = self.cfg()["output"]
        if want == "cyd":
            return "cyd", None
        if want in ("bluetooth", "auto"):
            mac = self.bt_speaker()
            if mac:
                return "bluetooth", f"bluealsa:DEV={mac},PROFILE=a2dp"
            if want == "bluetooth":
                return "cyd", None
        cards = _playback_cards()
        if want == "pi" and PI_CARD in cards:
            return "pi", f"plughw:CARD={PI_CARD},DEV=0"
        external = [c for c in cards if c != PI_CARD]
        if external:
            return f"external:{external[0]}", f"plughw:CARD={external[0]},DEV=0"
        return "cyd", None

    def set_output(self, output=None, pi_volume=None, pi_speaker=None):
        c = self.cfg()
        if output in OUTPUTS:
            c["output"] = output
        if pi_speaker is not None:
            c["pi_speaker"] = bool(pi_speaker)
        if pi_volume is not None:
            c["pi_volume"] = max(0, min(31, int(pi_volume)))
            self.apply_pi_volume()
        self.r.cfg.save()
        self.apply_audio()
        self.r.bus.emit("settings_changed")
        return self.status()

    def apply_pi_volume(self):
        if PI_CARD not in _playback_cards():
            return False
        v = str(self.cfg()["pi_volume"])
        try:
            subprocess.run(["amixer", "-q", "-c", PI_CARD, "sset", "Line Out", v, "unmute"], timeout=5, check=True)
            subprocess.run(["amixer", "-q", "-c", PI_CARD, "sset", "Line Out Source", "Mono Differential"], timeout=5)
            subprocess.run(["alsactl", "store"], timeout=5)
            return True
        except Exception as e:
            self.r.log(f"pi volume: {e}", "warn")
            return False

    def status(self):
        name, dev = self.output_device()
        return {**self.cfg(), "active": name, "device": dev, "cards": _playback_cards(), "options": list(OUTPUTS),
                "bt_speaker": self.bt_speaker(), "bt_name": (self.bt_cfg().get("speaker") or {}).get("name", "")}

    @staticmethod
    def envelope(wav_bytes, frame_ms=40):
        """Loudness per frame as characters '0'..'O' (0..31) for the CYD mouth when the Pi plays the audio."""
        with wave.open(io.BytesIO(wav_bytes)) as w:
            sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(n)
        x = np.frombuffer(raw, dtype=np.int16 if sw == 2 else np.uint8).astype(np.float32)
        if sw == 1:
            x = (x - 128) * 256
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        f = max(1, int(sr * frame_ms / 1000))
        k = len(x) // f
        if not k:
            return ""
        rms = np.sqrt((x[:k * f].reshape(k, f) ** 2).mean(axis=1))
        lv = np.clip(np.sqrt(rms / (rms.max() or 1.0)) * 31, 0, 31).astype(int)   # sqrt: quiet syllables still move
        lv[rms < (rms.max() or 1.0) * 0.04] = 0
        return "".join(chr(48 + int(v)) for v in lv[:2400])

    @staticmethod
    def louder(wav_bytes, drive=3.0, eq=True):
        """Normalise + soft-compress speech (tanh) so a small unamplified speaker is as loud as it can be.
        espeak speech averages about -22 dBFS; this brings it to about -12 dBFS without hard clipping."""
        with wave.open(io.BytesIO(wav_bytes)) as w:
            sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(n)
        if sw != 2 or not raw:
            return wav_bytes
        x = np.frombuffer(raw, np.int16).astype(np.float32)
        if eq and ch == 1 and len(x) > 1024:
            # small-speaker EQ: drop what a tiny speaker cannot reproduce (< ~300 Hz) so the limited line-out
            # power goes into audible frequencies, and lift the 1.5-4 kHz range that carries intelligibility
            nf = 1 << (len(x) - 1).bit_length()          # power-of-two FFT: ~5x faster on the H2+
            f = np.fft.rfftfreq(nf, 1 / sr)
            g = np.clip((f - 200) / 250, 0, 1) * (1 + 0.8 * np.exp(-((f - 2600) / 1200) ** 2))
            x = np.fft.irfft(np.fft.rfft(x, nf) * g, nf)[:len(x)].astype(np.float32)
        peak = float(np.abs(x).max()) or 1.0
        y = np.tanh(drive * x / peak) / np.tanh(drive) * 32000
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(ch); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(y.astype(np.int16).tobytes())
        return out.getvalue()

    def play(self, wav, device=None):
        """Play a WAV on an ALSA device with lip sync, or on the CYD earpiece if device is None."""
        self.apply_audio()                           # cheap when nothing changed
        self.last_sound = time.time()
        if not self.level():
            return True                              # muted: the words still show on the face
        self._stop_keep()
        if device:
            lips = {"t": "lips", "ms": 40, "lv": self.envelope(wav)}
            if PI_CARD in device and self.cfg().get("boost", True):
                wav = self.louder(wav)
            if device.startswith("bluealsa"):
                drive = float(self.cfg().get("bt_boost", 3.0) or 0)
                wav = self.bt_wav(self.louder(wav, drive=drive, eq=False) if drive > 0 else wav)
                # the mouth waits for the silent lead-in plus the Bluetooth delay
                threading.Timer(0.4, lambda: self.r.cyd.send(lips)).start()
            else:
                try:
                    self.r.cyd.send(lips)
                except Exception:
                    pass
            p = subprocess.run(["aplay", "-q", "-D", device, "-"], input=wav, capture_output=True, timeout=90)
            if p.returncode != 0:
                self.r.log(f"aplay {device} failed: {p.stderr.decode(errors='replace')[-200:]}", "warn")
            return p.returncode == 0
        return bool(self.r.cyd.send_pcm(self.wav_to_u8(wav, 8000), 8000))

    def tts_wav(self, text, speed=None, pitch=None):
        """Text to speech with the voice chosen in voice.tts (all free, offline espeak-ng variants)."""
        name, sp, pi = TTS_VOICES.get(self.cfg().get("tts"), TTS_VOICES["robot"])
        text = re.sub(r"[*_#`>\[\]]", "", text)[:600]  # strip markdown, keep it short
        p = subprocess.run(["espeak-ng", "-v", name, "-s", str(speed or sp), "-p", str(pitch or pi), "--stdout", text],
                           capture_output=True, timeout=30)
        return p.stdout

    @staticmethod
    def wav_to_u8(wav_bytes, out_rate=8000):
        with wave.open(io.BytesIO(wav_bytes)) as w:
            sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(n)
        x = np.frombuffer(raw, dtype=np.int16 if sw == 2 else np.uint8).astype(np.float32)
        if sw == 1:
            x = (x - 128) * 256
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        if len(x) == 0:
            return b""
        # simple low-pass (moving average) before decimation, then linear resample
        k = max(1, int(round(sr / out_rate / 2)))
        if k > 1:
            x = np.convolve(x, np.ones(k) / k, mode="same")
        t_new = np.arange(0, len(x) / sr, 1 / out_rate)
        y = np.interp(t_new, np.arange(len(x)) / sr, x)
        peak = np.max(np.abs(y)) or 1.0
        y = y / peak * 120.0                      # normalise to nearly full scale; the CYD applies volume
        return (np.clip(y + 128, 0, 255)).astype(np.uint8).tobytes()

    def speak(self, text, mood=None):
        """Say something out loud and show it on the face."""
        if not text:
            return
        sec = max(3, len(text) // 12)
        # A mood with no hold becomes the screen's lasting base mood (CYD firmware), so a spoken line's mood is
        # held only while the line is on screen; then the face goes back to its normal mood.
        self.r.face(mood, text, sec=sec, **({"hold": sec * 1000} if mood else {}))
        self.r.log(f"robot says: {text}", persist=False)
        self.r.store.event("said", text)
        wav = self.tts_wav(text)
        if not wav:
            return
        with self.speak_lock:
            self.play(wav, self.output_device()[1])
            self.stats["spoken"] += 1

    # ------------------------------------------------------------------ ears
    def _load_model(self):
        with self.model_lock:
            if self.model is None:
                from vosk import Model, SetLogLevel
                SetLogLevel(-1)
                t0 = time.time()
                self.model = Model(str(VOSK_MODEL))
                self.r.log(f"speech model loaded in {time.time() - t0:.1f}s")
        return self.model

    @property
    def mic_present(self):
        try:
            return "webcam" in subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=3).stdout
        except Exception:
            return False

    def listen(self, max_s=8.0, silence_s=1.1, wait_s=6.0, on_state=None):
        """Record until the speaker pauses; return the recognised text ('' if nothing understood)."""
        from vosk import KaldiRecognizer
        if not self.listen_lock.acquire(blocking=False):
            return ""
        proc = None
        try:
            if self.output_device()[1]:
                self.play_tones([(1400, 40)])        # the start beep on the speaker, before recording (not in it)
            rec = KaldiRecognizer(self._load_model(), RATE)
            self.stop_listening.clear()
            proc = subprocess.Popen(["arecord", "-q", "-D", mic_device(self.r.cfg), "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            chunk = RATE // 10 * 2          # 100 ms
            floor, started, last_voice, t0 = None, False, 0.0, time.time()
            levels, pcm = [], []
            if on_state:
                on_state(True)
            while not self.stop_listening.is_set():
                data = proc.stdout.read(chunk)
                if not data:
                    break
                x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
                levels.append(rms)
                now = time.time() - t0
                if now < 0.3:                      # learn the room noise first
                    floor = rms if floor is None else 0.7 * floor + 0.3 * rms
                    continue
                rec.AcceptWaveform(data)
                pcm.append(data)
                if rms > max(250.0, (floor or 100.0) * 2.8):
                    started, last_voice = True, now
                if started and now - last_voice > silence_s:
                    break
                if not started and now > wait_s:
                    break
                if now > max_s:
                    break
            if on_state:
                on_state(False)
            text = json.loads(rec.FinalResult()).get("text", "").strip()
            how = "offline"
            import os
            if started and self.cfg().get("stt") == "cloud" and os.environ.get("OPENROUTER_API_KEY"):
                try:
                    text = self.transcribe_cloud(b"".join(pcm))
                    how = "cloud"
                    self.stats["cloud"] += 1
                except Exception as e:
                    self.stats["cloud_fail"] += 1
                    self.r.log(f"cloud speech-to-text failed, using the offline result: {e}", "warn")
            self.last_heard = text
            if text:
                self.stats["heard"] += 1
                self.r.store.event("heard", text)
            self.r.log(f"heard ({how}): '{text}' (noise floor {floor or 0:.0f}, peak {max(levels or [0]):.0f})", persist=False)
            return text
        finally:
            if proc:
                proc.terminate()
                try:
                    proc.wait(2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if on_state:
                on_state(False)
            self.listen_lock.release()

    def transcribe_upload(self, data, mime=""):
        """Speech recorded on a phone or browser (web app hold-to-talk: webm/opus, mp4/aac, wav...) -> text.
        Cloud speech-to-text like TALK; Vosk when the cloud fails. The recording is not kept."""
        import os
        import tempfile
        ext = ".mp4" if "mp4" in mime or "aac" in mime or "m4a" in mime else ".ogg" if "ogg" in mime else ".wav" if "wav" in mime else ".webm"
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in" + ext)
            with open(src, "wb") as f:
                f.write(data)
            p = subprocess.run(["ffmpeg", "-v", "quiet", "-nostdin", "-i", src, "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                               capture_output=True, timeout=60)
        return self._pcm_text(p.stdout, "the web app")

    def transcribe_file(self, path, max_s=180):
        """The sound of a video or audio file -> text (for the privacy check of a video post). Raises if it cannot."""
        p = subprocess.run(["ffmpeg", "-v", "quiet", "-nostdin", "-i", str(path), "-t", str(max_s), "-vn", "-ac", "1",
                            "-ar", str(RATE), "-f", "s16le", "-"], capture_output=True, timeout=180)
        if p.returncode != 0:
            raise RuntimeError("ffmpeg could not read the sound")
        # cloud only: the offline model needs about 250 MB, and loading it next to everything else ran the Pi out of
        # memory (2026-11-14); without the cloud the caller posts the video without its sound
        return self._pcm_text(p.stdout, "a video", offline=False)

    def _pcm_text(self, pcm, where, offline=True):
        import os
        if len(pcm) < RATE // 2:                      # under 0.25 s
            return ""
        self.stats["heard"] += 1
        if self.cfg().get("stt", "cloud") == "cloud" and os.environ.get("OPENROUTER_API_KEY"):
            try:
                text = self.transcribe_cloud(pcm)
                self.stats["cloud"] += 1
                self.r.log(f"heard via {where} (cloud): {text!r}", persist=False)
                return text
            except Exception as e:
                self.stats["cloud_fail"] += 1
                if not offline:
                    raise
                self.r.log(f"cloud speech-to-text failed ({e}); using the offline model", "warn")
        if not offline:
            raise RuntimeError("cloud speech-to-text is not available")
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self._load_model(), RATE)
        for i in range(0, len(pcm), 8000):
            rec.AcceptWaveform(pcm[i:i + 8000])
        text = json.loads(rec.FinalResult()).get("text", "")
        self.r.log(f"heard via {where} (offline): {text!r}", persist=False)
        return text

    def transcribe_wav(self, wav_bytes, engine="vosk"):
        """Recognise speech in a WAV (used by the self-test: robot voice -> robot ears). engine: vosk | cloud."""
        from vosk import KaldiRecognizer
        with wave.open(io.BytesIO(wav_bytes)) as w:
            sr, raw = w.getframerate(), w.readframes(w.getnframes())
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        y = np.interp(np.arange(0, len(x) / sr, 1 / RATE), np.arange(len(x)) / sr, x).astype(np.int16).tobytes()
        if engine == "cloud":
            return self.transcribe_cloud(y)
        rec = KaldiRecognizer(self._load_model(), RATE)
        for i in range(0, len(y), 8000):
            rec.AcceptWaveform(y[i:i + 8000])
        return json.loads(rec.FinalResult()).get("text", "")

    def mic_level(self, seconds=1.0):
        """Open the microphone briefly and return (mean, peak) RMS. Audio is not stored or transcribed."""
        p = subprocess.run(["arecord", "-q", "-D", mic_device(self.r.cfg), "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw",
                            "-d", str(max(1, int(seconds)))], capture_output=True, timeout=10)
        x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)
        if len(x) < RATE // 4:
            raise RuntimeError("microphone returned no audio")
        blocks = x[: len(x) // 1600 * 1600].reshape(-1, 1600)
        rms = np.sqrt((blocks ** 2).mean(axis=1))
        return float(rms.mean()), float(rms.max())
