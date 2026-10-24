"""Coach: how AIRTAC gets better at helping you, honestly.

AIRTAC doesn't retrain its own model on your laptop (that needs a GPU). It improves in three
ways you can see and undo:
  1. Lessons. Press thumbs-down on an answer and say what was wrong: that becomes a lesson,
     added to the instructions whenever a similar question comes up.
  2. Weekly review. Once a week it looks at what went wrong (your corrections, tools that
     failed) and, with a model running, suggests up to 3 general lessons. Suggestions stay off
     until you approve them on this page: it never changes its own rules behind your back.
  3. Skills. A Work mode job that went well can be saved as a skill and run again in one click.
For the brave: export your good conversations as training data and fine-tune a small model on a
free cloud GPU (see docs/ROADMAP.md, phase 4).
"""
import json
from datetime import datetime, time, timedelta

from airtac import memory
from airtac.llm import ask_json, brain_on
from airtac.plugin_api import Tool, ToolResult, plugin, schema

p = plugin(__name__)
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

REVIEW_PROMPT = (
    "You review an assistant's mistakes from the last week and write at most 3 short, general lessons it should "
    "follow from now on. Each lesson is one sentence starting with a verb, e.g. \"Give dates in the user's time zone.\" "
    "Only lessons the corrections really support. Reply with a JSON list of strings only.")


def review(now: datetime | None = None) -> dict:
    """The week's numbers, plus suggested lessons when a model is running."""
    week = memory.feedback_summary(7)
    suggested = []
    if brain_on() and week["corrections"]:
        notes = "\n".join(f"Q: {c['question'][:200]}\nA: {c['answer'][:200]}\nCorrection: {c['note'][:200]}"
                          for c in week["corrections"])
        data = ask_json(REVIEW_PROMPT, notes)
        for text in (data if isinstance(data, list) else [])[:3]:
            if isinstance(text, str) and 10 < len(text) < 300:
                memory.add_lesson(text, "review", active=False)
                suggested.append(text)
    memory.kv_set(p.id, "last_review", (now or datetime.now()).isoformat(timespec="minutes"))
    return {**week, "suggested": suggested}


def _summary(r: dict) -> str:
    rated = r["good"] + r["bad"]
    text = f"This week you asked {r['asks']} things"
    text += f" and rated {rated} answers: {r['good']} good, {r['bad']} wrong." if rated else "."
    if r["tool_failures"]:
        worst = r["tool_failures"][0]
        text += f" {worst['tool'].replace('_', ' ')} failed {worst['count']} times."
    if r["suggested"]:
        text += f" I suggested {len(r['suggested'])} new lessons; approve them on the Coach page."
    return text


def weekly_review() -> ToolResult:
    r = review()
    lines = [_summary(r)]
    if r["suggested"]:
        lines += ["", "Suggested lessons (off until you approve):"] + [f"- {x}" for x in r["suggested"]]
    if r["tool_failures"]:
        lines += ["", "Tools that failed:"] + [f"- {t['tool']}: {t['count']}" for t in r["tool_failures"]]
    return ToolResult(_summary(r), "\n".join(lines))


def export_training_data() -> ToolResult:
    """Your thumbs-up answers as chat examples (JSONL), for fine-tuning a small model elsewhere."""
    from airtac import db
    with db.connect() as conn:
        rows = conn.execute("SELECT question, answer FROM feedback WHERE good = 1 AND question != '' ORDER BY id").fetchall()
    if not rows:
        return ToolResult("There's nothing to export yet. Give some good answers a thumbs up first.")
    path = p.output_dir() / f"training-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for q, a in rows:
            f.write(json.dumps({"messages": [{"role": "user", "content": q}, {"role": "assistant", "content": a}]}) + "\n")
    return ToolResult(f"I exported {len(rows)} good answers as training examples. The file is in your files.",
                      f"Saved {path.name} ({len(rows)} examples). See docs/ROADMAP.md for how to fine-tune with it.")


p.add(Tool("weekly_review", "Review how the assistant did this week (ratings, corrections, failing tools) and suggest lessons.",
           schema(), "local", weekly_review))
p.add(Tool("export_training_data", "Export the user's thumbs-up answers as a JSONL file for fine-tuning a model elsewhere.",
           schema(), "local", export_training_data))


@p.job(900)
def weekly(now: datetime) -> list[dict]:
    day = str(p.setting("review_day", "Sunday")).capitalize()
    at = time.fromisoformat(str(p.setting("review_time", "20:00")))
    if day not in DAYS or DAYS[now.weekday()] != day:
        return []
    start = datetime.combine(now.date(), at)
    if not start <= now < start + timedelta(hours=3) or memory.kv_get(p.id, "review_day") == now.date().isoformat():
        return []
    memory.kv_set(p.id, "review_day", now.date().isoformat())
    r = review(now)
    return [{"title": "Your weekly AIRTAC review", "body": _summary(r), "spoken": _summary(r)}]


@p.dashboard
def page(pg: dict) -> None:
    week = memory.feedback_summary(7)
    lessons = memory.list_lessons()
    pg["stats"] = [{"label": "Asked this week", "value": week["asks"]},
                   {"label": "Rated good", "value": week["good"]}, {"label": "Rated wrong", "value": week["bad"]},
                   {"label": "Lessons on", "value": sum(1 for x in lessons if x["active"])}]
    if week["tool_failures"]:
        pg["bars"] = {"title": "Tools that failed this week",
                      "items": [{"label": t["tool"], "value": t["count"]} for t in week["tool_failures"]]}
    pg["lessons"] = lessons
    pg["last_review"] = memory.kv_get(p.id, "last_review")


p.mock_rule(28, ("weekly review", "how am i doing", "how are you doing", "review yourself", "self review"), "weekly_review")
p.mock_rule(28.5, ("export training", "training data"), "export_training_data")
