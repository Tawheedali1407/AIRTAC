"""Your day (Part E): the morning briefing, the day planner and the evening check-in, in one place.

BRIEFING. Each morning at the set time (and on start-up if that time was missed), AIRTAC fetches:
"AI today" from Hacker News, arXiv (through the News plugin) and a web search per AI topic, and
"Other industries" from Google News (RSS, the last 24 hours, India edition) per topic and industry you list. Duplicates are
dropped. A brain then writes one line per item in its own words plus why it matters for your plan.
It may only use the items it was given: an item number that wasn't fetched is thrown away, and with no
brain each item shows its headline only. Offline, or when every source fails, it says so: never news
it didn't fetch. Shown as a card on Your day, a line in Home's Today, a notification, and read aloud
if you switch that on.

PLANNER. "Plan my day" proposes today's timetable: your fixed hours (college), timed Schedule items,
then the roadmap's learning blocks (plans/learn_ai.json) filled with this week's DSA, study and lab,
the briefing and a break. Nothing overlaps a fixed item, at most max_study_blocks study blocks, and at
least 10% of the free time stays free. An exam this week, or a Light Mode week, keeps only the DSA block.
Nothing goes into Schedule until you press Accept. The evening check-in (3 quick questions) moves what
you missed to tomorrow; the weekly review counts done and missed.

PROACTIVE RULES. Only two kinds of message come unasked: the briefing and the check-in. At most 3 a day,
never in quiet hours, each kind can be switched off, and one switch turns them all off.
"""
import json
import logging
import re
import time
from datetime import date, datetime, time as dtime, timedelta

from airtac import brains, config, memory
from airtac.plugin_api import CURRENT, Tool, ToolResult, plugin, schema

p = plugin(__name__)
log = logging.getLogger("airtac.daily")
SOURCE = "planner"
DEFAULTS = {
    "briefing_time": "06:15", "checkin_time": "21:30",
    "ai_topics": ["AI models and LLMs", "AI agents"],
    "other_industries": ["tech jobs and internships in India", "semiconductors and chips"],
    "proactive": True, "briefing_on": True, "checkin_on": True, "max_per_day": 3,
    "quiet_start": "22:30", "quiet_end": "06:00", "read_aloud": False,
}
AI_WORDS = re.compile(r"\b(ai|llm|llms|gpt|model|models|agent|agents|neural|transformer|openai|anthropic|gemini|claude|"
                      r"deepseek|machine learning|ml|nvidia|robot|diffusion|rag)\b", re.I)


def settings() -> dict:
    try:
        saved = json.loads(memory.kv_get("daily", "settings") or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def _hhmm(value: str) -> str:
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value)):
        raise ValueError("Times look like 07:00.")
    return value


def save_settings(changes: dict) -> dict:
    s = settings()
    for k, v in changes.items():
        if k not in DEFAULTS or v is None:
            continue
        if k.endswith("_time") or k in ("quiet_start", "quiet_end"):
            v = _hhmm(v)
        elif k in ("ai_topics", "other_industries"):
            v = [str(x).strip()[:60] for x in v if str(x).strip()][:6]
        elif k == "max_per_day":
            v = max(0, min(int(v), 3))          # the rule: never more than 3 a day
        else:
            v = bool(v)
        s[k] = v
    memory.kv_set("daily", "settings", json.dumps(s))
    return s


# ---------------------------------------------------------------- proactive rules

def _quiet(now: datetime, s: dict) -> bool:
    start, end = s["quiet_start"], s["quiet_end"]
    t = now.strftime("%H:%M")
    return (start <= t or t < end) if start > end else (start <= t < end)


def may_send(kind: str, now: datetime) -> bool:
    s = settings()
    if not s["proactive"] or not s.get(f"{kind}_on", True) or _quiet(now, s):
        return False
    sent = int(memory.kv_get("daily", f"sent:{now.date()}") or 0)
    return sent < s["max_per_day"]


def _count(now: datetime) -> None:
    key = f"sent:{now.date()}"
    memory.kv_set("daily", key, str(int(memory.kv_get("daily", key) or 0) + 1))


