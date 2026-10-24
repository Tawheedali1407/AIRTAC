"""Schedule: exams, deadlines and plans, a spoken summary of your day, and reminders.

This is the reference plugin: it uses every hook (tools, briefing, dashboard, progress,
routes, a background job and no-model rules). Copy its shape when you write your own.

Events live in the events table (store.py). A plan can be added in one click from
templates/ (for example the summer 2027 internship prep); dates guessed from last year's
cycle are marked estimated there and stay marked here.
"""
import json
import re
from datetime import date, datetime, time, timedelta

from fastapi import HTTPException
from pydantic import BaseModel, Field

from airtac import dates, memory
from airtac.plugin_api import Tool, ToolResult, capitalize, fetch, plugin, schema

from . import store

p = plugin(__name__)
p.sql(store.SCHEMA)

CATEGORIES = ("internship", "exam", "contest", "project", "study", "reminder", "personal", "birthday", "holiday", "follow-up")
# Festivals and public holidays come from Google's public "Holidays in India" calendar (never typed in by AIRTAC).
HOLIDAYS_ICS = "https://calendar.google.com/calendar/ical/en.indian%23holiday%40group.v.calendar.google.com/public/basic.ics"
TEMPLATES = p.dir / "templates"


def guess_category(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ("gsoc", "internship", "intern", "summer school", "amazon", "microsoft", "engage")):
        return "internship"
    if any(w in low for w in ("hackathon", "project", "deploy", "submit", "devpost", "demo video", "airtac")):
        return "project"
    if any(w in low for w in ("contest", "codeforces", "codechef", "leetcode weekly")):
        return "contest"
    # Whole words only: "contest" contains "test", "midnight" contains "mid".
    if re.search(r"\b(exams?|mid|mids|midterm|sem|semester|quiz|test|viva)\b", low):
        return "exam"
    if any(w in low for w in ("study", "revise", "assignment", "lab", "record")):
        return "study"
    return "personal"


# ---------------------------------------------------------------- plans (templates/)

def load_template(name: str) -> dict:
    return json.loads((TEMPLATES / f"{name}.json").read_text(encoding="utf-8"))


