"""The events table: your schedule.

Repeating items (Ali, 17 Oct: "birthdays every year without always adding"): an event with repeat =
yearly, monthly or weekly is stored once, on its first date, and list_events() shows it on every date it
falls in the window asked for (29 Feb falls on 28 Feb in other years; the 31st on a month's last day).
"""
import calendar
from datetime import date, timedelta

from airtac import db, memory

REPEATS = ("", "yearly", "monthly", "weekly")


def _ensure_repeat(conn) -> None:
    """Databases made before 17 Oct have no repeat column: add it (nothing else changes)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
    if cols and "repeat" not in cols:
        _backup_first(conn)            # the rule: back up data\ before any migration
        conn.execute("ALTER TABLE events ADD COLUMN repeat TEXT DEFAULT ''")


def _backup_first(conn) -> None:
    import sqlite3
    import time
    from airtac import config
    folder = config.DB_PATH.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / f"airtac-before-repeat-{time.strftime('%Y%m%d-%H%M%S')}.db") as copy:
        conn.backup(copy)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT DEFAULT '',
    category TEXT DEFAULT 'personal',
    notes TEXT DEFAULT '',
    source TEXT DEFAULT '',
    estimated INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    repeat TEXT DEFAULT '',

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def add_event(title: str, date: str, time: str = "", category: str = "personal", notes: str = "",
              source: str = "", estimated: bool = False, repeat: str = "") -> int:
    with db.connect() as conn:
        _ensure_repeat(conn)
        return conn.execute(
            "INSERT INTO events (title, date, time, category, notes, source, estimated, repeat) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title.strip()[:120], date, time, (category or "personal").lower()[:20], notes[:500], source[:300],
             int(bool(estimated)), repeat if repeat in REPEATS else "")).lastrowid


def _clamp(y: int, m: int, d: int) -> date:
    return date(y, m, min(d, calendar.monthrange(y, m)[1]))


def occurrences(base: date, repeat: str, start: date, end: date) -> list[date]:
    """The dates a repeating item falls on between start and end (inclusive), never before its first date."""
    out, start = [], max(start, base)
    if repeat == "yearly":
        for y in range(start.year, end.year + 1):
            d = _clamp(y, base.month, base.day)
            if start <= d <= end:
                out.append(d)
    elif repeat == "monthly":
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            d = _clamp(y, m, base.day)
            if start <= d <= end:
                out.append(d)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif repeat == "weekly":
        d = start + timedelta(days=(base.weekday() - start.weekday()) % 7)
        while d <= end:
            out.append(d)
            d += timedelta(days=7)
    return out


def list_events(category: str | None = None, start: str | None = None, end: str | None = None,
                include_done: bool = True) -> list[dict]:
    """Events by date. start/end are 'YYYY-MM-DD' (inclusive)."""
    where, args = [], []
    for clause, value in (("category = ?", category), ("date >= ?", start), ("date <= ?", end)):
        if value:
            where.append(clause)
            args.append(value.lower() if clause.startswith("category") else value)
    if not include_done:
        where.append("done = 0")
    where.append("coalesce(repeat, '') = ''")
    sql = "SELECT * FROM events WHERE " + " AND ".join(where) + " ORDER BY date, time, id"  # nosec B608 (placeholders only)
    with db.connect() as conn:
        _ensure_repeat(conn)
        rows = db.rows(conn.execute(sql, args))
        repeating = db.rows(conn.execute("SELECT * FROM events WHERE coalesce(repeat, '') != '' AND (? OR done = 0)",
                                         (int(include_done),)))
    today = date.today()
    lo = date.fromisoformat(start) if start else today
    hi = date.fromisoformat(end) if end else lo + timedelta(days=366)
    for e in repeating:
        if category and e["category"] != category.lower():
            continue
        dates = occurrences(date.fromisoformat(e["date"]), e["repeat"], lo, hi)
        if not start and not end:
            dates = dates[:1]           # the whole list: just the next time it comes round
        rows += [{**e, "date": d.isoformat(), "first_date": e["date"]} for d in dates]
    return sorted(rows, key=lambda e: (e["date"], e.get("time") or "", e["id"]))


def remove_done_older_than(days: int, today: date | None = None) -> int:
    """Settings > Remove done items after N days (off unless you choose a number). Repeating items stay."""
    if days <= 0:
        return 0
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        _ensure_repeat(conn)
        return conn.execute("DELETE FROM events WHERE done = 1 AND date < ? AND coalesce(repeat, '') = ''", (cutoff,)).rowcount


def find_event(text: str) -> dict | None:
    """The not-done event whose title best matches the words in text (for "mark X done")."""
    terms = memory._terms(text)
    best, score = None, 0
    for e in list_events(include_done=False):
        s = sum(t in e["title"].lower() for t in terms)
        if s > score:
            best, score = e, s
    return best


def set_done(event_id: int, done: bool = True) -> bool:
    with db.connect() as conn:
        return conn.execute("UPDATE events SET done = ? WHERE id = ?", (int(done), event_id)).rowcount == 1


def update_event(event_id: int, **changes) -> bool:
    """Change some fields of one event (title, date, time, category, notes, done)."""
    fields = {k: v for k, v in changes.items() if k in ("title", "date", "time", "category", "notes", "done", "repeat")}
    if fields.get("repeat") not in (None, *REPEATS):
        fields.pop("repeat")
    if not fields:
        return False
    if "title" in fields:
        fields["title"] = fields["title"].strip()[:120]
    if "done" in fields:
        fields["done"] = int(bool(fields["done"]))
    sql = ", ".join(f"{k} = ?" for k in fields)
    if "date" in fields:
        sql += ", estimated = 0"   # a date you set by hand is no longer a guess
    with db.connect() as conn:
        return conn.execute(f"UPDATE events SET {sql} WHERE id = ?", (*fields.values(), event_id)).rowcount == 1  # nosec B608 (placeholders only)


def delete_event(event_id: int) -> bool:
    with db.connect() as conn:
        return conn.execute("DELETE FROM events WHERE id = ?", (event_id,)).rowcount == 1
