"""History search (every word, in any order, special characters taken literally) and the
"Your progress" numbers: streak, chats this week, tasks."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from airtac import db, memory
from airtac.main import app


def _ask(text, day: date, session="s1"):
    memory.add_message(session, "user", text)
    with db.connect() as conn:   # put it on that day
        conn.execute("UPDATE messages SET created_at = ? WHERE id = (SELECT max(id) FROM messages)", (f"{day} 10:00:00",))


def test_history_search_matches_every_word_in_any_order():
    today = date.today()
    _ask("When is my DBMS exam?", today)
    _ask("Explain DBMS normal forms", today)
    _ask("100% sure about the_answer?", today)
    assert [r["text"] for r in memory.search_messages("exam dbms")] == ["When is my DBMS exam?"]
    assert len(memory.search_messages("dbms")) == 2
    assert [r["text"] for r in memory.search_messages("100%")] == ["100% sure about the_answer?"]
    assert memory.search_messages("%") == [r for r in memory.search_messages("%") if "%" in r["text"]]   # literal %
    assert memory.search_messages("the_a")[0]["text"].startswith("100%")
    assert memory.search_messages("nothing like this") == []


def test_streak_counts_days_in_a_row_up_to_today_or_yesterday():
    t = date(2026, 10, 17)
    assert memory.you(t.isoformat())["streak"] == 0 and memory.you(t.isoformat())["ever"] is False
    for back in (1, 2, 3, 5):                                       # 14, 15, 16 in a row; 12 is a gap
        _ask(f"q{back}", t - timedelta(days=back))
    y = memory.you(t.isoformat())
    assert y["streak"] == 3 and y["asked_today"] is False           # not asked yet today: streak still shows
    _ask("today", t, session="s2")
    y = memory.you(t.isoformat())
    assert y["streak"] == 4 and y["asked_today"] and y["chats_week"] == 2 and y["questions_week"] == 5   # 12, 14, 15, 16, 17


def test_progress_api_carries_your_numbers_and_tasks():
    c = TestClient(app, base_url="http://localhost")
    c.post("/api/plugins/schedule", json={"title": "DBMS exam", "date": (date.today() + timedelta(days=3)).isoformat()})
    you = c.get("/api/progress").json()["you"]
    assert set(you) >= {"streak", "chats_week", "tasks_done", "tasks_total"} and you["tasks_total"] == 1