def list_templates() -> list[dict]:
    out = []
    for path in sorted(TEMPLATES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append({"name": path.stem, "title": data.get("name", path.stem), "count": len(data.get("events", [])),
                    "estimated": any(e.get("estimated") for e in data.get("events", []))})
    return out


def add_template(name: str) -> int:
    """Add a plan's events, skipping any already on the schedule (same title and date)."""
    existing = {(e["title"], e["date"]) for e in store.list_events()}
    added = 0
    for e in load_template(name)["events"]:
        if (e["title"], e["date"]) in existing:
            continue
        store.add_event(e["title"], e["date"], e.get("time", ""), e.get("category", "personal"),
                        e.get("notes", ""), e.get("source", ""), e.get("estimated", False))
        added += 1
    return added


def _line(e: dict) -> str:
    d = date.fromisoformat(e["date"])
    mark = "[x]" if e.get("done") else "[ ]"
    est = " (estimated)" if e.get("estimated") else ""
    return f"{mark} {dates.nice(d, e.get('time', ''))}: {e['title']}{est}"


# ---------------------------------------------------------------- tools

REPEAT_WORDS = (("yearly", r"\b(every year|yearly|annual(ly)?|birthday|anniversary)\b"),
                ("monthly", r"\b(every month|monthly)\b"), ("weekly", r"\b(every week|weekly)\b"))


def repeat_from(text: str) -> str:
    low = (text or "").lower()
    return next((r for r, pat in REPEAT_WORDS if re.search(pat, low)), "")


def schedule_add(title: str, when: str = "", category: str = "", notes: str = "", repeat: str = "") -> ToolResult:
    d, t, words = dates.find_when(when or title)
    if d is None:
        return ToolResult("I couldn't tell the date. Say it like 12 Nov, next Monday or in 3 days.")
    repeat = repeat if repeat in store.REPEATS else ""
    repeat = repeat or repeat_from(f"{title} {when}")
    title = dates.strip_when(title, words) if not when else title.strip()
    title = re.sub(r"(?i)\s*\b(every year|every month|every week|yearly|monthly|weekly)\b", "", title).strip(" ,") or title
    if not title:
        return ToolResult("What should I put on your schedule?")
    category = category if category in CATEGORIES else ("birthday" if "birthday" in title.lower() else guess_category(title))
    store.add_event(title, d.isoformat(), t, category, notes, repeat=repeat)
    again = {"yearly": ", every year", "monthly": ", every month", "weekly": ", every week"}.get(repeat, "")
    return ToolResult(f"Added {title} on {dates.nice(d, t)}{again} to your schedule.",
                      f"Added to your schedule ({category}{again}):\n{_line({'date': d.isoformat(), 'time': t, 'title': title})}")


def schedule_list(period: str = "upcoming", category: str = "") -> ToolResult:
    today = date.today()
    start, end = today.isoformat(), None
    if period == "week":
        end = (today + timedelta(days=7)).isoformat()
    elif period == "month":
        end = (today + timedelta(days=31)).isoformat()
    elif period == "all":
        start = None
    rows = store.list_events(category or None, start, end)
    if period != "all":
        rows = [e for e in rows if not e.get("done")]
    what = f"{category} " if category else ""
    when = {"week": "this week", "month": "in the next month", "all": "on your schedule"}.get(period, "coming up")
    if not rows:
        hint = " Or add a ready-made plan on the Schedule page." if not store.list_events() else ""
        return ToolResult(f"Nothing {what}{when}. Add something, like: add DBMS exam on 14 Nov.{hint}")
    first = rows[0]
    est = ", estimated" if first.get("estimated") else ""
    spoken = (f"You have {len(rows)} {what}item{'s' if len(rows) != 1 else ''} {when}. "
              f"Next: {first['title']} on {dates.nice(date.fromisoformat(first['date']))}{est}.")
    screen = f"Your {what}schedule\n" + "\n".join(_line(e) for e in rows[:25])
    if any(e.get("estimated") for e in rows):
        screen += "\n\nEstimated dates copy last year's cycle. Check the official pages when they're out."
    return ToolResult(spoken, screen)


def schedule_done(what: str) -> ToolResult:
    event = store.find_event(what)
    if event is None:
        return ToolResult("I couldn't find that on your schedule. Say a word from its title.")
    store.set_done(event["id"], True)
    return ToolResult(f"Marked {event['title']} as done. Nice work.", _line({**event, "done": True}))


def daily_briefing() -> ToolResult:
    from airtac import briefing  # imported here: briefing imports the plugin loader
    b = briefing.build()
    return ToolResult(b["spoken"], b["text"])


p.add(Tool("daily_briefing", "Give the user's spoken summary of the day: overdue items, today, tomorrow and what's next.",
           schema(), "local", daily_briefing))
p.add(Tool("schedule_add", "Add an exam, deadline, internship step or plan to the user's schedule on a date.",
           schema({"title": {"type": "string", "description": "What it is, e.g. GSoC proposal draft"},
                   "when": {"type": "string", "description": "The date in the user's words, e.g. 14 Nov, next Monday"},
                   "category": {"type": "string", "enum": list(CATEGORIES)},
                   "notes": {"type": "string"},
                   "repeat": {"type": "string", "enum": ["", "yearly", "monthly", "weekly"],
                              "description": "yearly for birthdays and anniversaries"}}, required=("title",)),
           "local", schedule_add))
p.add(Tool("schedule_list", "Show the user's schedule: upcoming items, this week, this month, or by category such as internship.",
           schema({"period": {"type": "string", "enum": ["upcoming", "week", "month", "all"]},
                   "category": {"type": "string", "enum": ["", *CATEGORIES]}}),
           "local", schedule_list))
p.add(Tool("schedule_done", "Mark an item on the user's schedule as done.",
           schema({"what": {"type": "string", "description": "Words from the item's title"}}, required=("what",)),
           "local", schedule_done))


# ---------------------------------------------------------------- your day (said when AIRTAC opens)

def _say(text: str) -> str:
    """Easier to read aloud: underscores and backslashes become spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[_\\]+", " ", text)).strip()


def _day_line(label: str, rows: list[dict], spoken: bool = False) -> str:
    """'Today: DBMS exam at 2 pm.' or 'Tomorrow: 3 things, starting with Claim the credits.'
    Only the first title is read: a list of titles (which may hold commas) is hard to follow."""
    first = rows[0]
    title = _say(first["title"]) if spoken else first["title"]
    at = ""
    if first.get("time"):
        at = f" at {dates.spoken_time(first['time'])}" if spoken else f" at {first['time']}"
    if len(rows) == 1:
        return f"{label}: {title}{at}."
    return f"{label}: {len(rows)} things, starting with {title}{at}."


def _timed_first(e: dict) -> tuple:
    """Timed items first, by time; festivals and birthdays next; untimed tasks last."""
    special = e.get("category") in ("holiday", "birthday")
    return (0 if e.get("time") else 1 if special else 2, e.get("time") or "")


def _in(minutes: int) -> str:
    h, m = divmod(max(0, minutes), 60)
    parts = ([f"{h} hour{'s' if h != 1 else ''}"] if h else []) + ([f"{m} minute{'s' if m != 1 else ''}"] if m else [])
    return " ".join(parts) or "now"


def _today_lines(todays: list[dict], now: datetime) -> list[tuple[str, str]]:
    """Today, said the way a person would (Ali, 18 Oct: his 11 am session wasn't announced): festivals and
    birthdays by name, then every timed item still ahead with its time and how long until it, then the
    untimed tasks. Items whose time has passed are left out of the spoken day."""
    out: list[tuple[str, str]] = []
    special = [e for e in todays if e.get("category") in ("holiday", "birthday")]
    rest = [e for e in todays if e not in special]
    for e in special[:2]:
        lead = "Today is" if e.get("category") == "holiday" else "Today:"
        out.append((f"{lead} {e['title']}.", f"{lead} {_say(e['title'])}."))
    hm = now.strftime("%H:%M")
    ahead = sorted((e for e in rest if e.get("time") and e["time"] >= hm), key=lambda e: e["time"])
    untimed = [e for e in rest if not e.get("time")]
    for i, e in enumerate(ahead[:3]):
        h, m = map(int, e["time"].split(":"))
        mins = (h * 60 + m) - (now.hour * 60 + now.minute)
        lead = "Today" if i == 0 else "Then"
        when = f", in {_in(mins)}" if i == 0 else ""
        out.append((f"{lead} at {e['time']}: {e['title']}{when}.",
                    f"{lead} at {dates.spoken_time(e['time'])}: {_say(e['title'])}{when}."))
    if len(ahead) > 3:
        out.append((f"{len(ahead) - 3} more timed items later today.", f"And {len(ahead) - 3} more later today."))
    if untimed:
        more = f" and {len(untimed) - 1} more" if len(untimed) > 1 else ""
        lead = "Also today" if ahead or special else "Today"
        out.append((f"{lead}: {untimed[0]['title']}{more}.", f"{lead}: {_say(untimed[0]['title'])}{more}."))
    if not rest:
        out.append(("Nothing else on your schedule today.",) * 2 if special else ("Nothing on your schedule today.",) * 2)
    elif not ahead and not untimed:
        out.append(("Everything timed today is done or past.",) * 2)
    return out


@p.briefing
def your_day(now: datetime) -> dict:
    today = now.date()
    tomorrow = today + timedelta(days=1)
    rows = store.list_events(start=(today - timedelta(days=14)).isoformat(),
                             end=(today + timedelta(days=90)).isoformat(), include_done=False)
    iso = today.isoformat()
    overdue = [e for e in rows if e["date"] < iso]
    todays = [e for e in rows if e["date"] == iso]
    tomorrows = [e for e in rows if e["date"] == tomorrow.isoformat()]
    later = [e for e in rows if e["date"] > tomorrow.isoformat()]
    week = [e for e in rows if iso <= e["date"] <= (today + timedelta(days=7)).isoformat()]
    screen, spoken = [], []

    def both(text_screen: str, text_spoken: str | None = None):
        screen.append(text_screen)
        spoken.append(text_spoken or text_screen)

    if overdue:
        n = len(overdue)
        lead = f"{n} item{'s are' if n > 1 else ' is'} overdue"
        both(f"{lead}: {overdue[0]['title']}.", f"{lead}: {_say(overdue[0]['title'])}.")
    for x in _today_lines(todays, now):
        both(*x)
    if tomorrows:
        tomorrows = sorted(tomorrows, key=_timed_first)
        both(_day_line("Tomorrow", tomorrows), _day_line("Tomorrow", tomorrows, spoken=True))
    elif later:
        nxt = later[0]
        d = date.fromisoformat(nxt["date"])
        days = (d - today).days
        est = " (estimated)" if nxt.get("estimated") else ""
        both(f"Next up: {nxt['title']}, {dates.nice(d, today=today)}, in {days} days{est}.",
             f"Next up: {_say(nxt['title'])}, {dates.spoken_day(d, today)}, in {days} days"
             + (". That date is estimated." if est else "."))
    if len(week) > max(len(todays) + len(tomorrows), 1):
        both(f"{len(week)} things this week.", f"You have {len(week)} things this week.")
    return {"order": 10, "screen": " ".join(screen), "spoken": " ".join(spoken),
            "data": {"overdue": overdue, "today": todays, "tomorrow": tomorrows,
                     "next": later[0] if later else None, "week_count": len(week)}}


# ---------------------------------------------------------------- app page and home screen

@p.dashboard
def page(pg: dict) -> None:
    rows = store.list_events()
    today = date.today().isoformat()
    upcoming = [e for e in rows if e["date"] >= today and not e["done"]]
    done = sum(1 for e in rows if e["done"])
    pg["stats"] = [{"label": "Coming up", "value": len(upcoming)}, {"label": "Done", "value": f"{done} of {len(rows)}"}]
    if upcoming:
        pg["stats"].append({"label": "Next", "value": dates.nice(date.fromisoformat(upcoming[0]["date"]))})
    cats: dict[str, list[int]] = {}
    for e in rows:
        c = cats.setdefault(e["category"], [0, 0])
        c[0] += 1 if e["done"] else 0
        c[1] += 1
    pg["bars"] = {"title": "Done by category", "kind": "progress",
                  "items": [{"label": c.title(), "value": d, "total": t} for c, (d, t) in cats.items()]} if cats else None


@p.progress
def numbers() -> dict:
    nxt = next(iter(store.list_events(start=date.today().isoformat(), include_done=False)), None)
    # "Tasks done" is shown by the core (memory.you), so no count here: only what's next.
    return {"order": 10,
            "next_event": {"title": nxt["title"], "date": nxt["date"], "estimated": bool(nxt["estimated"])} if nxt else None}


# ---------------------------------------------------------------- reminders and the morning briefing

def _due_reminders(now: datetime, before: int) -> list[dict]:
    out = []
    for e in store.list_events(start=now.date().isoformat(), end=now.date().isoformat(), include_done=False):
        if not e["time"]:
            continue
        try:
            starts = datetime.combine(now.date(), time.fromisoformat(e["time"]))
        except ValueError:
            continue
        minutes = (starts - now).total_seconds() / 60
        stamp = f"{e['date']} {e['time']}"
        window = 1 if e["category"] == "reminder" else before  # a reminder goes off at its time
        if 0 <= minutes <= window and memory.kv_get(p.id, f"reminded:{e['id']}") != stamp:
            memory.kv_set(p.id, f"reminded:{e['id']}", stamp)
            left = max(1, round(minutes))
            if e["category"] == "reminder":
                out.append({"title": f"Reminder: {e['title']}", "body": f"At {e['time']}.",
                            "spoken": f"Reminder: {_say(e['title'])}."})
            else:
                out.append({"title": f"{e['title']} at {e['time']}", "body": f"Starts in {left} minutes.",
                            "spoken": f"Heads up: {_say(e['title'])} starts in {left} minute{'s' if left != 1 else ''}."})
    return out


@p.job(60)
def remind(now: datetime) -> list[dict]:
    """Tell you before a timed event starts, and once each morning, what your day looks like."""
    out = _due_reminders(now, int(p.setting("remind_before_minutes", 15) or 0))
    morning = str(p.setting("morning_briefing", "07:30") or "")
    if re.fullmatch(r"\d{2}:\d{2}", morning):
        at = datetime.combine(now.date(), time.fromisoformat(morning))
        # Once a day, from that time until noon (a laptop opened at 9 still gets it).
        if at <= now < max(at + timedelta(hours=1), datetime.combine(now.date(), time(12)))\
                and memory.kv_get(p.id, "briefed") != now.date().isoformat():
            from airtac import briefing
            memory.kv_set(p.id, "briefed", now.date().isoformat())
            b = briefing.build(now)
            out.append({"title": "Your day", "body": b["text"], "spoken": b["spoken"]})
    return out


# ---------------------------------------------------------------- the Schedule page's editor

class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str = Field(default="", pattern=r"^(\d{2}:\d{2})?$")
    category: str = Field(default="personal", max_length=20)
    notes: str = Field(default="", max_length=500)
    repeat: str = Field(default="", pattern="^(|yearly|monthly|weekly)$")


class EventUpdate(BaseModel):
    """Any of these; the ones left out stay as they are."""
    title: str | None = Field(default=None, min_length=1, max_length=120)
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str | None = Field(default=None, pattern=r"^(\d{2}:\d{2})?$")
    category: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=500)
    done: bool | None = None
    repeat: str | None = Field(default=None, pattern="^(|yearly|monthly|weekly)$")


# ---------------------------------------------------------------- import a file, and ask the schedule

class ImportIn(BaseModel):
    attachment: int
    start: str = Field(default="", pattern=r"^(|next-week|\d{4}-\d{2}-\d{2})$")


class ImportItem(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str = Field(default="", pattern=r"^(|\d{2}:\d{2})$")
    category: str = Field(default="personal", max_length=20)
    notes: str = Field(default="", max_length=500)
    source: str = Field(default="", max_length=300)


class ImportSave(BaseModel):
    items: list[ImportItem] = Field(max_length=400)


def import_holidays(text: str | None = None) -> dict:
    """Festivals and public holidays from Google's "Holidays in India" calendar: this year's and next year's
    (the calendar lists the real dates; festivals like Diwali move every year, so they're not "repeating")."""
    from plugins.schedule import importer
    if text is None:
        from airtac.plugin_api import CURRENT
        token = CURRENT.set(p.id)          # fetch() checks the schedule plugin's own permission
        try:
            text = fetch(HOLIDAYS_ICS, as_text=True, timeout=20)
        finally:
            CURRENT.reset(token)
    if not text or "BEGIN:VCALENDAR" not in text:
        raise ValueError("Google's holiday calendar didn't answer. Try again later.")
    this_year = date.today().year
    items = [{**x, "category": "holiday", "repeat": "", "source": "Google Holidays in India"} for x in importer.ics(text)
             if x["date"][:4] in (str(this_year), str(this_year + 1))]
    return {**save_items(items), "found": len(items)}


def tidy_done(today: date | None = None) -> int:
    """Settings: remove done items after N days (0 = keep them, the default)."""
    return store.remove_done_older_than(int(p.setting("remove_done_after_days", 0) or memory.kv_get(p.id, "remove_done_after_days") or 0),
                                        today)


@p.job(3600)
def tidy(now: datetime) -> list[dict]:
    tidy_done(now.date())
    return []


class AskIn(BaseModel):
    q: str = Field(min_length=1, max_length=300)


def save_items(items: list[dict]) -> dict:
    """Add imported items, skipping ones already there (same title and date)."""
    existing = {(e["title"].lower(), e["date"]) for e in store.list_events()}
    added = skipped = 0
    for x in items:
        date.fromisoformat(x["date"])
        if (x["title"].lower(), x["date"]) in existing:
            skipped += 1
            continue
        store.add_event(x["title"], x["date"], x.get("time", ""), x["category"] if x.get("category") in CATEGORIES else "personal",
                        x.get("notes", ""), x.get("source", "") or "import", repeat=x.get("repeat", ""))
        existing.add((x["title"].lower(), x["date"]))
        added += 1
    return {"added": added, "skipped": skipped}


def period(q: str, today: date) -> tuple[date | None, date | None, str]:
    """The days a question is about ("this week", "next week", "tomorrow", "in November"), if any."""
    low = q.lower()
    monday = today - timedelta(days=today.weekday())
    if "tomorrow" in low:
        return today + timedelta(1), today + timedelta(1), "tomorrow"
    if "today" in low:
        return today, today, "today"
    if "next week" in low:
        return monday + timedelta(7), monday + timedelta(13), "next week"
    if "this week" in low or "week" in low:
        return today, monday + timedelta(6), "this week"
    for name, n in (("january", 1), ("february", 2), ("march", 3), ("april", 4), ("may", 5), ("june", 6), ("july", 7),
                    ("august", 8), ("september", 9), ("october", 10), ("november", 11), ("december", 12)):
        if re.search(rf"\b({name}|{name[:3]})\b", low):
            year = today.year + (1 if n < today.month else 0)
            end = (date(year + (n == 12), n % 12 + 1, 1) - timedelta(1))
            return date(year, n, 1), end, name.title()
    if "month" in low:
        return today, (date(today.year + (today.month == 12), today.month % 12 + 1, 1) - timedelta(1)), "this month"
    return None, None, ""


def ask_schedule(q: str, today: date | None = None) -> dict:
    """Answer a question from the stored schedule only (never invented dates)."""
    from airtac import llm
    today = today or date.today()
    start, end, label = period(q, today)
    rows = store.list_events(start=(start or today).isoformat(), end=end.isoformat() if end else None, include_done=False)
    words = [w for w in memory._terms(q) if w not in ("schedule", "week", "next", "this", "today", "tomorrow", "month", "when", "what")]
    if words and not start:          # "when is my dbms exam" -> the items that mention it (past ones too)
        rows = [e for e in store.list_events() if any(w in f"{e['title']} {e['notes']} {e['category']}".lower() for w in words)] or rows
    rows = rows[:60]
    lines = [f"{e['date']} {e['time'] or ''} {e['title']} ({e['category']}{', estimated' if e['estimated'] else ''})".replace("  ", " ")
             for e in rows]
    if not rows:
        return {"answer": f"Nothing on your schedule {('for ' + label) if label else 'matches that'}.", "items": [], "model": ""}
    if llm.brain_on():
        try:
            text = llm.ask_text("Answer the question using ONLY these schedule items (today is "
                                f"{today.strftime('%A %d %B %Y')}). Be short; give dates as weekday, day and month. "
                                "If they don't answer it, say so.", "\n".join(lines) + f"\n\nQuestion: {q}",
                                role="general", max_tokens=300)
            if text.strip():
                return {"answer": text.strip(), "items": rows, "model": "model"}
        except llm.LLMUnavailable:
            pass
    head = f"{len(rows)} item{'s' if len(rows) != 1 else ''}" + (f" for {label}" if label else "") + ":"
    return {"answer": head + "\n" + "\n".join(f"- {_line(e)}" for e in rows[:15]), "items": rows, "model": ""}


class TemplateIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9-]{1,40}$")


@p.routes
def api(router) -> None:
    @router.get("")
    def all_events(category: str = ""):
        return {"events": store.list_events(category or None), "sample": False}

    @router.post("")
    def add(body: EventIn):
        try:
            date.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(422, "That date doesn't exist.")
        category = body.category if body.category in CATEGORIES else "personal"
        return {"id": store.add_event(body.title, body.date, body.time, category, body.notes, repeat=body.repeat)}

    @router.get("/templates")
    def templates():
        return list_templates()

    @router.post("/holidays")
    def holidays():
        try:
            return import_holidays()
        except ValueError as e:
            raise HTTPException(502, str(e))

    @router.get("/tidy")
    def tidy_read():
        return {"days": int(memory.kv_get(p.id, "remove_done_after_days") or 0)}

    @router.post("/tidy")
    def tidy_set(days: int = 0):
        """Remove done items after this many days (0 = never). Repeating items are never removed."""
        days = max(0, min(int(days), 3650))
        memory.kv_set(p.id, "remove_done_after_days", str(days))
        return {"days": days, "removed": tidy_done()}

    @router.post("/template")
    def apply_template(body: TemplateIn):
        if not (TEMPLATES / f"{body.name}.json").exists():
            raise HTTPException(404, "No plan with that name.")
        return {"added": add_template(body.name)}

    @router.post("/import")
    def import_file(body: ImportIn):
        """What a file attached with the paperclip holds, as schedule items. Nothing is saved yet."""
        from plugins.attachments import get as attachment
        from plugins.schedule import importer
        row = attachment(body.attachment)
        if row is None:
            raise HTTPException(404, "That file isn't here any more. Attach it again.")
        if row["status"] != "ready":
            raise HTTPException(409, row["message"] or "That file is still being read. Try again in a moment.")
        return importer.import_preview(row["text"], row["name"], body.start)

    @router.post("/import/save")
    def import_save(body: ImportSave):
        return save_items([x.model_dump() for x in body.items])

    @router.post("/ask")
    def ask(body: AskIn):
        return ask_schedule(body.q)

    @router.post("/{event_id}")
    def update(event_id: int, body: EventUpdate):
        """Tick an item off, or edit it: only the fields you send change."""
        changes = body.model_dump(exclude_none=True)
        if "date" in changes:
            try:
                date.fromisoformat(changes["date"])
            except ValueError:
                raise HTTPException(422, "That date doesn't exist.")
        if "category" in changes and changes["category"] not in CATEGORIES:
            changes["category"] = "personal"
        if not changes or not store.update_event(event_id, **changes):
            raise HTTPException(404, "That item isn't on your schedule.")
        return {"ok": True}

    @router.delete("/{event_id}")
    def delete(event_id: int):
        if not store.delete_event(event_id):
            raise HTTPException(404, "That item isn't on your schedule.")
        return {"ok": True}


# ---------------------------------------------------------------- dates you mention in a chat

# Words that make a sentence an event worth keeping (with a date in it).
EVENT_WORDS = re.compile(r"\b(exams?|mid ?terms?|mids|tests?|quiz|viva|deadline|due|submit|submission|interview|"
                         r"meeting|class|lab|contest|hackathon|presentation|seminar|assignment|review|fee|"
                         r"appointment|webinar|workshop|internship|registration|results?)\b", re.I)
QUESTION = re.compile(r"^\s*(when|what|which|is|are|do|does|did|can|could|should|how|why|where|who)\b|\?\s*$", re.I)
ALREADY = ("schedule_add", "schedule_done", "schedule_list", "reminder", "eduskills_add", "eduskills_update",
           "daily_briefing")


def event_in(message: str):
    """(title, date, time) when the message states an event with a date, else None. Only dates you
    actually said count: nothing is guessed."""
    text = (message or "").strip()
    if not text or QUESTION.search(text) or not EVENT_WORDS.search(text):
        return None
    d, t, words = dates.find_when(text)
    if d is None or d < date.today():
        return None
    title = dates.strip_when(text, words)
    title = re.sub(r"^(hey|hi|so|ok|okay|fyi|btw|note|just so you know|remember( that)?|note that)\b[\s,:]*", "",
                   title, flags=re.I)
    title = re.sub(r"^(i|we)\s+(have|got|ve got|'ve got)\s+(an?|my|the|our)?\s*|^(my|our|the|an?)\s+", "", title, flags=re.I)
    title = re.sub(r"\s+(is|are|will be|starts?|begins?|falls?)\s*$|\s+(on|at|by|from)\s*$", "", title.strip(" .,!:;"), flags=re.I)
    title = capitalize(title.strip(" .,!:;"))[:80]
    return (title, d, t) if len(title) >= 3 else None


@p.observe
def from_chat(message: str, tools_used: list[str]):
    """A date you mention in passing ("my DBMS exam is on 14 Nov") goes on your schedule, once."""
    if not p.setting("auto_from_chat", True) or any(t in ALREADY for t in tools_used):
        return None
    found = event_in(message)
    if not found:
        return None
    title, d, t = found
    if any(e["title"].lower() == title.lower() and e["date"] == d.isoformat() for e in store.list_events()):
        return None
    eid = store.add_event(title, d.isoformat(), t, guess_category(title), "Added from a chat message.", "chat")
    when = dates.nice(d, t)
    return {"say": f"I put {title} on your schedule for {when}.",
            "screen": f"Added to your schedule from what you said: {title}, {when}. Undo it on the page or in Schedule.",
            "undo": {"method": "DELETE", "url": f"/api/plugins/schedule/{eid}", "label": f"Undo: remove {title}"}}


# ---------------------------------------------------------------- rules for the no-model mode

def _add_args(text, _messages):
    body = re.sub(r"\b(please\s+)?(add|put|schedule)\b|\b(to|on|in)\s+(my\s+)?(schedule|calendar|plan)\b", " ", text,
                  flags=re.IGNORECASE)
    _, _, words = dates.find_when(body)
    title = dates.strip_when(body, words).strip(" :,.?!")
    return {"title": capitalize(title) or "New item", "when": words}


def _list_args(text, _messages):
    low = text.lower()
    period = "week" if "week" in low else "month" if "month" in low else "all" if re.search(r"\ball\b|whole", low) else "upcoming"
    category = next((c for c, words in (("internship", ("internship", "gsoc", "summer school", "amazon")),
                                        ("exam", ("exam",)), ("contest", ("contest",))) if any(w in low for w in words)), "")
    return {"period": period, "category": category}


p.mock_rule(0, ("brief me", "my day", "daily briefing", "briefing", "plan for today", "what do i have today"),
            "daily_briefing")
p.mock_rule(1, ("as done", "i finished", "i completed", "done with"), "schedule_done",
            lambda t, m: {"what": re.sub(r"\b(mark|as|done|with|i|i'm|finished|completed|the|my)\b", " ", t,
                                         flags=re.IGNORECASE).strip(" .!?")})
p.mock_rule(2, ("add to my schedule", "to my schedule", "to my calendar", "put on my schedule", "add to schedule"),
            "schedule_add", _add_args)
p.mock_rule(3, ("schedule", "calendar", "deadlines", "coming up", "internship plan", "what's next"), "schedule_list",
            _list_args)
