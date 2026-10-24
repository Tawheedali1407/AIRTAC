"""Weather (Ali, 18 Oct: "no weather forecast" in the morning greeting).

Open-Meteo's free forecast API (no key; non-commercial use; data CC BY 4.0, credited on the page):
- now: temperature, feels like, humidity, wind, sky;
- today and tomorrow: high, low, chance of rain, sky, sunrise and sunset.
The town defaults to Nandyal (RGM college); change it in Settings > Weather or by asking "set my weather town to ...".
Fetched in the background every 30 minutes and kept, so the greeting never waits on the network. Offline or failed:
the greeting simply leaves the weather out (it never guesses).
"""
import json
import time
from datetime import date, datetime

from airtac import config, memory
from airtac.plugin_api import Tool, ToolResult, fetch, plugin, schema

p = plugin(__name__)

FORECAST = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_PLACE = {"name": "Nandyal", "region": "Andhra Pradesh", "lat": 15.47799, "lon": 78.4836, "tz": "Asia/Kolkata"}
REFRESH_S = 30 * 60
CREDIT = "Weather data by Open-Meteo.com (CC BY 4.0)"

# WMO weather interpretation codes, as Open-Meteo returns them.
WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with heavy hail"}


def sky(code) -> str:
    try:
        return WMO.get(int(code), "")
    except (TypeError, ValueError):
        return ""


def place() -> dict:
    try:
        saved = json.loads(memory.kv_get("weather", "place") or "null")
    except ValueError:
        saved = None
    return saved or dict(DEFAULT_PLACE)


def set_place(name: str) -> dict:
    """Finds a town by name (India first) and keeps it. Raises ValueError with a sentence when it can't."""
    name = " ".join((name or "").split())[:80]
    if len(name) < 2:
        raise ValueError("Type a town name.")
    data = fetch(GEOCODE, params={"name": name, "count": 5, "language": "en", "format": "json"}, timeout=8)
    if data is None:
        raise ValueError("Couldn't reach the town search. Check the internet and try again.")
    found = data.get("results") or []
    if not found:
        raise ValueError(f"No town called {name} was found.")
    best = next((r for r in found if r.get("country_code") == "IN"), found[0])
    chosen = {"name": best.get("name", name), "region": best.get("admin1", ""), "lat": float(best["latitude"]),
              "lon": float(best["longitude"]), "tz": best.get("timezone") or "Asia/Kolkata"}
    memory.kv_set("weather", "place", json.dumps(chosen))
    memory.kv_set("weather", "forecast", "")
    return chosen


def parse(data: dict, where: dict, now: float | None = None) -> dict | None:
    """Open-Meteo's answer -> what AIRTAC shows and says. None when it isn't a forecast."""
    cur, daily = data.get("current") or {}, data.get("daily") or {}
    if "temperature_2m" not in cur or not daily.get("time"):
        return None

    def day(i: int) -> dict | None:
        if i >= len(daily["time"]):
            return None
        pick = lambda k: (daily.get(k) or [None] * (i + 1))[i]  # noqa: E731
        return {"date": daily["time"][i], "high": pick("temperature_2m_max"), "low": pick("temperature_2m_min"),
                "rain_chance": pick("precipitation_probability_max"), "sky": sky(pick("weather_code")),
                "sunrise": (pick("sunrise") or "")[-5:], "sunset": (pick("sunset") or "")[-5:]}
    return {"place": where["name"], "region": where.get("region", ""), "at": cur.get("time", ""),
            "fetched": now or time.time(),
            "now": {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
                    "humidity": cur.get("relative_humidity_2m"), "wind": cur.get("wind_speed_10m"),
                    "sky": sky(cur.get("weather_code"))},
            "today": day(0), "tomorrow": day(1), "credit": CREDIT}


