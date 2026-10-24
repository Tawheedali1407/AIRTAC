"""Dual entry for the schedule: by hand on the page (add, edit, delete) and from what you say in a chat."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from airtac import agent
from airtac.main import app

client = TestClient(app, base_url="http://localhost")


def store():
    import plugins.schedule as sched
    return sched.store


def test_a_date_you_mention_goes_on_the_schedule_once_with_an_undo():
    r = agent.chat("My DBMS exam is on 14 Nov")
    rows = store().list_events()
    assert [(e["title"], e["date"][5:], e["category"], e["source"]) for e in rows] == [("DBMS exam", "11-14", "exam", "chat")]
    assert "I put DBMS exam on your schedule" in r.spoken
    undo = r.noticed[0]["undo"]
    assert undo["method"] == "DELETE" and undo["url"].startswith("/api/plugins/schedule/")
    agent.chat("My DBMS exam is on 14 Nov")                  # said again: not added twice
    assert len(store().list_events()) == 1
    assert client.delete(undo["url"]).status_code == 200 and store().list_events() == []


def test_questions_past_dates_and_plain_chat_add_nothing():
    for m in ("When is my DBMS exam?", "I watched a movie yesterday", "hello there",
              f"My viva was on {(date.today() - timedelta(days=3)).isoformat()}"):
        agent.chat(m)
    assert store().list_events() == []


def test_an_explicit_add_is_not_doubled():
    r = agent.chat("Add DBMS exam on 14 Nov to my schedule")
    assert r.steps[0].tool == "schedule_add" and not r.noticed and len(store().list_events()) == 1


def test_times_and_titles_come_out_clean():
    agent.chat("I have a placement interview on 20 Nov at 10 am")
    agent.chat("Remember that the hackathon deadline is 30 Oct 2027")
    got = {e["title"]: (e["date"][5:], e["time"]) for e in store().list_events()}
    assert got == {"Placement interview": ("11-20", "10:00"), "Hackathon deadline": ("10-30", "")}


def test_switch_off(monkeypatch):
    from airtac import config
    monkeypatch.setattr(config, "plugin_settings", lambda pid: {"auto_from_chat": False} if pid == "schedule" else {})
    agent.chat("My DBMS exam is on 14 Nov")
    assert store().list_events() == []


def test_edit_on_the_page():
    eid = client.post("/api/plugins/schedule", json={"title": "DBMS exam", "date": "2026-11-14", "category": "exam"}).json()["id"]
    assert client.post(f"/api/plugins/schedule/{eid}", json={"title": "DBMS mid exam", "time": "10:00"}).status_code == 200
    assert client.post(f"/api/plugins/schedule/{eid}", json={"date": "2026-11-16"}).status_code == 200
    assert client.post(f"/api/plugins/schedule/{eid}", json={"done": True}).status_code == 200
    e = store().list_events()[0]
    assert (e["title"], e["date"], e["time"], e["done"]) == ("DBMS mid exam", "2026-11-16", "10:00", 1)
    assert client.post(f"/api/plugins/schedule/{eid}", json={"date": "2026-02-30"}).status_code == 422
    assert client.post(f"/api/plugins/schedule/{eid}", json={}).status_code == 404
