# Weather (RoboOS app + BLINKERS panel)

`robotd/weather.py` (class `Weather`, `robot.weather`) and the RoboOS app **Weather** (`robotd/desktop_weather.py`,
desktop icon: a sun behind a cloud). Added 2026-11-12.

- Data: Open-Meteo (`api.open-meteo.com`), free, no key, no account. Current temperature, feels-like, humidity, wind, the
  sky (WMO code), today's high and low, the chance of rain and the next 6 hours. Refreshed when older than
  `weather.refresh_min` (15) while someone uses it.
- Location: config `weather.lat` and `weather.lon`. If they are empty, a coarse city-level guess from the robot's public
  internet address is made once (ipapi.co, then ipwho.is) and stored in the config (rounded to about 1 km).
  **The place is private**: it is never drawn on the screen (screenshots of RoboOS can end up in social posts), never
  returned by the API and never given to the brain or the social code.
- Units: `weather.units` = `auto` (Fahrenheit in the US and a few other countries, else Celsius), `fahrenheit` or
  `celsius`. The app's °F / °C button switches it.
- **Panel**: the app's Panel button cycles on the BLINKERS display: the current temperature ("82°"), the high ("H85"), the
  low ("L72") and the sky scrolling ("SUNNY", "CLOUDS", "FOG", "DRIZZLE", "RAIN", "ICE", "SNOW", "SHOWERS", "STORM",
  "CLEAR" at night), `weather.panel_step_s` (2.5 s) per step. Steps that the current wiring cannot show cleanly are
  skipped (see `blinkers.md`, Text). It switches itself off after `weather.panel_minutes` (10) so the display is never
  left on unattended for long. The Blinkers app's Off button stops it too.
- API (owner): `GET /api/weather`; `POST /api/weather {"cmd": "refresh" | "panel_on" | "panel_off"}`.

## Two sources (2026-11-13)

Open-Meteo first; when it cannot be reached (it timed out from the home network on 2026-11-13, which left the app
empty), `api.met.no` (free, no key) is used instead. `GET /api/weather` shows `source`. met.no has no rain chance, so
that line is left out.
