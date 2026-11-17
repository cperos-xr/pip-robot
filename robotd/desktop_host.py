"""RobotHost: RoboOS (roboos/) running as the robot's desktop. The robot's voice, camera, AI brain, event log and
Bluesky clips behind the RoboOS Host API, plus the robot-only apps. RoboOS never imports robotd; robotd gives it this."""
import io
import subprocess
import threading
import time

from roboos.host import Host


class RobotHost(Host):
    program = "robotd"
    machine = "the robot"

    def __init__(self, robot):
        super().__init__(robot.cyd, robot.cfg, robot.log, robot)
        self.t0 = getattr(robot, "t0", self.t0)

    def has(self, feature):
        attr = {"screen": "cyd", "sysinfo": "sys", "voice": "voice", "ai": "brain", "camera": "cam",
                "social": "social"}.get(feature)
        return bool(attr and getattr(self.robot, attr, None) is not None)

    def apps(self, builtin):
        """RoboDev first (as before), the robot apps just before Logs, so the Start menu keeps its order."""
        from .desktop_battery import BatteryApp
        from .desktop_blinkers import BlinkersApp
        from .desktop_bluetooth import Bluetooth
        from .desktop_dev import RoboDev
        from .desktop_robot import CameraApp, SettingsApp
        from .desktop_scripts import ScriptsApp
        from .desktop_weather import WeatherApp
        out = [RoboDev] + list(builtin)
        i = next((k for k, a in enumerate(out) if a.name == "logs"), len(out))
        out[i:i] = [BlinkersApp, WeatherApp, BatteryApp, CameraApp, SettingsApp, Bluetooth, ScriptsApp]
        return out

    def recent_log(self, n=120):
        return self.robot.store.recent_events(n)

    def say(self, text, mood=None):
        if text and self.has("voice"):
            threading.Thread(target=self.robot.voice.speak, args=(text, mood), daemon=True).start()

    # ---- sound: the voice output decides (Bluetooth speaker, Pi speaker or the screen earpiece)
    def audio_begin(self):
        v = self.robot.voice
        v.apply_audio()
        v._stop_keep()                       # the keep-awake hum gives way
        return v.output_device()[1]

    def audio_end(self):
        self.robot.voice.last_sound = time.time()

    def wav_to_u8(self, data, rate):
        return self.robot.voice.wav_to_u8(data, rate)

    def bt_wav(self, data):
        return self.robot.voice.bt_wav(data, lead=0)

    def mic_device(self):
        from . import voice
        if hasattr(voice, "mic_device"):     # finds the mic on any robot (config voice.mic, the webcam, a USB mic)
            return voice.mic_device(self.robot.cfg)
        return voice.MIC_DEVICE

    # ---- camera
    def camera_privacy(self):
        return bool(self.robot.cam.privacy)

    def set_camera_privacy(self, on):
        self.robot.cam.set_privacy(on)
        if on:
            vc = self.robot.vision.cfg()
            vc["follow"] = vc["gaze_mirror"] = False
            self.save()
        self.log(f"camera privacy {'ON - camera off' if on else 'off - camera allowed'} (desktop)")

    # ---- a game moment for Bluesky (the social scheduler decides whether it is posted)
    def post_moment(self, app_name, note, frames, score=2):
        from PIL import Image
        from .screencap import SHOTS, encode_mp4
        SHOTS.mkdir(parents=True, exist_ok=True)
        out = encode_mp4(frames, SHOTS / time.strftime(f"{app_name}_%Y%m%d_%H%M%S.mp4"))
        last = Image.open(io.BytesIO(frames[-1][1]))
        self.robot.social.moment("game", note, image=last, video=str(out) if out else None, score=score,
                                 key=f"game:{app_name}")

    # ---- AI
    def ai_model(self):
        if not self.has("ai"):
            return None, None
        _, active = self.robot.brain.active()
        return active.get("provider"), active.get("model")

    # ---- the robot manual (shared pages + this robot's own notes)
    def manual_pages(self):
        from .brain import manual_pages
        return manual_pages()

    def read_manual(self, page):
        from .brain import read_manual
        return read_manual(page)

    def bookmarks(self):
        return [("My public page", "http://localhost:8080/public")]

    # ---- power
    def power(self, action):
        if action == "restart":
            subprocess.Popen(["systemctl", "restart", "robotd"])
        else:
            self.robot.on_power({"action": action})

    # ---- system information (robotd's own SysInfo)
    def cpu_pct(self):
        return self.robot.sys.cpu_pct()

    def mem(self):
        return self.robot.sys.mem()

    def temp(self):
        return self.robot.sys.temp()

    def ips(self):
        return self.robot.sys.ips()
