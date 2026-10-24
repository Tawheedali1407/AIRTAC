"""Part E4/E5: the briefing (fetched items only), the planner, the check-in and the proactive rules."""
import json
from datetime import date, datetime, time as dtime

from fastapi.testclient import TestClient

from airtac import briefing as day_briefing, config
from airtac.main import app
from plugins import daily, learn
from plugins.schedule import store

client = TestClient(app, base_url="http://localhost")
FETCHED = ([{"title": "New open model beats GPT on maths", "url": "https://example.org/a", "snippet": "A lab released...", "source": "HN"},
            {"title": "Agents paper", "url": "https://arxiv.org/abs/2610.00001", "snippet": "We study...", "source": "arXiv"}],
           [{"title": "Chip exports rise", "url": "https://example.org/c", "snippet": "India's...", "source": "web"}], [])


def test_offline_says_so_and_invents_nothing():
    b = daily.build_briefing(datetime(2026, 10, 19, 7, 0))
    assert b["offline"] and not b["ai"] and "never makes up news" in b["note"]


def test_briefing_keeps_only_fetched_items(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(daily, "_gather", lambda: FETCHED)

    class Brain:
        def chat(self, *a, **k):
            from airtac.llm import LLMReply
            return LLMReply(text=json.dumps([{"n": 0, "summary": "An open model now leads on maths.", "why": "stage 5 reading"},
                                             {"n": 2, "summary": "India's chip exports grew.", "why": "jobs"},
                                             {"n": 9, "summary": "Invented story", "why": "x"}]))
    monkeypatch.setattr(daily.brains, "get", lambda role="general": Brain())
    b = daily.build_briefing(datetime(2026, 10, 19, 7, 0))
    assert [x["summary"] for x in b["ai"]] == ["An open model now leads on maths."]
    assert b["other"][0]["url"] == "https://example.org/c"
    assert "Invented story" not in json.dumps(b)                                  # item 9 was never fetched
    part = daily.today_line(datetime(2026, 10, 19, 8, 0))
    assert part["spoken"].startswith("In AI today: An open model now leads on maths")


def test_no_brain_shows_headlines_only(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(daily, "_gather", lambda: FETCHED)
    b = daily.build_briefing(datetime(2026, 10, 19, 7, 0))
    assert b["ai"][0]["headline_only"] and b["ai"][0]["summary"] == FETCHED[0][0]["title"]


def test_busy_brains_retry_the_summaries_later(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(daily, "_gather", lambda: FETCHED)

    class Busy:
        def chat(self, *a, **k):
            raise RuntimeError("503")
    monkeypatch.setattr(daily.brains, "get", lambda role="general": Busy())
    daily.morning_and_evening(datetime(2026, 10, 19, 6, 30))
    b = daily.briefing(date(2026, 10, 19))
    assert b["summaries"] is False and "tries again in 30 minutes" in b["note"]
    daily.morning_and_evening(datetime(2026, 10, 19, 6, 45))                 # too soon: unchanged
    assert daily.briefing(date(2026, 10, 19))["tries"] == 1

    class Ok:
        def chat(self, *a, **k):
            from airtac.llm import LLMReply
            return LLMReply(text=json.dumps([{"n": 0, "summary": "An open model now leads on maths.", "why": "x"}]))
    monkeypatch.setattr(daily.brains, "get", lambda role="general": Ok())
    daily.morning_and_evening(datetime(2026, 10, 19, 7, 5))
    b = daily.briefing(date(2026, 10, 19))
    assert b["summaries"] and b["tries"] == 2 and b["ai"][0]["summary"].startswith("An open model")


def test_planner_respects_college_hours_and_buffer():
    pr = daily.propose(date(2026, 10, 20))                                     # a Tuesday in week 3
    kinds = {b["title"]: b for b in pr["blocks"]}
    assert "College" in kinds and kinds["DSA"]["note"].startswith("Sliding window")
    starts = sorted((daily._mins(b["start"]), daily._mins(b["end"])) for b in pr["blocks"])
    assert all(a[1] <= b[0] for a, b in zip(starts, starts[1:]))                # nothing overlaps
    assert sum(b["kind"] == "study" for b in pr["blocks"]) <= 3
    assert pr["free_minutes"] > 0


def test_exam_week_is_light_mode():
    store.add_event("DBMS mid exam", "2026-10-22", "10:00", "exam")
    pr = daily.propose(date(2026, 10, 20))
    assert pr["light"] and [b["title"] for b in pr["blocks"] if b["kind"] == "study"] == ["DSA"]


def test_accept_then_checkin_moves_what_was_missed():
    day = date(2026, 10, 20)
    assert daily.accept(day) >= 3 and daily.accept(day) == 0                   # not twice
    planned = [e for e in store.list_events(start="2026-10-20", end="2026-10-20") if e["source"] == "planner"]
    r = daily.checkin(day, [planned[0]["id"]], "ok", "")
    assert r["done"] == 1 and r["moved"] == len(planned) - 1
    assert len([e for e in store.list_events(start="2026-10-21", end="2026-10-21") if e["source"] == "planner"]) == r["moved"]


def test_proactive_rules():
    morning = datetime(2026, 10, 19, 7, 0)
    assert daily.may_send("briefing", morning)
    assert not daily.may_send("briefing", datetime(2026, 10, 19, 23, 0))      # quiet hours
    for _ in range(3):
        daily._count(morning)
    assert not daily.may_send("checkin", morning)                             # at most 3 a day
    daily.save_settings({"proactive": False})
    assert not daily.may_send("briefing", datetime(2026, 10, 20, 7, 0))       # the global switch
    assert daily.save_settings({"max_per_day": 9})["max_per_day"] == 3


def test_morning_job_builds_once_and_respects_switches(monkeypatch):
    out = daily.morning_and_evening(datetime(2026, 10, 19, 6, 30))
    assert daily.briefing(date(2026, 10, 19)) is not None and out == []      # offline: built, but nothing to announce
    daily.save_settings({"checkin_on": False})
    assert daily.morning_and_evening(datetime(2026, 10, 19, 21, 45)) == []


def test_routes_and_home_line():
    d = client.get("/api/plugins/daily").json()
    assert "blocks" in d["plan"] and d["settings"]["briefing_time"] == "06:15"
    assert client.post("/api/plugins/daily/settings", json={"briefing_time": "7am"}).status_code == 400
    assert "Your AI briefing" not in day_briefing.build(datetime(2026, 10, 19, 8, 0))["text"]   # none today: no line


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Nvidia unveils new chip - The Hindu</title><link>https://news.google.com/rss/articles/abc</link>
<pubDate>Sat, 17 Oct 2026 08:00:00 GMT</pubDate><source url="https://www.thehindu.com">The Hindu</source></item>
<item><title>No link</title></item></channel></rss>"""


def test_google_news_rss_is_parsed_and_bombs_refused():
    rows = daily.parse_news_rss(RSS)
    assert rows == [{"title": "Nvidia unveils new chip", "url": "https://news.google.com/rss/articles/abc",
                     "source": "The Hindu", "snippet": "The Hindu, 17 Oct"}]
    assert daily.parse_news_rss('<!DOCTYPE x [<!ENTITY a "b">]><rss/>') == []


def test_offline_google_news_is_never_called():
    assert daily.google_news("AI") is None          # fetch() refuses in offline mode


def test_greeting_reads_top_news_then_ai_news_not_papers(monkeypatch):
    import time as _t
    from airtac import memory
    memory.kv_set("daily", "headlines", json.dumps({"at": _t.time(), "items": [
        {"title": "Monsoon rains lash Andhra coast", "url": "https://news.google.com/rss/articles/a", "source": "The Hindu"},
        {"title": "ISRO schedules next launch", "url": "https://news.google.com/rss/articles/b", "source": "NDTV"}]}))
    day = date.today()
    memory.kv_set("daily", f"briefing:{day.isoformat()}", json.dumps({
        "date": day.isoformat(), "offline": False, "other": [],
        "ai": [{"title": "Paper X", "summary": "Paper X", "url": "https://arxiv.org/abs/2610.1"},
               {"title": "Open model news", "summary": "A new open model leads coding tests", "url": "https://example.org/n"}]}))
    part = daily.today_line(datetime.combine(day, dtime(9, 20)))
    assert part["spoken"].startswith("Top news: Monsoon rains lash Andhra coast. ISRO schedules next launch.")
    assert "In AI today: A new open model leads coding tests" in part["spoken"]       # news before the arXiv paper
    memory.kv_set("daily", "headlines", json.dumps({"at": _t.time() - 5 * 3600, "items": [{"title": "Old", "url": "u"}]}))
    assert "Old" not in daily.today_line(datetime.combine(day, dtime(9, 20)))["spoken"]   # stale news isn't "today"
