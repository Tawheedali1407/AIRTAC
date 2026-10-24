"""Part E2: the AI roadmap as a learning plan: this week, into Schedule, re-plan, level check."""
from datetime import date

from fastapi.testclient import TestClient

from airtac import agent
from airtac.main import app
from plugins import learn
from plugins.schedule import store

client = TestClient(app, base_url="http://localhost")


def test_the_plan_comes_from_the_roadmap():
    pl = learn.plan()
    assert pl["start"] == "2026-10-18" and len(pl["stages"]) == 7 and len(pl["level_check"]) == 10
    assert pl["weeks"][0]["week"] == 3 and "gradient descent" in pl["weeks"][0]["lab"]
    assert all(r["url"].startswith("https://") for s in pl["stages"] for r in s["resources"])


def test_this_week():
    w = learn.current_week(date(2026, 11, 4))
    assert w["week"] == 5 and "micrograd" in learn.week_text(w)


def test_to_schedule_adds_labs_and_mocks_once(monkeypatch):
    r = learn.to_schedule(date(2026, 10, 18))
    titles = [e["title"] for e in store.list_events()]
    assert r["added"] > 30 and "AI roadmap kickoff" in titles
    assert any(t.startswith("Mock 1") for t in titles)
    again = learn.to_schedule(date(2026, 10, 18))
    assert again["added"] == 0 and again["skipped"] == r["added"]


def test_fell_behind_moves_unfinished_items_only():
    learn.to_schedule(date(2026, 10, 18))
    first = [e for e in store.list_events() if e["source"] == "learn"][1]
    store.set_done(first["id"])
    moved = learn.replan(7, date(2026, 10, 18))
    after = {e["id"]: e for e in store.list_events()}
    assert after[first["id"]]["date"] == first["date"]                       # done: stays
    assert moved >= 30 and learn._shift() == 7
    assert learn.current_week(date(2026, 10, 27))["week"] == 3               # the plan moved with it


def test_level_check_suggests_the_first_weak_stage():
    qs = learn.plan()["level_check"]
    answers = [q["answer"] for q in qs]
    answers[4] = (answers[4] + 1) % 4                                        # a classical-ML question wrong
    r = client.post("/api/plugins/learn/level", json={"answers": answers}).json()
    assert r["score"] == 9 and r["start_stage"] == 3


def test_chats_about_study_get_this_weeks_goals(monkeypatch):
    monkeypatch.setattr(learn, "current_week", lambda today=None: learn.plan()["weeks"][0])
    text, _ = agent.plugin_context("what should I study today?")
    assert "AI roadmap" in text and "Sliding window" in text
    assert "AI roadmap" not in agent.plugin_context("what's the weather like?")[0]


def test_page_and_done():
    assert client.post("/api/plugins/learn/done", json={"week": 3}).json()["done"] == [3]
    d = client.get("/api/plugins/learn").json()
    assert d["done"] == [3] and d["plan"]["name"] == "Become an AI expert"
    assert client.post("/api/plugins/learn/done", json={"week": 59}).status_code == 404
