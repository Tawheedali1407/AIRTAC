"""Schedule (17 Oct, Ali): birthdays repeat every year, festivals come from Google's holiday calendar,
and done items can be removed after N days (off by default)."""
from datetime import date

from fastapi.testclient import TestClient

from airtac.main import app
from plugins import schedule
from plugins.schedule import importer, store

client = TestClient(app, base_url="http://localhost")


def test_a_birthday_shows_every_year():
    store.add_event("Mom's birthday", "2001-03-14", category="birthday", repeat="yearly")
    got = store.list_events(start="2026-01-01", end="2027-12-31")
    assert [e["date"] for e in got if e["title"] == "Mom's birthday"] == ["2026-03-14", "2027-03-14"]
    nxt = [e for e in store.list_events() if e["title"] == "Mom's birthday"]
    assert len(nxt) == 1 and nxt[0]["date"] >= date.today().isoformat() and nxt[0]["first_date"] == "2001-03-14"


def test_leap_day_and_month_ends():
    assert store.occurrences(date(2004, 2, 29), "yearly", date(2027, 1, 1), date(2028, 12, 31)) == [date(2027, 2, 28), date(2028, 2, 29)]
    assert store.occurrences(date(2026, 1, 31), "monthly", date(2026, 2, 1), date(2026, 4, 30)) == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert len(store.occurrences(date(2026, 10, 19), "weekly", date(2026, 10, 1), date(2026, 11, 1))) == 2


def test_chat_understands_every_year():
    r = schedule.schedule_add("Ammi's birthday on 5 June every year")
    assert "every year" in r.spoken
    e = next(e for e in store.list_events(start="2027-06-01", end="2027-06-30"))
    assert e["repeat"] == "yearly" and e["category"] == "birthday" and "every year" not in e["title"]


def test_google_birthday_calendar_import_keeps_yearly():
    ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;VALUE=DATE:20050710\nRRULE:FREQ=YEARLY\nSUMMARY:Rahul's birthday\nEND:VEVENT\n"
           "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20261020\nRRULE:FREQ=WEEKLY;COUNT=4\nSUMMARY:Lab\nEND:VEVENT\nEND:VCALENDAR")
    items = importer.ics(ics)
    assert items[0]["repeat"] == "yearly" and items[1]["repeat"] == ""     # a 4-week series isn't "forever"


def test_festivals_from_the_holiday_calendar_only():
    y = date.today().year
    ics = ("BEGIN:VCALENDAR\nX-WR-CALNAME:Holidays in India\n"
           f"BEGIN:VEVENT\nDTSTART;VALUE=DATE:{y}1108\nSUMMARY:Diwali/Deepavali\nEND:VEVENT\n"
           f"BEGIN:VEVENT\nDTSTART;VALUE=DATE:{y + 1}0304\nSUMMARY:Holi\nEND:VEVENT\n"
           "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20190101\nSUMMARY:Old\nEND:VEVENT\nEND:VCALENDAR")
    r = schedule.import_holidays(ics)
    assert r["added"] == 2 and schedule.import_holidays(ics)["added"] == 0
    hol = [e for e in store.list_events() if e["category"] == "holiday"]
    assert {e["title"] for e in hol} == {"Diwali/Deepavali", "Holi"}


def test_holidays_route_says_when_offline():
    assert client.post("/api/plugins/schedule/holidays").status_code == 502


def test_done_items_are_kept_unless_you_choose():
    old = store.add_event("Old quiz", "2026-01-05", category="exam")
    store.set_done(old)
    store.add_event("Mom's birthday", "2001-03-14", repeat="yearly")
    assert client.get("/api/plugins/schedule/tidy").json()["days"] == 0
    assert schedule.tidy_done(date(2026, 10, 17)) == 0                       # off by default: nothing removed
    r = client.post("/api/plugins/schedule/tidy?days=30").json()
    assert r["days"] == 30 and r["removed"] == 1
    assert any(e["title"] == "Mom's birthday" for e in store.list_events())   # repeating items stay


def test_old_databases_get_the_column_after_a_backup():
    import sqlite3
    from airtac import config, db
    with db.connect() as conn:
        conn.execute("DROP TABLE events")
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL, time TEXT DEFAULT '', "
                     "category TEXT DEFAULT 'personal', notes TEXT DEFAULT '', source TEXT DEFAULT '', estimated INTEGER DEFAULT 0, "
                     "done INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO events (title, date) VALUES ('Old exam', '2026-11-01')")
    assert [e["title"] for e in store.list_events()] == ["Old exam"]
    copies = list((config.DB_PATH.parent / "backups").glob("airtac-before-repeat-*.db"))
    assert copies and sqlite3.connect(copies[0]).execute("SELECT title FROM events").fetchone() == ("Old exam",)
