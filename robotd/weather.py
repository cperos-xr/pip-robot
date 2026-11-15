"""Weather for the RoboOS Weather app and the BLINKERS panel (Open-Meteo: free, no key, no account).

Location: config weather.lat / weather.lon, set by the owner, or (if unset) a coarse city-level guess from the robot's
public IP, made once and stored in the config (rounded to about 1 km). The place is PRIVATE: it is never drawn on the
screen (screenshots of RoboOS can end up in social posts) and never given to the brain or the social code.

The panel cycle shows the current temperature, today's high and low and the sky ("SUNNY", "RAIN", ...) on the
three 7-segment digits. With every wire on the PCF8574 some of them cannot be shown cleanly; those steps are skipped.
"""
import threading
import time

import requests

# WMO weather codes -> (text for the screen, word for the panel, icon)
WMO = {0: ("Clear", "SUNNY", "sun"), 1: ("Mostly clear", "SUNNY", "sun"), 2: ("Partly cloudy", "CLOUDS", "part"),
       3: ("Cloudy", "CLOUDY", "cloud"), 45: ("Fog", "FOG", "fog"), 48: ("Fog", "FOG", "fog"),
       51: ("Light drizzle", "DRIZZLE", "rain"), 53: ("Drizzle", "DRIZZLE", "rain"), 55: ("Heavy drizzle", "DRIZZLE", "rain"),
       56: ("Freezing drizzle", "ICE", "rain"), 57: ("Freezing drizzle", "ICE", "rain"),
       61: ("Light rain", "RAIN", "rain"), 63: ("Rain", "RAIN", "rain"), 65: ("Heavy rain", "RAIN", "rain"),
       66: ("Freezing rain", "ICE", "rain"), 67: ("Freezing rain", "ICE", "rain"),
       71: ("Light snow", "SNOW", "snow"), 73: ("Snow", "SNOW", "snow"), 75: ("Heavy snow", "SNOW", "snow"), 77: ("Snow grains", "SNOW", "snow"),
       80: ("Showers", "SHOWERS", "rain"), 81: ("Showers", "SHOWERS", "rain"), 82: ("Heavy showers", "SHOWERS", "rain"),
       85: ("Snow showers", "SNOW", "snow"), 86: ("Snow showers", "SNOW", "snow"),
       95: ("Thunderstorm", "STORM", "storm"), 96: ("Thunderstorm", "STORM", "storm"), 99: ("Thunderstorm", "STORM", "storm")}
FAHRENHEIT_COUNTRIES = {"US", "LR", "MM", "PR", "GU", "VI", "AS", "MP", "BS", "BZ", "KY", "PW", "FM", "MH"}
UA = {"User-Agent": "imalittlerobot-weather/1.0"}


def describe(code, is_day=True):
    text, word, icon = WMO.get(int(code or 0), ("Unknown", "", "cloud"))
    if int(code or 0) in (0, 1) and not is_day:
        return ("Clear" if code == 0 else "Mostly clear"), "CLEAR", "moon"
    return text, word, icon


def metno_code(symbol):
    """met.no symbol ('partlycloudy_night', 'heavyrainshowers_day', ...) -> (WMO weather code, is_day)."""
    s = str(symbol or "")
    is_day = not s.endswith("_night")
    s = s.split("_")[0]
    if "thunder" in s:
        return 95, is_day
    if "snow" in s or "sleet" in s:
        return (85 if "showers" in s else 71 if s.startswith("light") else 75 if s.startswith("heavy") else 73), is_day
    if "rainshowers" in s:
        return (82 if s.startswith("heavy") else 80), is_day
    if "rain" in s:
        return (61 if s.startswith("light") else 65 if s.startswith("heavy") else 63), is_day
    return {"clearsky": 0, "fair": 1, "partlycloudy": 2, "cloudy": 3, "fog": 45}.get(s, 3), is_day


