"""Become an AI expert: Ali's learning plan (plans/learn_ai.json, from his AGI roadmap) inside AIRTAC.

- The plan file is editable: stages (goals, free resources, practice, checkpoint), the week-by-week items
  (DSA, prep topic, Zero-to-AGI lab, mocks, Light Mode weeks), the daily blocks and his hours.
- "Add to Schedule" writes the lab item of each coming week, the mocks and the kickoff as Schedule items
  (category study, source "learn"), skipping ones already there. The DSA and prep topics came in with the
  Summer 2027 prep plan, so they aren't added twice.
- "I fell behind" moves every unfinished future plan item by a week (or more): re-planning, no guilt.
- A 10-question level check suggests the stage to start from.
- Every chat that's about studying gets one line: this week's items (the persona "remembers the goals").
The planner (plugins/planner) reads daily_blocks and hours from the same file.
"""
import json
from datetime import date, datetime, timedelta

from airtac import config, memory
from airtac.plugin_api import Tool, ToolResult, plugin, schema

p = plugin(__name__)
SOURCE = "learn"
STUDY_WORDS = ("study", "learn", "roadmap", "plan", "week", "today", "focus", "dsa", "gsoc", "mock", "lab", "prep",
               "internship", "agi", "exam", "schedule", "revise", "revision")


def plan() -> dict:
    return json.loads((config.ROOT / "plans" / "learn_ai.json").read_text(encoding="utf-8"))


def _shift() -> int:
    return int(memory.kv_get("learn", "shift_days") or 0)


def _done() -> set[int]:
    try:
        return set(json.loads(memory.kv_get("learn", "done") or "[]"))
    except ValueError:
        return set()


def week_start(w: dict) -> date:
    return date.fromisoformat(w["start"]) + timedelta(days=_shift())


def current_week(today: date | None = None) -> dict | None:
    today = today or date.today()
    weeks = plan()["weeks"]
    now = [w for w in weeks if week_start(w) <= today < week_start(w) + timedelta(days=7)]
    if now:
        return now[0]
    later = [w for w in weeks if week_start(w) > today]
    return later[0] if later else None


def stage_of(week: int) -> dict | None:
    found = [s for s in plan()["stages"] if s["weeks"][0] <= week <= s["weeks"][1]]
    return found[-1] if found else None


def week_text(w: dict) -> str:
    if w.get("light"):
        head = f"Week {w['week']} (from {week_start(w):%d %b}, Light Mode)"
    else:
        head = f"Week {w['week']} (from {week_start(w):%d %b})"
    parts = [f"DSA: {w['dsa']}", f"study: {w['prep']}"] + ([f"lab: {w['lab']}"] if w.get("lab") else [])
    if w.get("mock"):
        parts.append(f"{w['mock']['title']} on {date.fromisoformat(w['mock']['date']) + timedelta(days=_shift()):%a %d %b}")
    return f"{head}: " + "; ".join(parts) + "."


# ---------------------------------------------------------------- into Schedule, and re-planning

def to_schedule(today: date | None = None) -> dict:
    """Add the coming weeks' lab items, mocks and the kickoff to Schedule. Returns {added, skipped}."""
    from plugins.schedule import store
    today = today or date.today()
    pl, added, skipped = plan(), 0, 0
    items = []
    k = pl["kickoff"]
    items.append((k["date"], "AI roadmap kickoff", "; ".join(k["tasks"])))
    for w in pl["weeks"]:
        start = week_start(w)
        if w.get("lab"):
            items.append((start.isoformat(), f"AGI lab, week {w['week']}: {w['lab']}"[:120], f"{pl['name']}. Study: {w['prep']}"))
        if w.get("mock"):
            d = date.fromisoformat(w["mock"]["date"]) + timedelta(days=_shift())
            items.append((d.isoformat(), w["mock"]["title"][:120], f"{pl['name']}: timed mock, then a 30-minute error review"))
    for day, title, notes in items:
        if date.fromisoformat(day) < today:
            continue
        same_day = store.list_events(start=day, end=day)
        mock_there = title.lower().startswith("mock") and any("mock" in e["title"].lower() for e in same_day)
        if mock_there or any(e["title"].lower() == title.lower() for e in same_day):
            skipped += 1
            continue
        store.add_event(title, day, "", "study", notes, source=SOURCE)
        added += 1
    return {"added": added, "skipped": skipped}


def replan(days: int = 7, today: date | None = None) -> int:
    """Move every unfinished plan item from today on by `days` (and the plan's dates with them)."""
    from plugins.schedule import store
    today = today or date.today()
    moved = 0
    for e in store.list_events(start=today.isoformat(), include_done=False):
        if e.get("source") == SOURCE:
            store.update_event(e["id"], date=(date.fromisoformat(e["date"]) + timedelta(days=days)).isoformat())
            moved += 1
    memory.kv_set("learn", "shift_days", str(_shift() + days))
    return moved


