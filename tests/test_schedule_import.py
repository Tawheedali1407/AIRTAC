"""Part C: importing schedules (week-by-week plans moved to start next week, .ics, dated lines) and Ask schedule."""
import base64
from datetime import date

from fastapi.testclient import TestClient

from airtac.main import app
from plugins import attachments as att
from plugins.schedule import ask_schedule, importer, save_items, store

here = lambda: TestClient(app, base_url="http://localhost")   # noqa: E731
TODAY = date(2026, 10, 17)   # a Saturday

PLAN = """How to use this plan
Spend about 2 hours on weekdays, every week from 5 Oct
2026 to June 2027.
Week DSA pattern + example problems Learning topic
1 (5 Oct) Arrays and prefix sums: Two Sum,
Running Sum
Python refresh
2 (12
Oct)
Two pointers: Valid Palindrome
NumPy: arrays
3 (19
Oct)
Sliding window: Max Sum Subarray
Pandas
13 (28
Dec)
Graphs 1: Number of Islands
Neural nets
14 (4
Jan)
Graphs 2: Course Schedule
LLM basics
Mock When Format
1 28 Nov 2026 10 math MCQs + 2 easy coding, 60 min
Engage 10 Apr 2027 4 coding + reasoning/CS MCQs, 60 min
"""


def test_a_weekly_plan_moves_to_start_next_week():
    r = importer.import_preview(PLAN, "plan.pdf", "next-week", today=TODAY)
    weeks = [x for x in r["items"] if x["title"].startswith("Week")]
    assert r["kind"] == "plan" and weeks[0] == {**weeks[0], "title": "Week 1: Arrays and prefix sums", "date": "2026-10-19"}
    assert [w["date"] for w in weeks] == ["2026-10-19", "2026-10-26", "2026-11-02", "2027-01-11", "2027-01-18"]   # Dec -> Jan: next year
    mocks = {x["title"].split(":")[0]: x["date"] for x in r["items"] if not x["title"].startswith("Week")}
    assert mocks == {"Mock 1": "2026-12-12", "Engage-style mock": "2027-04-24"}      # moved by the same 14 days
    assert r["moved_by"] == 14 and "starts on 2026-10-19" in r["note"]
    assert all(x["source"] == "plan.pdf" for x in r["items"])
    assert not any("June" in x["title"] or x["date"].startswith("2026-06") for x in r["items"])   # "June 2027" isn't a day


def test_keeping_the_files_dates_and_a_chosen_start_day():
    kept = importer.import_preview(PLAN, "plan.pdf", "", today=TODAY)
    assert kept["items"][0]["date"] == "2026-10-05" and kept["moved_by"] == 0
    later = importer.import_preview(PLAN, "plan.pdf", "2026-11-02", today=TODAY)
    assert min(x["date"] for x in later["items"]) == "2026-11-02"


def test_dated_lines_from_a_notice():
    text = "RGM College\nDBMS mid exam on 12 Nov 2026 at 10:00 am\nLab record submission: 2026-11-20\nHoliday 14/11/2026"
    r = importer.import_preview(text, "notice.png", today=TODAY)
    got = {(x["title"], x["date"], x["time"]) for x in r["items"]}
    assert ("DBMS mid exam", "2026-11-12", "10:00") in got
    assert ("Lab record submission", "2026-11-20", "") in got and ("Holiday", "2026-11-14", "") in got
    assert next(x for x in r["items"] if x["title"].startswith("DBMS"))["category"] == "exam"


def test_ics_calendar_events():
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Hackathon demo\r\nDTSTART:20261030T093000Z\r\nEND:VEVENT\r\n"
           "BEGIN:VEVENT\r\nSUMMARY:Weekly DSA\r\n  session\r\nDTSTART;VALUE=DATE:20261021\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    r = importer.import_preview(ics, "cal.ics", today=TODAY)
    titles = {x["title"]: x for x in r["items"]}
    assert titles["Weekly DSA session"]["date"] == "2026-10-21" and "added once" in r["note"]
    assert titles["Hackathon demo"]["date"] == "2026-10-30" and titles["Hackathon demo"]["time"]


def test_import_routes_preview_then_save_without_duplicates():
    raw = PLAN.encode()
    up = here().post("/api/upload", json={"section": "schedule", "name": "plan.txt", "data_base64": base64.b64encode(raw).decode()}).json()
    att.read_now(up["id"])
    preview = here().post("/api/plugins/schedule/import", json={"attachment": up["id"], "start": "next-week"}).json()
    assert preview["items"] and not store.list_events()           # nothing saved by the preview
    body = {"items": [{k: x[k] for k in ("title", "date", "time", "category", "notes", "source")} for x in preview["items"]]}
    first = here().post("/api/plugins/schedule/import/save", json=body).json()
    again = here().post("/api/plugins/schedule/import/save", json=body).json()
    assert first["added"] == len(preview["items"]) and again == {"added": 0, "skipped": len(preview["items"])}
    assert here().post("/api/plugins/schedule/import", json={"attachment": 9999}).status_code == 404
    assert here().post("/api/plugins/schedule/import", json={"attachment": up["id"], "start": "soon"}).status_code == 422


def test_ask_schedule_answers_only_from_stored_items():
    save_items([{"title": "DBMS mid exam", "date": "2026-11-12", "time": "10:00", "category": "exam"},
                {"title": "Week 1: Arrays", "date": "2026-10-19", "category": "study"}])
    nxt = ask_schedule("what's next week?", today=TODAY)
    assert "Week 1: Arrays" in nxt["answer"] and "DBMS" not in nxt["answer"]
    assert "DBMS mid exam" in ask_schedule("when is my dbms exam", today=TODAY)["answer"]
    assert ask_schedule("anything in march?", today=TODAY)["answer"].startswith("Nothing on your schedule")
    assert here().post("/api/plugins/schedule/ask", json={"q": "this week"}).status_code == 200
