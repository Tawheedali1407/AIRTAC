"""Turn a document into schedule items: a calendar file (.ics), a week-by-week plan (like a prep plan PDF),
or any text with dates in it (a timetable PDF, a photo of a notice read with OCR).

Nothing is saved here: import_preview() returns the items, the page shows them, and you press Save.
Only dates written in the document are used (never guessed); a plan can be moved to start on another day,
and every item keeps the file it came from.
"""
import re
from datetime import date, datetime, timedelta

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
MON = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# "12 Nov 2026", "12 November", "Nov 12, 2026", "2026-11-12", "12/11/2026" (day first, as in India)
DATE_RES = [
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{MON}(?:,?\s+(\d{{4}}))?", re.I), "dmy"),
    (re.compile(rf"\b{MON}\s+(\d{{1,2}})(?!\d)(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", re.I), "mdy"),   # not "June 2027"
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    (re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b"), "slash"),
]
TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm)\b", re.I)
WEEK_RE = re.compile(rf"(?m)^\s*(\d{{1,2}})\s*\(\s*(\d{{1,2}})\s*\n?\s*{MON}\s*\)", re.I)


def _month(word: str) -> int:
    return MONTHS[word[:3].lower()]


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(line: str, year: int) -> list[tuple[date, tuple[int, int]]]:
    """Every date in a line, with where it is. A date without a year takes `year`."""
    out = []
    for rx, kind in DATE_RES:
        for m in rx.finditer(line):
            if kind == "dmy":
                d = _safe(int(m.group(3) or year), _month(m.group(2)), int(m.group(1)))
            elif kind == "mdy":
                d = _safe(int(m.group(3) or year), _month(m.group(1)), int(m.group(2)))
            elif kind == "iso":
                d = _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            else:
                d = _safe(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if d and not any(a <= m.start() < b for _, (a, b) in out):
                out.append((d, m.span()))
    return out


def _time(line: str) -> str:
    m = TIME_RE.search(line)
    if not m:
        return ""
    if m.group(1):
        h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    else:
        h, mi, ap = int(m.group(4)), 0, m.group(5).lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else ""


def _clean(text: str, n: int = 120) -> str:
    return re.sub(r"\s+", " ", text).strip(" -:|,;.")[:n]


def _guess(text: str) -> str:
    from plugins.schedule import guess_category
    return guess_category(text)


# ---------------------------------------------------------------- a week-by-week plan

def plan_year(text: str, today: date) -> int:
    """The year a plan starts in: the first full date in it ("from 5 Oct 2026"), else this year."""
    for line in text.splitlines():
        for d, _ in find_dates(line, today.year):
            if re.search(r"\d{4}", line):
                return d.year
    return today.year


def weekly_plan(text: str, today: date) -> list[dict]:
    """Rows like "3 (19 Oct) Sliding window: ..." become one item on that Monday, with the row's text
    as notes. Years count up as the months pass Dec -> Jan."""
    marks = list(WEEK_RE.finditer(text))
    if len(marks) < 3:                       # not a weekly plan
        return []
    year, last_month, out = plan_year(text, today), 0, []
    for i, m in enumerate(marks):
        month = _month(m.group(3))
        if month < last_month:
            year += 1
        last_month = month
        start = _safe(year, month, int(m.group(2)))
        if start is None:
            continue
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else m.end() + 600]
        body = re.split(r"\n(?:Ali's|Page \d|Phase \d|Week\s|Possible exam|Question types)", body)[0]
        flat = _clean(body, 600)
        topic = _clean(re.split(r":|\.\s", flat)[0], 60) or "Plan"
        if len(topic) > 44:
            topic = topic[:44].rsplit(" ", 1)[0]
        out.append({"title": f"Week {m.group(1)}: {topic}", "date": start.isoformat(), "time": "",
                    "category": "study", "notes": flat[:480], "kind": "week"})
    return out


def dated_lines(text: str, today: date, skip: set[str] | None = None) -> list[dict]:
    """Any line with a full or partial date becomes an item: the rest of the line is its title."""
    out, seen = [], set(skip or ())
    year = plan_year(text, today)
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 6:
            continue
        found = find_dates(line, year)
        if not found:
            continue
        d, (a, b) = found[0]
        title = _clean(TIME_RE.sub(" ", line[:a] + " " + line[b:]), 100)
        title = re.sub(r"(?i)\s+(on|at|by|from|till|until)$", "", re.sub(r"(?i)\s+(on|at|by)\s+(on|at|by)\b", " ", title)).strip(" -:|,;.")
        title = re.sub(r"^\d{1,2}\s+", "", title) if re.match(r"^\d{1,2}\s+\D", title) else title
        if len(title) < 3 or (title, d.isoformat()) in seen:
            continue
        seen.add((title, d.isoformat()))
        out.append({"title": title[0].upper() + title[1:], "date": d.isoformat(), "time": _time(line[b:]),
                    "category": _guess(title), "notes": "", "kind": "line"})
    return out