def mark_done(week: int, done: bool = True) -> list[int]:
    d = _done()
    (d.add if done else d.discard)(int(week))
    memory.kv_set("learn", "done", json.dumps(sorted(d)))
    return sorted(d)


def level_check(answers: list[int]) -> dict:
    """Score the 10-question check; the first stage with a wrong answer is where to start."""
    qs = plan()["level_check"]
    right = [i < len(answers) and answers[i] == q["answer"] for i, q in enumerate(qs)]
    weak = sorted({q["stage"] for q, ok in zip(qs, right) if not ok})
    start = weak[0] if weak else 7
    stage = next(s for s in plan()["stages"] if s["id"] == start)
    result = {"score": sum(right), "of": len(qs), "start_stage": start, "stage_title": stage["title"], "weak_stages": weak,
              "at": datetime.now().strftime("%d %b %Y")}
    memory.kv_set("learn", "level", json.dumps(result))
    return result


# ---------------------------------------------------------------- chat: tools and context

def this_week() -> ToolResult:
    w = current_week()
    if w is None:
        return ToolResult("Your roadmap's weekly plan is finished. Time for Year 3: see docs/ROADMAP_AGI.md.")
    s = stage_of(w["week"])
    text = week_text(w)
    return ToolResult(f"This week: {w['lab'] or w['prep']}. Details are on screen.",
                      text + (f"\nStage {s['id']}: {s['title']}. Checkpoint: {s['checkpoint']}." if s else ""))


def add_plan() -> ToolResult:
    r = to_schedule()
    return ToolResult(f"Added {r['added']} roadmap items to your schedule" + (f", {r['skipped']} were already there." if r["skipped"] else "."))


def behind(weeks: int = 1) -> ToolResult:
    weeks = max(1, min(int(weeks or 1), 8))
    n = replan(7 * weeks)
    return ToolResult(f"No problem. I moved {n} unfinished roadmap items by {weeks} week{'s' if weeks > 1 else ''}. "
                      "Pick up from where you are.")


p.add(Tool("roadmap_this_week", "What's on Ali's AI roadmap this week (DSA, study topic, lab, mocks).", schema(), "read", this_week))
p.add(Tool("roadmap_to_schedule", "Add the AI roadmap's coming lab items and mocks to the schedule.", schema(), "write", add_plan,
           summarize=lambda a: "Add the AI roadmap's coming weekly labs and mocks to your Schedule"))
p.add(Tool("roadmap_fell_behind", "Move every unfinished AI roadmap item forward by some weeks.",
           schema({"weeks": {"type": "integer", "minimum": 1, "maximum": 8}}), "write", behind,
           summarize=lambda a: f"Move your unfinished roadmap items by {a.get('weeks', 1)} week(s)"))
p.mock_rule(0.8, ("roadmap",), "roadmap_this_week")
p.mock_rule(0.9, ("roadmap to the schedule", "roadmap to my schedule"), "roadmap_to_schedule")
p.mock_rule(0.9, ("fell behind", "move my plan"), "roadmap_fell_behind")


@p.context
def goals(message: str) -> dict | None:
    if not any(word in message.lower() for word in STUDY_WORDS):
        return None
    w = current_week()
    if w is None:
        return None
    return {"text": f"Ali's AI roadmap ({plan()['name']}), this week. {week_text(w)}"}


@p.dashboard
def page(pg: dict) -> None:
    pl, w = plan(), current_week()
    done = _done()
    try:
        level = json.loads(memory.kv_get("learn", "level") or "null")
    except ValueError:
        level = None
    pg["stats"] = [{"label": "Week", "value": w["week"] if w else "done"},
                   {"label": "Weeks done", "value": f"{len(done)} / {len(pl['weeks'])}"},
                   {"label": "Level check", "value": f"{level['score']}/{level['of']}" if level else "not taken"}]
    pg["learn"] = {"plan": pl, "current": w["week"] if w else None, "done": sorted(done), "level": level,
                   "shift_days": _shift(), "this_week": week_text(w) if w else "",
                   "starts": {x["week"]: week_start(x).isoformat() for x in pl["weeks"]}}


@p.routes
def api(router) -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel, Field

    class Answers(BaseModel):
        answers: list[int] = Field(max_length=20)

    class Weeks(BaseModel):
        weeks: int = Field(default=1, ge=1, le=8)

    class Done(BaseModel):
        week: int = Field(ge=1, le=60)
        done: bool = True

    @router.get("")
    def read():
        pg: dict = {}
        page(pg)
        return pg["learn"]

    @router.post("/level")
    def level(body: Answers):
        return level_check(body.answers)

    @router.post("/schedule")
    def schedule():
        return to_schedule()

    @router.post("/behind")
    def fell_behind(body: Weeks):
        return {"moved": replan(7 * body.weeks), "shift_days": _shift()}

    @router.post("/done")
    def done(body: Done):
        if not any(w["week"] == body.week for w in plan()["weeks"]):
            raise HTTPException(404, "No such week in the plan.")
        return {"done": mark_done(body.week, body.done)}
