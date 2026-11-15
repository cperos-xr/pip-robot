"""RoboOS app: Weather (robotd/weather.py, Open-Meteo). Current temperature and sky, today's high and low, the next
hours, and a Panel button that cycles it on the BLINKERS LED display. The place is never shown (screenshots of the
screen can end up in social posts)."""
import time

from roboos.desktop import App, DARK, F_BOLD, F_UI, FACE, GREEN, LIGHT, NAVY, SHADOW, YELLOW, _font, bevel, button, fit_text, inside

F_HUGE = _font("DejaVuSans-Bold.ttf", 30)
SKY = (210, 230, 250)
CLOUD = (235, 235, 240)
CLOUD_DARK = (150, 155, 165)
RAIN = (40, 110, 230)


def wx_icon(d, kind, x, y, s=40, bg=FACE):
    """A weather picture in a box s x s with top-left (x, y): sun, moon, part, cloud, rain, snow, fog, storm."""
    k = s / 40.0
    P = lambda a, b: (x + a * k, y + b * k)
    E = lambda a, b, c, e: (x + a * k, y + b * k, x + c * k, y + e * k)

    def cloud(dark=False, dy=0):
        col = CLOUD_DARK if dark else CLOUD
        out = (110, 110, 120)
        for bx in (E(6, 16 + dy, 22, 30 + dy), E(14, 9 + dy, 32, 27 + dy), E(22, 15 + dy, 36, 30 + dy)):
            d.ellipse(bx, fill=col, outline=out)
        d.rectangle(E(11, 21 + dy, 31, 30 + dy), fill=col)
        d.line([P(10, 30 + dy), P(32, 30 + dy)], fill=out)
    if kind in ("sun", "part"):
        cx, cy, r = (20, 20, 8) if kind == "sun" else (14, 14, 6)
        for a in range(8):
            import math
            ang = a * math.pi / 4
            d.line([P(cx + math.cos(ang) * (r + 2), cy + math.sin(ang) * (r + 2)), P(cx + math.cos(ang) * (r + 6), cy + math.sin(ang) * (r + 6))],
                   fill=(230, 160, 0), width=max(1, int(2 * k)))
        d.ellipse(E(cx - r, cy - r, cx + r, cy + r), fill=YELLOW, outline=(220, 150, 0))
        if kind == "part":
            cloud(dy=6)
    elif kind == "moon":
        d.ellipse(E(10, 8, 32, 30), fill=(240, 230, 160))
        d.ellipse(E(17, 5, 37, 27), fill=bg)
    elif kind in ("cloud", "rain", "snow", "storm"):
        cloud(dark=kind != "cloud", dy=-4 if kind != "cloud" else 0)
        if kind == "rain":
            for a in (12, 19, 26):
                d.line([P(a, 30), P(a - 3, 37)], fill=RAIN, width=max(1, int(2 * k)))
        elif kind == "snow":
            for a, b in ((12, 31), (20, 35), (28, 31), (16, 38), (25, 38)):
                d.ellipse(E(a - 1.5, b - 1.5, a + 1.5, b + 1.5), fill=LIGHT, outline=(120, 140, 200))
        elif kind == "storm":
            d.polygon([P(20, 27), P(15, 35), P(19, 35), P(16, 40), P(25, 31), P(21, 31), P(24, 27)], fill=YELLOW, outline=(200, 140, 0))
    elif kind == "fog":
        for i, b in enumerate((12, 18, 24, 30)):
            d.line([P(6 + 3 * (i % 2), b), P(34 - 3 * (i % 2), b)], fill=CLOUD_DARK, width=max(1, int(3 * k)))
    else:
        cloud()