# ---------------------------------------------------------------- calendar files

def _unfold(text: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def ics(text: str) -> list[dict]:
    """VEVENTs from an .ics file (Google Calendar, Outlook). A yearly, monthly or weekly RRULE (a birthday from
    Google Calendar) becomes a repeating item; other repeat rules are added once, on their first day."""
    out, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if cur.get("date") and cur.get("title"):
                out.append({"title": cur["title"][:120], "date": cur["date"], "time": cur.get("time", ""),
                            "category": _guess(cur["title"]), "notes": cur.get("notes", "")[:480], "kind": "ics",
                            "repeats": cur.get("repeats", False), "repeat": cur.get("repeat", "")})
            cur = None
        elif cur is not None and ":" in line:
            key, value = line.split(":", 1)
            name = key.split(";")[0].upper()
            value = value.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";")
            if name == "SUMMARY":
                cur["title"] = _clean(value)
            elif name == "DESCRIPTION":
                cur["notes"] = _clean(value, 480)
            elif name == "RRULE":
                cur["repeats"] = True
                freq = re.search(r"FREQ=(YEARLY|MONTHLY|WEEKLY)", value.upper())
                plain = not re.search(r"(INTERVAL=(?!1\b)|BYDAY=\d|BYSETPOS|COUNT=|UNTIL=)", value.upper())
                if freq and plain:      # every year / month / week, no exceptions: keep it repeating
                    cur["repeat"] = freq.group(1).lower()
            elif name == "DTSTART":
                m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?", value)
                if m:
                    d = _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    if d and m.group(4):
                        when = datetime(d.year, d.month, d.day, int(m.group(4)), int(m.group(5)))
                        if m.group(7):          # UTC: show it in this PC's time
                            when = when.replace(tzinfo=__import__("datetime").timezone.utc).astimezone().replace(tzinfo=None)
                        cur["date"], cur["time"] = when.date().isoformat(), when.strftime("%H:%M")
                    elif d:
                        cur["date"] = d.isoformat()
    return out


# ---------------------------------------------------------------- one entry point

def next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def import_preview(text: str, source: str, start: str = "", today: date | None = None) -> dict:
    """{"items": [...], "kind", "note", "moved_by"}: what the document holds, not saved yet.
    start = "YYYY-MM-DD" or "next-week": move a week-by-week plan (and the dated items in it) so its
    first week begins on that day."""
    today = today or date.today()
    if "BEGIN:VCALENDAR" in text[:2000]:
        items, kind = ics(text), "calendar"
    else:
        weeks = weekly_plan(text, today)
        if weeks:
            dated = [x for x in dated_lines(text, today) if re.match(r"(?i)(mock|engage|test|exam|\d)", x["title"])
                     and x["date"] not in {w["date"] for w in weeks}]
            for x in dated:                  # rows of a "Mock | When | Format" table
                m = re.match(r"^(\d(?:-\d)?)\s+(.+)", x["title"])
                if m:
                    x["title"] = f"Mock {m.group(1)}: {m.group(2)}"
                elif x["title"].lower().startswith("engage "):
                    x["title"] = "Engage-style mock: " + x["title"][7:]
                x["category"] = "study"
            items, kind = weeks + dated, "plan"
        else:
            items, kind = dated_lines(text, today), "dates"
    moved = 0
    if start and items:
        first = min(date.fromisoformat(x["date"]) for x in items if x.get("kind") == "week") if kind == "plan" \
            else min(date.fromisoformat(x["date"]) for x in items)
        target = next_monday(today) if start == "next-week" else date.fromisoformat(start)
        moved = (target - first).days
        for x in items:
            x["date"] = (date.fromisoformat(x["date"]) + timedelta(days=moved)).isoformat()
    for x in items:
        x["source"] = source[:300]
    items.sort(key=lambda x: (x["date"], x["time"]))
    note = {"calendar": "Calendar events found.", "plan": "A week-by-week plan: one item per week (on its Monday), plus its dated tests.",
            "dates": "Dates found in the text."}[kind]
    if any(x.get("repeats") for x in items):
        note += " Repeating events are added once, on their first day."
    if moved:
        note += f" Moved {abs(moved)} days {'later' if moved > 0 else 'earlier'} so it starts on {min(x['date'] for x in items)}."
    return {"items": items[:400], "kind": kind, "note": note if items else "No dates found in that file.", "moved_by": moved}
