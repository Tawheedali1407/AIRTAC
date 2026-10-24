"""Ali, 18 Oct: the morning greeting missed his 11 am session, current news and the weather.
Weather from Open-Meteo (the response below is a real one for Nandyal, 18 Oct 09:15); the day said in time order."""
import json
import time
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from airtac import briefing, memory
from airtac.main import app
from plugins import weather
from plugins.schedule import _today_lines, store

client = TestClient(app, base_url="http://localhost")
REAL = {"current": {"time": "2026-10-18T09:15", "temperature_2m": 28.2, "apparent_temperature": 32.5,
                    "relative_humidity_2m": 71, "weather_code": 3, "wind_speed_10m": 5.7},
        "daily": {"time": ["2026-10-18", "2026-10-19"], "weather_code": [53, 53], "temperature_2m_max": [33.7, 34.5],
                  "temperature_2m_min": [25.5, 24.6], "precipitation_probability_max": [61, 45],
                  "sunrise": ["2026-10-18T06:08", "2026-10-19T06:08"], "sunset": ["2026-10-18T17:53", "2026-10-19T17:53"]}}


def _keep(today: str):
    data = json.loads(json.dumps(REAL))
    data["daily"]["time"] = [today, (date.fromisoformat(today) + timedelta(days=1)).isoformat()]
    w = weather.parse(data, weather.place())
    memory.kv_set("weather", "forecast", json.dumps(w))
    return w


def test_forecast_is_read_and_said_plainly():
    w = weather.parse(REAL, weather.place())
    assert w["now"]["sky"] == "overcast" and w["today"]["sky"] == "drizzle" and w["today"]["sunset"] == "17:53"
    said = weather.sentence(w, spoken=True)
    assert said == ("In Nandyal it's 28 degrees and overcast, feels like 32. Today 26 to 34 degrees, "
                    "61 percent chance of rain. Take an umbrella.")
    assert weather.parse({"error": True}, weather.place()) is None


def test_greeting_has_the_weather_only_when_it_is_fresh():
    today = date.today().isoformat()
    _keep(today)
    b = briefing.build(datetime.combine(date.today(), datetime.min.time()).replace(hour=9))
    assert "In Nandyal it's 28 degrees" in b["spoken"]
    w = json.loads(memory.kv_get("weather", "forecast"))
    w["fetched"] = time.time() - 3 * 3600                                   # too old: left out, never guessed
    memory.kv_set("weather", "forecast", json.dumps(w))
    assert "Nandyal" not in briefing.build()["spoken"]
    _keep((date.today() - timedelta(days=1)).isoformat())                   # yesterday's forecast: left out
    assert weather.current() is None


def test_town_search_prefers_india_and_offline_is_honest(monkeypatch):
    monkeypatch.setattr(weather, "fetch", lambda url, **k: {"results": [
        {"name": "Kurnool", "latitude": 15.83, "longitude": 78.04, "country_code": "IN", "admin1": "Andhra Pradesh",
         "timezone": "Asia/Kolkata"}]} if "geocoding" in url else None)
    chosen = weather.set_place("Kurnool")
    assert chosen["name"] == "Kurnool" and weather.place()["lat"] == 15.83
    assert "can't get the weather" in weather.weather_tool().spoken           # offline in tests: says so
    r = client.get("/api/plugins/weather").json()
    assert r["place"]["name"] == "Kurnool" and "Open-Meteo" in r["credit"]


def test_today_is_said_in_time_order_with_the_session_time():
    """Ali's real morning: a festival, two untimed study items and an 11 am workshop, heard at 9:20."""
    rows = [{"title": "Week 1: Arrays and prefix sums", "time": "", "category": "study"},
            {"title": "Maha Saptami", "time": "", "category": "holiday"},
            {"title": "AI roadmap kickoff", "time": "", "category": "study"},
            {"title": "Claude Powered Ai tools & side income workshop", "time": "11:00", "category": "study"}]
    spoken = " ".join(s for _, s in _today_lines(rows, datetime(2026, 10, 18, 9, 20)))
    assert spoken == ("Today is Maha Saptami. Today at 11 am: Claude Powered Ai tools & side income workshop, "
                      "in 1 hour 40 minutes. Also today: Week 1: Arrays and prefix sums and 1 more.")
    later = " ".join(s for _, s in _today_lines(rows, datetime(2026, 10, 18, 11, 30)))
    assert "11 am" not in later and "Also today: Week 1" in later              # a passed session isn't announced


def test_full_greeting_order(monkeypatch):
    today = date.today()
    store.add_event("Workshop", today.isoformat(), "23:59")
    _keep(today.isoformat())
    b = briefing.build(datetime.combine(today, datetime.min.time()).replace(hour=9, minute=20))
    s = b["spoken"]
    assert s.index("It's 9:20 am on") < s.index("In Nandyal") < s.index("Today at 11:59 pm: Workshop")