def refresh() -> dict | None:
    where = place()
    data = fetch(FORECAST, timeout=8, params={
        "latitude": where["lat"], "longitude": where["lon"], "timezone": where.get("tz") or "Asia/Kolkata", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"})
    w = parse(data, where) if isinstance(data, dict) else None
    if w:
        memory.kv_set("weather", "forecast", json.dumps(w))
    return w


def current(max_age: float = REFRESH_S * 2) -> dict | None:
    """The kept forecast if it's fresh enough, else None (the greeting never waits for the network)."""
    try:
        w = json.loads(memory.kv_get("weather", "forecast") or "null")
    except ValueError:
        return None
    if not w or time.time() - w.get("fetched", 0) > max_age:
        return None
    if w.get("today") and w["today"].get("date") and w["today"]["date"] < date.today().isoformat():
        return None    # yesterday's forecast: don't read it as today's
    return w


def _n(x) -> str:
    return "?" if x is None else str(round(float(x)))


def sentence(w: dict, spoken: bool = False) -> str:
    deg = " degrees" if spoken else "°C"
    now, today = w["now"], w.get("today") or {}
    text = f"In {w['place']} it's {_n(now['temp'])}{deg}"
    if now.get("sky"):
        text += f" and {now['sky']}"
    if now.get("feels") is not None and abs(float(now["feels"]) - float(now["temp"])) >= 2:
        text += f", feels like {_n(now['feels'])}"
    text += "."
    if today:
        text += f" Today {_n(today['low'])} to {_n(today['high'])}{deg}"
        if today.get("rain_chance") is not None:
            text += f", {_n(today['rain_chance'])}{' percent' if spoken else '%'} chance of rain"
        text += "."
        if today.get("rain_chance") is not None and float(today["rain_chance"]) >= 50:
            text += " Take an umbrella."
    return text


@p.briefing
def weather_line(now: datetime) -> dict | None:
    w = current()
    if not w:
        return None
    return {"order": 5, "screen": sentence(w), "spoken": sentence(w, spoken=True), "data": {"weather": w}}


@p.job(300)
def keep_fresh(now: datetime) -> list:
    """Every 5 minutes check; fetch only when the kept forecast is over 30 minutes old."""
    if config.OFFLINE:
        return []
    try:
        w = json.loads(memory.kv_get("weather", "forecast") or "null")
    except ValueError:
        w = None
    if not w or time.time() - w.get("fetched", 0) > REFRESH_S:
        refresh()
    return []


def weather_tool(day: str = "today") -> ToolResult:
    w = current() or (None if config.OFFLINE else refresh())
    if not w:
        return ToolResult("I can't get the weather right now (offline, or Open-Meteo didn't answer).", ok=False)
    if day == "tomorrow" and w.get("tomorrow"):
        t = w["tomorrow"]
        line = f"Tomorrow in {w['place']}: {_n(t['low'])} to {_n(t['high'])} degrees, {t['sky'] or 'sky unknown'}, " \
               f"{_n(t['rain_chance'])} percent chance of rain."
        return ToolResult(line, screen=f"{line} {CREDIT}.")
    return ToolResult(sentence(w, spoken=True), screen=f"{sentence(w)} {CREDIT}.")


p.add(Tool("weather_now", "The weather now and today's or tomorrow's forecast for Ali's town (Open-Meteo).",
           schema({"day": {"type": "string", "enum": ["today", "tomorrow"]}}), "read", weather_tool))
p.mock_rule(0.9, ("weather", "rain today", "forecast"), "weather_now")


@p.dashboard
def page(pg: dict) -> None:
    w = current(max_age=24 * 3600)
    where = place()
    pg["stats"] = [{"label": "Town", "value": where["name"]}]
    if w:
        pg["stats"] += [{"label": "Now", "value": f"{_n(w['now']['temp'])}°C {w['now']['sky']}"},
                        {"label": "Today", "value": f"{_n(w['today']['low'])}-{_n(w['today']['high'])}°C, rain {_n(w['today']['rain_chance'])}%"}]
        if w.get("tomorrow"):
            t = w["tomorrow"]
            pg["stats"].append({"label": "Tomorrow", "value": f"{_n(t['low'])}-{_n(t['high'])}°C, rain {_n(t['rain_chance'])}%"})
        pg["note"] = f"Updated {w['at'].replace('T', ' ')}. Sunrise {w['today']['sunrise']}, sunset {w['today']['sunset']}. {CREDIT}."
    else:
        pg["note"] = "No forecast yet: AIRTAC fetches it every 30 minutes when it's online."


@p.routes
def api(router) -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel, Field

    class Place(BaseModel):
        name: str = Field(min_length=2, max_length=80)

    @router.get("")
    def show():
        return {"place": place(), "weather": current(max_age=24 * 3600), "credit": CREDIT}

    @router.post("/place")
    def change(body: Place):
        try:
            chosen = set_place(body.name)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"place": chosen, "weather": refresh()}

    @router.post("/refresh")
    def again():
        return {"weather": refresh()}
