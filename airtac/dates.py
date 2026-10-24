"""Understand dates the way people say them: "12 Nov", "tomorrow", "next Monday", "in 3 days".

Small on purpose: covers what a student says to a voice assistant, and nothing fancier.
find_when() also returns the matched words, so the caller can cut them out of a title
("Add GSoC proposal on 30 Mar" -> title "GSoC proposal", date 2027-03-30).
"""
import re
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_PATTERNS = [
    ("iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("dmy", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}(?:,?\s+(\d{{4}}))?\b", re.I)),
    ("mdy", re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I)),
    ("slash", re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b")),            # 12/11 = 12 Nov (Indian order)
    ("rel", re.compile(r"\b(day after tomorrow|tomorrow|today|tonight)\b", re.I)),
    ("in", re.compile(r"\bin\s+(\d{1,3})\s+(day|days|week|weeks)\b", re.I)),
    ("nextweek", re.compile(r"\bnext\s+week\b", re.I)),
    ("weekday", re.compile(r"\b(?:(next|this|on)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)),
]
_TIME = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\bat\s+(\d{1,2}):(\d{2})\b", re.I)


def _future(day: int, month: int, today: date) -> date | None:
    """A date without a year means the next time it comes round (today counts)."""
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= today:
            return d
    return None


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_when(text: str, today: date | None = None) -> tuple[date | None, str, str]:
    """Return (date, time 'HH:MM' or '', the matched words) for the first date in text."""
    today = today or date.today()
    found, words = None, ""
    for kind, pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        g = m.groups()
        if kind == "iso":
            found = _safe(int(g[0]), int(g[1]), int(g[2]))
        elif kind in ("dmy", "mdy"):
            day, mon, year = (g[0], g[1], g[2]) if kind == "dmy" else (g[1], g[0], g[2])
            month = MONTHS[mon[:3].lower()]
            found = _safe(int(year), month, int(day)) if year else _future(int(day), month, today)
        elif kind == "slash":
            found = _safe(int(g[2]), int(g[1]), int(g[0])) if g[2] else _future(int(g[0]), int(g[1]), today)
        elif kind == "rel":
            word = g[0].lower()
            found = today + timedelta(days={"today": 0, "tonight": 0, "tomorrow": 1}.get(word, 2))
        elif kind == "in":
            n = int(g[0])
            found = today + timedelta(days=n * (7 if g[1].lower().startswith("week") else 1))
        elif kind == "nextweek":
            found = today + timedelta(days=7)
        elif kind == "weekday":
            ahead = (DAYS.index(g[1].lower()) - today.weekday()) % 7 or 7  # always a future day
            if (g[0] or "").lower() == "next" and ahead < 7:
                ahead += 7 if ahead <= 2 else 0  # "next Monday" said on Saturday still means the coming Monday
            found = today + timedelta(days=ahead)
        if found:
            words = m.group(0)
            break
    time_str = ""
    t = _TIME.search(text)
    if t:
        if t.group(1):
            hour, minute = int(t.group(1)) % 12, int(t.group(2) or 0)
            hour += 12 if t.group(3).lower() == "pm" else 0
        else:
            hour, minute = int(t.group(4)), int(t.group(5))
        if hour < 24 and minute < 60:
            time_str = f"{hour:02d}:{minute:02d}"
            words = (words + " " + t.group(0)).strip()
            if found is None:
                found = today
    return found, time_str, words


def parse_when(text: str, today: date | None = None) -> tuple[date | None, str]:
    d, t, _ = find_when(text or "", today)
    return d, t


def strip_when(text: str, words: str) -> str:
    """Remove the date words (and a dangling 'on'/'at'/'by') from text."""
    if not words:
        return text.strip()
    out = text
    for part in words.split():
        out = re.sub(rf"\b(on|at|by|for)?\s*{re.escape(part)}\b", " ", out, count=1, flags=re.I)
    return re.sub(r"\s+", " ", out).strip(" ,.-")


def spoken_time(time_str: str) -> str:
    """'14:00' -> '2 pm', '09:30' -> '9:30 am' (no ':00', which voices read out as 'oh oh')."""
    if not time_str:
        return ""
    h, m = map(int, time_str.split(":"))
    return f"{h % 12 or 12}{f':{m:02d}' if m else ''} {'pm' if h >= 12 else 'am'}"


def spoken_day(d: date, today: date | None = None) -> str:
    """'today', 'tomorrow', or 'on Sunday 15 November' (plus the year when it isn't this year).
    Full words, because voices read 'Sun 15 Nov' as 'sun fifteen nov'."""
    today = today or date.today()
    days = (d - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    text = f"on {d.strftime('%A')} {d.day} {d.strftime('%B')}"
    return text + (f" {d.year}" if d.year != today.year else "")


def nice(d: date, time_str: str = "", today: date | None = None) -> str:
    """'Mon 16 Mar 2027' (year only when it isn't this year), plus the time if any."""
    text = d.strftime("%a %d %b") + (d.strftime(" %Y") if d.year != (today or date.today()).year else "")
    if time_str:
        h, m = map(int, time_str.split(":"))
        text += f", {h % 12 or 12}:{m:02d} {'pm' if h >= 12 else 'am'}"
    return text