class Weather:
    def __init__(self, robot):
        self.r = robot
        self.data = {"ok": False, "error": "not loaded yet"}
        self.t = 0.0
        self.lock = threading.Lock()
        self.fetching = False
        self.panel_ev = threading.Event()
        self.panel_thread = None
        self.panel_step = ""

    def cfg(self):
        c = self.r.cfg.data.setdefault("weather", {})
        c.setdefault("lat", None)
        c.setdefault("lon", None)
        c.setdefault("units", "auto")          # auto (from the country), fahrenheit or celsius
        c.setdefault("refresh_min", 15)
        c.setdefault("panel_step_s", 2.5)       # how long each panel step stays
        c.setdefault("panel_minutes", 10)       # the panel switches itself off (never left on unattended for long)
        return c

    # ------------------------------------------------------------------ data
    def locate(self):
        """Coarse location from the public IP (city level), once. Kept in the config, never shown or posted."""
        c = self.cfg()
        for url, la, lo, cc in (("https://ipapi.co/json/", "latitude", "longitude", "country_code"),
                                ("https://ipwho.is/", "latitude", "longitude", "country_code")):
            try:
                j = requests.get(url, timeout=8, headers=UA).json()
                if j.get(la) is not None and j.get(lo) is not None:
                    c["lat"], c["lon"] = round(float(j[la]), 2), round(float(j[lo]), 2)
                    c["country"] = str(j.get(cc) or "")[:2].upper()
                    try:
                        self.r.cfg.save()
                    except Exception:
                        pass
                    self.r.log("weather: location set from the internet address (city level, kept private)", persist=False)
                    return True
            except Exception:
                continue
        return False

    def units(self):
        u = self.cfg()["units"]
        if u in ("fahrenheit", "celsius"):
            return u
        return "fahrenheit" if self.cfg().get("country") in FAHRENHEIT_COUNTRIES else "celsius"

    def fetch(self):
        """Open-Meteo first; if it cannot be reached (it was unreachable from the home network on 2026-11-13), met.no."""
        c = self.cfg()
        if c.get("lat") is None and not self.locate():
            raise RuntimeError("no location: set weather.lat and weather.lon in the config")
        errors = []
        for name, fn in (("Open-Meteo", self._fetch_open_meteo), ("met.no", self._fetch_metno)):
            try:
                d = fn(c)
                d["source"] = name
                return d
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}")
        raise RuntimeError("no weather service reachable (" + ", ".join(errors) + ")")

    def _fetch_metno(self, c):
        """The Norwegian Meteorological Institute's free world-wide forecast (no key; wants a User-Agent). Celsius, m/s, UTC."""
        import calendar
        units = self.units()
        j = requests.get("https://api.met.no/weatherapi/locationforecast/2.0/compact", timeout=(6, 12), headers=UA,
                         params={"lat": c["lat"], "lon": c["lon"]}).json()
        ts = j["properties"]["timeseries"]
        f = units == "fahrenheit"
        temp = lambda v: round(v * 9 / 5 + 32) if f else round(v)

        def symbol(e):
            for k in ("next_1_hours", "next_6_hours", "next_12_hours"):
                s = ((e["data"].get(k) or {}).get("summary") or {}).get("symbol_code")
                if s:
                    return s
            return "cloudy"

        def local(e):
            return time.localtime(calendar.timegm(time.strptime(e["time"], "%Y-%m-%dT%H:%M:%SZ")))

        cur = ts[0]
        det = cur["data"]["instant"]["details"]
        code, is_day = metno_code(symbol(cur))
        text, word, icon = describe(code, is_day)
        today = time.localtime()[:3]
        temps = [e["data"]["instant"]["details"]["air_temperature"] for e in ts if local(e)[:3] == today] or [det["air_temperature"]]
        hours = []
        for e in ts[1:7]:
            hc, hd = metno_code(symbol(e))
            hr = local(e).tm_hour
            hours.append({"h": f"{hr % 12 or 12}{'am' if hr < 12 else 'pm'}", "temp": temp(e["data"]["instant"]["details"]["air_temperature"]),
                          "icon": describe(hc, hd)[2]})
        wind = det.get("wind_speed") or 0
        return {"ok": True, "temp": temp(det["air_temperature"]), "feels": temp(det["air_temperature"]),
                "hi": temp(max(temps + [det["air_temperature"]])), "lo": temp(min(temps + [det["air_temperature"]])),
                "rain_chance": None, "humidity": round(det["relative_humidity"]) if det.get("relative_humidity") is not None else None,
                "wind": round(wind * (2.237 if f else 3.6)), "wind_unit": "mph" if f else "km/h",
                "code": code, "is_day": bool(is_day), "text": text, "word": word, "icon": icon,
                "units": "F" if f else "C", "hours": hours, "updated": time.time()}

    def _fetch_open_meteo(self, c):
        units = self.units()
        j = requests.get("https://api.open-meteo.com/v1/forecast", timeout=(6, 12), headers=UA, params={
            "latitude": c["lat"], "longitude": c["lon"], "timezone": "auto", "forecast_days": 2,
            "temperature_unit": units, "wind_speed_unit": "mph" if units == "fahrenheit" else "kmh",
            "current": "temperature_2m,apparent_temperature,weather_code,is_day,relative_humidity_2m,wind_speed_10m",
            "hourly": "temperature_2m,weather_code,is_day",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"}).json()
        if "current" not in j:
            raise RuntimeError(str(j.get("reason") or "no data from Open-Meteo")[:120])
        cur, day = j["current"], j["daily"]
        text, word, icon = describe(cur["weather_code"], cur.get("is_day", 1))
        hours = []
        times = j["hourly"]["time"]
        start = next((i for i, t in enumerate(times) if t >= cur["time"][:13]), 0)
        for i in range(start + 1, min(len(times), start + 7)):
            hr = int(times[i][11:13])
            hours.append({"h": f"{hr % 12 or 12}{'am' if hr < 12 else 'pm'}", "temp": round(j["hourly"]["temperature_2m"][i]),
                          "icon": describe(j["hourly"]["weather_code"][i], j["hourly"]["is_day"][i])[2]})
        return {"ok": True, "temp": round(cur["temperature_2m"]), "feels": round(cur.get("apparent_temperature", cur["temperature_2m"])),
                "hi": round(day["temperature_2m_max"][0]), "lo": round(day["temperature_2m_min"][0]),
                "rain_chance": day.get("precipitation_probability_max", [None])[0], "humidity": cur.get("relative_humidity_2m"),
                "wind": round(cur.get("wind_speed_10m") or 0), "wind_unit": "mph" if units == "fahrenheit" else "km/h",
                "code": cur["weather_code"], "is_day": bool(cur.get("is_day", 1)), "text": text, "word": word, "icon": icon,
                "units": "F" if units == "fahrenheit" else "C", "hours": hours, "updated": time.time()}

    def refresh(self, wait=False):
        """Fetch new data in the background (or wait for it)."""
        def go():
            try:
                d = self.fetch()
            except Exception as e:
                d = {**self.data, "ok": bool(self.data.get("temp") is not None), "error": str(e)[:160]}
            with self.lock:
                self.data, self.t, self.fetching = d, time.time(), False
        with self.lock:
            if self.fetching:
                return
            self.fetching = True
        if wait:
            go()
        else:
            threading.Thread(target=go, daemon=True, name="weather").start()

    def get(self):
        """The latest data; starts a refresh when it is older than refresh_min."""
        if time.time() - self.t > 60 * float(self.cfg()["refresh_min"]):
            self.refresh()
        return self.data

    # ------------------------------------------------------------------ the BLINKERS panel
    def panel_steps(self, d=None):
        d = d or self.data
        if not d.get("ok"):
            return []
        u = "°"
        return [("now", "text", f"{d['temp']}{u}" if -9 <= d["temp"] <= 99 else str(d["temp"])),
                ("high", "text", f"H{d['hi']}" if -9 <= d["hi"] <= 99 else str(d["hi"])),
                ("low", "text", f"L{d['lo']}" if -9 <= d["lo"] <= 99 else str(d["lo"])),
                ("sky", "scroll", d.get("word") or "")]

    def panel_on(self):
        if self.panel_thread and self.panel_thread.is_alive():
            return {"ok": True, "msg": "already on"}
        if not self.data.get("ok"):
            self.refresh(wait=True)
        if not self.data.get("ok"):
            return {"ok": False, "msg": self.data.get("error", "no weather data")}
        self.panel_ev.clear()
        self.panel_thread = threading.Thread(target=self._panel_loop, daemon=True, name="weather-panel")
        self.panel_thread.start()
        return {"ok": True, "msg": "weather on the panel", "steps": [s[2] for s in self.panel_steps()]}

    def panel_off(self):
        self.panel_ev.set()
        self.panel_step = ""
        try:
            self.r.blinkers.off()
        except Exception:
            pass
        return {"ok": True, "msg": "panel off"}

    def panel_running(self):
        return bool(self.panel_thread and self.panel_thread.is_alive())

    def _panel_loop(self):
        b = self.r.blinkers
        end = time.time() + 60 * float(self.cfg()["panel_minutes"])
        step_s = float(self.cfg()["panel_step_s"])
        shown_any = False
        try:
            while not self.panel_ev.is_set() and time.time() < end:
                for name, kind, txt in self.panel_steps(self.get()):
                    if self.panel_ev.is_set():
                        break
                    if kind == "text":
                        res = b.show_text(txt, step_s + 1)
                        wait = step_s
                    else:
                        res = b.scroll(txt, 420, 0)
                        wait = float(res.get("secs", 0)) / 2 + 0.3 if res.get("ok") else 0
                    if res.get("ok"):
                        shown_any = True
                        self.panel_step = f"{name}: {res.get('shown_as', txt)}"
                        self.panel_ev.wait(wait)
                    else:
                        self.panel_step = f"{name}: '{txt}' cannot be shown cleanly"
                if not shown_any:
                    self.panel_step = "nothing in today's weather can be shown cleanly with this wiring"
                    break
        finally:
            try:
                b.off()
            except Exception:
                pass

    def status(self):
        d = dict(self.get())
        return {**d, "panel": self.panel_running(), "panel_step": self.panel_step, "units_setting": self.cfg()["units"],
                "located": self.cfg().get("lat") is not None}