class WeatherApp(App):
    name, title, icon = "weather", "Weather", "weather"
    short = "Weather"

    def __init__(self, desk):
        super().__init__(desk)
        self.btns = {}
        self.note = ""
        self._seen = None
        self._poll = 0.0

    @property
    def w(self):
        return self.r.weather

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        data = self.w.data
        top = (x0 + 4, y0 + 3, x1 - 4, y0 + 78)
        bevel(d, top, False, SKY)
        if data.get("temp") is not None:
            u = data.get("units", "F")
            wx_icon(d, data.get("icon", "cloud"), top[0] + 6, top[1] + 8, 56, SKY)
            d.text((top[0] + 70, top[1] + 6), f"{data['temp']}°{u}", font=F_HUGE, fill=DARK)
            tx = top[0] + 172
            d.text((tx, top[1] + 6), fit_text(d, data.get("text", ""), top[2] - tx - 4, F_BOLD), font=F_BOLD, fill=NAVY)
            d.text((tx, top[1] + 21), f"High {data['hi']}°  Low {data['lo']}°", font=F_UI, fill=DARK)
            d.text((tx, top[1] + 34), f"Feels {data.get('feels', data['temp'])}°  {data.get('humidity', '?')}% hum.", font=F_UI, fill=DARK)
            rc = data.get("rain_chance")
            d.text((tx, top[1] + 47), f"Wind {data.get('wind', 0)} {data.get('wind_unit', '')}" + (f"  Rain {rc}%" if rc is not None else ""),
                   font=F_UI, fill=DARK)
            ago = int((time.time() - data.get("updated", time.time())) / 60)
            d.text((top[0] + 70, top[1] + 46), "now" if ago < 1 else f"{ago} min ago", font=F_UI, fill=SHADOW)
            # the next hours
            hours = data.get("hours", [])[:6]
            hy = y0 + 82
            bw = (x1 - x0 - 8) / max(1, len(hours) or 6)
            for i, h in enumerate(hours):
                hx = int(x0 + 4 + i * bw)
                cell = (hx, hy, int(hx + bw) - 2, hy + 54)
                bevel(d, cell, False, LIGHT)
                d.text((hx + 4, hy + 2), h["h"], font=F_UI, fill=SHADOW)
                wx_icon(d, h["icon"], hx + (bw - 26) / 2, hy + 13, 24, LIGHT)
                d.text((hx + 4, hy + 39), f"{h['temp']}°", font=F_BOLD, fill=DARK)
        else:
            d.text((top[0] + 10, top[1] + 10), "Loading the weather...", font=F_BOLD, fill=NAVY)
            if data.get("error"):
                d.text((top[0] + 10, top[1] + 28), fit_text(d, data["error"], top[2] - top[0] - 20), font=F_UI, fill=DARK)
        # buttons
        panel_on = self.w.panel_running()
        labels = ["Refresh", "Panel: on" if panel_on else "Panel: off", "°F / °C"]
        bw = (x1 - x0 - 8 - 2 * 4) / 3
        self.btns = {}
        for i, lab in enumerate(labels):
            bx0 = x0 + 4 + i * (bw + 4)
            bb = (int(bx0), y0 + 141, int(bx0 + bw), y0 + 161)
            self.btns[lab.split(":")[0]] = bb
            button(d, bb, lab, font=F_BOLD, color=GREEN if lab == "Panel: on" else DARK)
        line = self.note or (("Panel: " + self.w.panel_step) if panel_on and self.w.panel_step else
                             ("Tap Panel to show it on the BLINKERS display" if data.get("ok") else ""))
        d.text((x0 + 6, y0 + 165), fit_text(d, line, x1 - x0 - 12), font=F_UI, fill=DARK)

    def click(self, x, y):
        for lab, bx in self.btns.items():
            if inside(bx, x, y):
                self.note = ""
                if lab == "Refresh":
                    self.w.refresh()
                    self.note = "refreshing..."
                elif lab == "Panel":
                    if self.w.panel_running():
                        self.w.panel_off()
                    else:
                        import threading

                        def go():
                            res = self.w.panel_on()
                            self.note = "" if res.get("ok") else res.get("msg", "")
                            self.d.dirty = True
                        threading.Thread(target=go, daemon=True).start()
                elif lab.startswith("°F"):
                    c = self.w.cfg()
                    c["units"] = "celsius" if self.w.units() == "fahrenheit" else "fahrenheit"
                    try:
                        self.r.cfg.save()
                    except Exception:
                        pass
                    self.w.refresh()
                    self.note = f"switching to °{'C' if c['units'] == 'celsius' else 'F'}..."
                self.d.dirty = True
                return

    def tick(self):
        if time.time() - self._poll > 20:
            self._poll = time.time()
            self.w.get()                                     # refreshes in the background when the data is old
        data = self.w.data
        st = (data.get("updated"), data.get("error"), self.w.panel_running(), self.w.panel_step, self.note)
        if self.note == "refreshing..." and not self.w.fetching:
            self.note = ""
        if st != self._seen:
            self._seen = st
            return True
        return False