# ---------------------------------------------------------------- the briefing

def _gather() -> tuple[list[dict], list[dict], list[str]]:
    """(ai items, other items, notes). Only real fetched items: sample or stale copies are left out."""
    from airtac.plugin_api import LOADED
    ai, other, notes, seen = [], [], [], set()

    def add(bucket, title, url, snippet, source):
        if url and url not in seen and title:
            seen.add(url)
            bucket.append({"title": title[:200], "url": url, "snippet": (snippet or "")[:300], "source": source})

    def run_as(plugin_id, fn, *a):
        token = CURRENT.set(plugin_id)
        try:
            return fn(*a)
        finally:
            CURRENT.reset(token)
    if "news" in LOADED:
        from plugins import news
        for sec in (run_as("news", news.research), run_as("news", news.tech)):
            if sec.get("sample") or sec.get("stale") or not sec["items"]:
                notes.append(f"{sec['source']}: {sec.get('note') or 'nothing new'}")
                continue
            for it in sec["items"]:
                if sec["id"] == "research" or AI_WORDS.search(it["title"]):
                    add(ai, it["title"], it["url"], it.get("meta", ""), sec["source"])
    s = settings()
    for bucket, topics in ((ai, s["ai_topics"]), (other, s["other_industries"])):
        for topic in topics:
            rows = run_as(p.id, google_news, topic)
            if rows is None:
                notes.append(f"Google News didn't answer for {topic}")
                continue
            for r in rows[:3]:
                add(bucket, r["title"], r["url"], r["snippet"], r["source"])
    return ai[:10], other[:8], notes


GOOGLE_NEWS = "https://news.google.com/rss/search"


def parse_news_rss(xml: str) -> list[dict]:
    """Items from a Google News RSS search: title (without " - Publisher"), link, publisher, date."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    if not xml or "<!DOCTYPE" in xml or "<!ENTITY" in xml:      # a feed never needs these (XML bombs)
        return []
    try:
        root = ET.fromstring(xml)  # nosec B314 (Python's bundled expat refuses entity expansion attacks; no external entities)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title, link = " ".join((item.findtext("title") or "").split()), (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip() or "Google News"
        if not title or not link.startswith("https://"):
            continue
        if title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "").strftime("%d %b")
        except (TypeError, ValueError):
            when = ""
        out.append({"title": title, "url": link, "source": source, "snippet": f"{source}{', ' + when if when else ''}"})
    return out


TOP_STORIES = "https://news.google.com/rss"     # Google News top stories, India edition (hl/gl/ceid below)
HEADLINES_EVERY = 30 * 60


def top_stories() -> list[dict] | None:
    from airtac.plugin_api import fetch
    token = CURRENT.set(p.id)            # the job may run outside the plugin's context; the manifest allows this site
    try:
        xml = fetch(TOP_STORIES, as_text=True, timeout=12, params={"hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    finally:
        CURRENT.reset(token)
    return None if xml is None else parse_news_rss(xml)


def refresh_headlines(now: float | None = None) -> list[dict]:
    rows = top_stories()
    if rows:
        memory.kv_set("daily", "headlines", json.dumps({"at": now or time.time(), "items": rows[:8]}))
    return rows or []


def headlines(max_age: float = 3 * 3600, now: float | None = None) -> list[dict]:
    """Today's top stories as last fetched (every 30 minutes in the background), or [] when too old: the greeting
    never reads stale news as current."""
    try:
        kept = json.loads(memory.kv_get("daily", "headlines") or "null")
    except ValueError:
        return []
    if not kept or (now or time.time()) - kept.get("at", 0) > max_age:
        return []
    return kept.get("items", [])


def google_news(topic: str) -> list[dict] | None:
    """Today's news on a topic: Google News RSS (personal, non-commercial use), the last 24 hours, India edition."""
    from airtac.plugin_api import fetch
    xml = fetch(GOOGLE_NEWS, as_text=True, timeout=15, params={"q": f"{topic} when:24h", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    return None if xml is None else parse_news_rss(xml)


def _summarise(ai: list[dict], other: list[dict]) -> dict[int, dict]:
    """{index: {"summary", "why", "unsure"}} for the items the brain covered; only given indices are kept."""
    items = ai + other
    if not items or config.LLM_PROVIDER == "mock":
        return {}
    plan_line = ""
    try:
        from plugins import learn
        w = learn.current_week()
        plan_line = learn.week_text(w) if w else ""
    except Exception:
        pass
    listing = "\n".join(f"{i}. {x['title']} | {x['snippet']} | {x['source']}" for i, x in enumerate(items))
    prompt = ("Write a morning briefing for Ali, a 2nd-year AI/ML student. Use ONLY these fetched items; never add news.\n"
              f"His plan this week: {plan_line}\n"
              "Return JSON only: a list of {\"n\": item number, \"summary\": one sentence in your own words (no copying), "
              "\"why\": why it matters for him (half a sentence), \"unsure\": true if the snippet is too thin to be sure}. "
              f"Pick the best 5-7 AI items (numbers 0-{len(ai) - 1}) and up to 3 others.\n\n{listing}")
    try:
        reply = brains.get("general").chat([{"role": "user", "content": prompt}], [], max_tokens=1500).text
        found = json.loads(re.search(r"\[.*\]", reply, re.S).group(0))
    except Exception:
        return {}
    out = {}
    for x in found if isinstance(found, list) else []:
        n = x.get("n") if isinstance(x, dict) else None
        if isinstance(n, int) and 0 <= n < len(items) and x.get("summary"):   # an invented item number is dropped
            out[n] = {"summary": str(x["summary"])[:300], "why": str(x.get("why", ""))[:200], "unsure": bool(x.get("unsure"))}
    return out


SUMMARY_TRIES = 3          # first build + 2 retries, 30 minutes apart, when every brain was busy


def build_briefing(now: datetime | None = None, tries: int = 1) -> dict:
    now = now or datetime.now()
    if config.OFFLINE:
        b = {"date": now.date().isoformat(), "offline": True, "ai": [], "other": [],
             "note": "AIRTAC is offline, so there's no briefing today. It never makes up news."}
    else:
        ai, other, notes = _gather()
        lines = _summarise(ai, other)

        def shape(x, i):
            s = lines.get(i)
            return {**x, "summary": s["summary"] if s else x["title"], "why": s["why"] if s else "",
                    "unsure": s["unsure"] if s else False, "headline_only": s is None}
        ai_out = [shape(x, i) for i, x in enumerate(ai)]
        other_out = [shape(x, len(ai) + i) for i, x in enumerate(other)]
        if lines:   # the brain picked the best ones
            ai_out = [x for i, x in enumerate(ai_out) if i in lines][:7] or ai_out[:7]
            other_out = [x for i, x in enumerate(other_out) if len(ai) + i in lines][:3] or other_out[:3]
        b = {"date": now.date().isoformat(), "offline": False, "ai": ai_out[:7], "other": other_out[:3],
             "note": "; ".join(notes) if notes else "", "made": now.strftime("%H:%M"),
             "summaries": bool(lines), "tries": tries}
        if not lines and (ai_out or other_out):
            busy = "The AI brains were busy, so these are headlines only for now"
            b["note"] = busy + (" (AIRTAC tries again in 30 minutes)." if tries < SUMMARY_TRIES else ". Press Build now later.") \
                + (f" {b['note']}" if b["note"] else "")
        if not ai_out and not other_out:
            b["note"] = "Every news source failed this morning, so there's no briefing. Try Build now later."
    memory.kv_set("daily", f"briefing:{b['date']}", json.dumps(b))
    return b


def briefing(day: date | None = None) -> dict | None:
    try:
        return json.loads(memory.kv_get("daily", f"briefing:{(day or date.today()).isoformat()}") or "null")
    except ValueError:
        return None


# ---------------------------------------------------------------- the planner

def _mins(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _t(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _days_match(spec: str, d: date) -> bool:
    wd = d.weekday()   # Mon 0 .. Sun 6
    return {"mon-fri": wd < 5, "mon-sat": wd < 6, "sat-holiday": wd == 5, "sun": wd == 6, "daily": True}.get(spec, False)


def propose(day: date | None = None) -> dict:
    """Today's timetable, not saved. Each block: start, end, title, kind (fixed | event | study | briefing | break)."""
    from plugins import learn
    from plugins.schedule import store
    day = day or date.today()
    pl = learn.plan()
    hours = pl["hours"]
    w = learn.current_week(day)
    events = store.list_events(start=day.isoformat(), end=day.isoformat(), include_done=False)
    week_end = (day + timedelta(days=6 - day.weekday())).isoformat()
    exam_week = any(e["category"] == "exam" for e in store.list_events(start=day.isoformat(), end=week_end, include_done=False))
    light = exam_week or bool(w and w.get("light"))
    blocks = []
    for b in pl["daily_blocks"]:
        if b.get("fixed") and _days_match(b["days"], day):
            blocks.append({"start": b["start"], "end": b["end"], "title": b["name"], "kind": "fixed", "note": b["what"]})
    for e in events:
        if re.fullmatch(r"\d{2}:\d{2}", e.get("time") or ""):
            end = _t(min(_mins(e["time"]) + 60, 23 * 60 + 59))
            blocks.append({"start": e["time"], "end": end, "title": e["title"], "kind": "event", "note": e["category"]})
    busy = [(_mins(b["start"]), _mins(b["end"])) for b in blocks]
    free_total = _mins(hours["sleep"]) - _mins(hours["wake"]) - sum(e - s for s, e in busy)
    budget = int(free_total * (1 - hours.get("buffer", 0.1)))
    fill = {"DSA": w["dsa"] if w else "", "Maths, ML and CS core": w["prep"] if w else "", "Zero-to-AGI lab": w["lab"] if w else "",
            "Mock + deep build": "Timed mock, then GSoC PRs and from-scratch builds", "Revision + GitHub": "1-7-30 revision and GitHub"}
    wanted = [{"start": "07:30", "end": "07:45", "title": "AI briefing", "kind": "briefing", "note": "read today's briefing"}]
    for b in pl["daily_blocks"]:
        if b.get("fixed") or not _days_match(b["days"], day):
            continue
        if light and b["name"] != "DSA":
            continue
        wanted.append({"start": b["start"], "end": b["end"], "title": b["name"], "kind": "study", "note": fill.get(b["name"]) or b["what"]})
    wanted.append({"start": "17:00", "end": "17:30", "title": "Break: walk, friends or family", "kind": "break", "note": ""})
    used, studies, dropped = 0, 0, []
    for b in sorted(wanted, key=lambda x: x["start"]):
        s, e = _mins(b["start"]), _mins(b["end"])
        clash = any(s < be and bs < e for bs, be in busy)
        if b["kind"] == "study":
            if studies >= hours.get("max_study_blocks", 3):
                dropped.append(f"{b['title']} (max study blocks)")
                continue
        if clash or used + (e - s) > budget:
            dropped.append(f"{b['title']} ({'clashes with a fixed item' if clash else 'keeps 10% free'})")
            continue
        blocks.append(b)
        busy.append((s, e))
        used += e - s
        studies += b["kind"] == "study"
    blocks.sort(key=lambda b: b["start"])
    return {"date": day.isoformat(), "blocks": blocks, "light": light, "exam_week": exam_week, "dropped": dropped,
            "free_minutes": free_total - used, "week": w["week"] if w else None}


def accept(day: date, keep: list[int] | None = None) -> int:
    """Write the proposal's study, briefing and break blocks into Schedule (skipping ones already there)."""
    from plugins.schedule import store
    plan_ = propose(day)
    existing = {(e["title"].lower(), e.get("time")) for e in store.list_events(start=plan_["date"], end=plan_["date"])}
    added = 0
    for i, b in enumerate(plan_["blocks"]):
        if b["kind"] in ("fixed", "event") or (keep is not None and i not in keep):
            continue
        title = f"{b['title']}: {b['note']}"[:120] if b["note"] else b["title"]
        if (title.lower(), b["start"]) in existing:
            continue
        store.add_event(title, plan_["date"], b["start"], "study", f"Until {b['end']}. Planned by Your day.", source=SOURCE)
        added += 1
    return added


def checkin(day: date, done_ids: list[int], feeling: str = "", blocked: str = "") -> dict:
    """Evening check-in: ticked items are done; the rest of today's plan moves to tomorrow, same time."""
    from plugins.schedule import store
    moved = 0
    tomorrow = (day + timedelta(days=1)).isoformat()
    for e in store.list_events(start=day.isoformat(), end=day.isoformat(), include_done=False):
        if e.get("source") != SOURCE:
            continue
        if e["id"] in done_ids:
            store.set_done(e["id"])
        else:
            store.update_event(e["id"], date=tomorrow)
            moved += 1
    memory.kv_set("daily", f"checkin:{day}", json.dumps({"feeling": feeling[:20], "blocked": blocked[:300], "moved": moved}))
    return {"moved": moved, "done": len(done_ids)}


def weekly_review(today: date | None = None) -> dict:
    from plugins.schedule import store
    today = today or date.today()
    rows = [e for e in store.list_events(start=(today - timedelta(days=7)).isoformat(), end=today.isoformat())
            if e.get("source") in (SOURCE, "learn")]
    done = sum(1 for e in rows if e.get("done"))
    return {"planned": len(rows), "done": done, "missed": len(rows) - done,
            "line": f"This week: {done} of {len(rows)} planned blocks done." if rows else "Nothing was planned this week yet."}


# ---------------------------------------------------------------- the morning, the evening

@p.job(60)
def morning_and_evening(now: datetime) -> list[dict]:
    s, out = settings(), []
    today = now.date()
    if s["briefing_on"] and now.strftime("%H:%M") >= s["briefing_time"] and briefing(today) is None:
        b = build_briefing(now)   # also the catch-up when the PC was off at briefing time
        if may_send("briefing", now) and (b["ai"] or b["other"]):
            _count(now)
            out.append({"title": "Your AI briefing is ready", "body": f"{len(b['ai'])} AI stories, {len(b['other'])} from other industries.",
                        "spoken": "Your AI briefing is ready." if s["read_aloud"] else ""})
    if not config.OFFLINE:
        try:
            kept = json.loads(memory.kv_get("daily", "headlines") or "null") or {}
        except ValueError:
            kept = {}
        if time.time() - kept.get("at", 0) > HEADLINES_EVERY:
            try:
                refresh_headlines()           # top stories for the greeting, at most every 30 minutes
            except Exception:                 # a news hiccup must never stop the briefing or the check-in
                log.warning("couldn't refresh the top stories", exc_info=True)
    old = briefing(today)
    if old and not old.get("offline") and old.get("summaries") is False and (old["ai"] or old["other"]) \
            and old.get("tries", 1) < SUMMARY_TRIES and old.get("made") \
            and _mins(now.strftime("%H:%M")) - _mins(old["made"]) >= 30:
        build_briefing(now, tries=old.get("tries", 1) + 1)   # headlines only earlier: try the summaries again
    key = f"checkin_sent:{today}"
    if s["checkin_on"] and now.strftime("%H:%M") >= s["checkin_time"] and not memory.kv_get("daily", key):
        memory.kv_set("daily", key, "1")
        if may_send("checkin", now):
            _count(now)
            out.append({"title": "Evening check-in", "body": "Three quick questions on Your day: what got done, how it went, anything blocking."})
    return out


@p.briefing
def today_line(now: datetime) -> dict | None:
    """Current news in the greeting (Ali, 17-18 Oct): India's top stories right now, then today's AI news. News
    articles come before arXiv papers, which are listed on Your day but aren't "news" to read aloud."""
    top = [x["title"].rstrip(".") for x in headlines()[:3]]
    b = briefing(now.date())
    ai = []
    if b and not b.get("offline"):
        items = b["ai"] + b["other"]
        news_first = [x for x in items if "arxiv.org" not in x.get("url", "")] or items
        ai = [x["summary"].rstrip(".") for x in news_first[:2]]
    if not top and not ai:
        return None
    spoken = (f"Top news: {'. '.join(top)}. " if top else "") + (f"In AI today: {'. '.join(ai)}." if ai else "")
    screen = (f"Top news: {'; '.join(top)}." if top else "") + \
        (f" Your AI briefing has {len(b['ai'])} stories (Your day)." if b and b.get("ai") else "")
    return {"screen": screen.strip(), "spoken": spoken.strip(), "order": 80}


# ---------------------------------------------------------------- chat tools

def plan_today() -> ToolResult:
    pr = propose()
    lines = [f"{b['start']}-{b['end']} {b['title']}" + (f": {b['note']}" if b["note"] else "") for b in pr["blocks"]]
    head = "Light Mode today (exam week)." if pr["exam_week"] else ""
    return ToolResult(f"Here's a plan for today with {len(pr['blocks'])} blocks. Accept it on Your day.",
                      "\n".join(([head] if head else []) + lines + ([f"Left out: {', '.join(pr['dropped'])}"] if pr["dropped"] else [])))


def briefing_tool() -> ToolResult:
    b = briefing() or build_briefing()
    if b.get("offline") or not (b["ai"] or b["other"]):
        return ToolResult(b.get("note") or "No briefing today.")
    text = "\n".join(f"- {x['summary']} ({x['url']})" for x in b["ai"] + b["other"])
    return ToolResult(f"{len(b['ai'])} AI stories today. Details are on screen.", text)


def review_tool() -> ToolResult:
    r = weekly_review()
    return ToolResult(r["line"])


p.add(Tool("plan_my_day", "Propose today's timetable around classes with the roadmap's study blocks (not saved).", schema(), "read", plan_today))
p.add(Tool("ai_briefing", "Today's AI and industry briefing, built only from fetched news with links.", schema(), "read", briefing_tool))
p.add(Tool("plan_weekly_review", "How many planned study blocks were done this week.", schema(), "read", review_tool))
p.mock_rule(0.9, ("plan my day", "today's plan"), "plan_my_day")
p.mock_rule(0.9, ("briefing", "ai news today"), "ai_briefing")
p.mock_rule(0.9, ("weekly review of my plan",), "plan_weekly_review")


@p.dashboard
def page(pg: dict) -> None:
    b = briefing()
    pg["stats"] = [{"label": "Briefing", "value": b["made"] if b and b.get("made") else "not yet"},
                   {"label": "Planned blocks done this week", "value": f"{weekly_review()['done']} / {weekly_review()['planned']}"}]
    pg["daily"] = {"briefing": b, "plan": propose(), "settings": settings(), "review": weekly_review()}


@p.routes
def api(router) -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel, Field

    class Settings(BaseModel):
        briefing_time: str | None = Field(default=None, max_length=5)
        checkin_time: str | None = Field(default=None, max_length=5)
        quiet_start: str | None = Field(default=None, max_length=5)
        quiet_end: str | None = Field(default=None, max_length=5)
        ai_topics: list[str] | None = Field(default=None, max_length=6)
        other_industries: list[str] | None = Field(default=None, max_length=6)
        proactive: bool | None = None
        briefing_on: bool | None = None
        checkin_on: bool | None = None
        read_aloud: bool | None = None
        max_per_day: int | None = None

    class Accept(BaseModel):
        keep: list[int] | None = Field(default=None, max_length=30)

    class CheckIn(BaseModel):
        done: list[int] = Field(default_factory=list, max_length=50)
        feeling: str = Field(default="", max_length=20)
        blocked: str = Field(default="", max_length=300)

    @router.get("")
    def read():
        pg: dict = {}
        page(pg)
        return pg["daily"]

    @router.post("/settings")
    def write(body: Settings):
        try:
            return save_settings(body.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/briefing")
    def build():
        return build_briefing()

    @router.post("/accept")
    def accept_(body: Accept):
        return {"added": accept(date.today(), body.keep)}

    @router.get("/checkin")
    def checkin_items():
        from plugins.schedule import store
        today = date.today().isoformat()
        return {"items": [e for e in store.list_events(start=today, end=today) if e.get("source") == SOURCE]}

    @router.post("/checkin")
    def checkin_(body: CheckIn):
        return checkin(date.today(), body.done, body.feeling, body.blocked)
