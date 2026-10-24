"""Memory: things you ask AIRTAC to remember, reminders that really go off, and PDFs from text.

Facts live in the core facts table (searched with SQLite's full-text index). The agent reads the
best-matching facts before every answer, so "when is my exam?" works without asking for a tool.
Reminders with a date go on your schedule; with a time, you get a notification at that time.
Passwords and keys are refused: they belong in .env, not in memory.
"""
import re
from datetime import datetime

from airtac import dates, memory, pdfgen, safety
from airtac.plugin_api import Tool, ToolResult, after, capitalize, plugin, schema

p = plugin(__name__)

# Order matters: phrases before single words, so "I am" becomes "you are" not "you am".
_SWAPS = [(r"\bi am\b", "you are"), (r"\bi'm\b", "you're"), (r"\bi was\b", "you were"), (r"\bmyself\b", "yourself"),
          (r"\bmine\b", "yours"), (r"\bmy\b", "your"), (r"\bme\b", "you"), (r"\bi\b", "you")]


def to_second_person(text: str) -> str:
    """'my exam is on 12 Nov' -> 'your exam is on 12 Nov', so replies sound natural."""
    for pattern, repl in _SWAPS:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def _short(text: str, n: int = 150) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."


def memory_save(text: str) -> ToolResult:
    text = text.strip()
    if not text:
        return ToolResult("What should I remember?")
    if safety.looks_like_secret(text):
        return ToolResult("That looks like a password or key, so I won't store it. Keep secrets in your .env file.")
    memory.save_fact(text)
    return ToolResult(f"Got it. I'll remember that {_short(to_second_person(text))}.", f"Saved: {text}")


def memory_recall(query: str) -> ToolResult:
    facts = memory.recall(query, k=5)
    if not facts:
        return ToolResult("I don't have anything saved about that yet.")
    spoken = f"You told me: {_short(to_second_person(facts[0]))}."
    if len(facts) > 1:
        spoken += " There's more on screen."
    return ToolResult(spoken, "Things you told me:\n" + "\n".join(f"- {f}" for f in facts))


def reminder(text: str, when: str = "") -> ToolResult:
    text, when = text.strip(), when.strip()
    if not text:
        return ToolResult("What should I remind you about?")
    memory.save_fact(f"Reminder: {text}" + (f" ({when})" if when else ""))
    day, time_str = dates.parse_when(when)
    if day is None:
        return ToolResult(f"Saved a reminder to {_short(to_second_person(text), 120)}. Tell me a day or time and "
                          "I'll alert you then.", f"Reminder: {text}")
    from plugins.schedule import store  # the schedule plugin keeps dated things and sends the alerts
    store.add_event(capitalize(to_second_person(text)), day.isoformat(), time_str, "reminder")
    at = dates.nice(day, time_str)
    alert = "I'll alert you at that time." if time_str else "It's on your schedule for that day."
    return ToolResult(f"Okay, I'll remind you to {_short(to_second_person(text), 120)} on {at}. {alert}",
                      f"Reminder: {text}\nWhen: {at}")


def make_pdf(title: str, body: str) -> ToolResult:
    title = (title or "Document").strip()[:100]
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "document"
    path = p.output_dir() / f"{slug}-{datetime.now():%Y%m%d-%H%M%S}.pdf"
    pdfgen.render(pdfgen.notes_sections(title, body[:8000]), path)
    return ToolResult(f"I made a PDF called {_short(title, 80)}. It's in your files list.",
                      f"Saved {path.name}\n\n{body[:1200]}")


p.add(Tool("memory_save", "Save a fact the user asks you to remember for later.",
           schema({"text": {"type": "string", "description": "The fact, in the user's words"}}, required=("text",)),
           "local", memory_save))
p.add(Tool("memory_recall", "Look up facts the user asked you to remember earlier.",
           schema({"query": {"type": "string", "description": "What to look for"}}, required=("query",)),
           "local", memory_recall))
p.add(Tool("reminder", "Set a reminder. With a day it goes on the schedule; with a time the user gets an alert then.",
           schema({"text": {"type": "string", "description": "What to be reminded about"},
                   "when": {"type": "string", "description": "When, in the user's words, e.g. tomorrow at 6 pm"}},
                  required=("text",)),
           "local", reminder))
p.add(Tool("make_pdf", "Create a PDF document from a title and body text and add it to the user's files.",
           schema({"title": {"type": "string"}, "body": {"type": "string", "description": "Plain text content"}},
                  required=("title", "body")),
           "local", make_pdf))


@p.dashboard
def page(pg: dict) -> None:
    facts = memory.recall("", k=50)
    reminders = [f for f in facts if f.startswith("Reminder:")]
    pg["stats"] = [{"label": "Things remembered", "value": memory.count("facts")},
                   {"label": "Reminders", "value": len(reminders)}]
    pg["lists"] = [{"title": "Recent", "empty": "Nothing yet. Say: remember my exam is on 12 Nov.",
                    "items": [{"title": f} for f in facts[:10]]}]


@p.progress
def numbers() -> dict:
    return {"order": 60, "counts": [{"label": "Things remembered", "value": memory.count("facts")}]}


# ---------------------------------------------------------------- rules for the no-model mode

STUDY_PLAN = """7-day study plan

Day 1: List every topic in the syllabus and mark the weak ones.
Day 2: Weak topic one. Two focused 50-minute sessions, then a short quiz.
Day 3: Weak topic two, same routine.
Day 4: One past paper under exam timing.
Day 5: Go through every mistake and write a one-page summary per topic.
Day 6: A second past paper, then light revision.
Day 7: Skim the summaries, rest, and sleep early.

Every day: one LeetCode problem to keep coding sharp."""


def _pdf_args(text, messages):
    m = re.search(r"\b(?:make|create|generate|build|write)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?(.*?)\s*\bpdf", text, re.IGNORECASE)
    title = capitalize((m.group(1) if m else "").strip()) or "Notes"
    if "study" in title.lower():
        body = STUDY_PLAN
        facts = [line[2:] for line in messages[0]["content"].splitlines() if line.startswith("- ")]
        if facts:
            body += "\n\nThings you told me:\n" + "\n".join(f"- {f}" for f in facts)
    else:
        body = f"{title}\n\nWithout a model AIRTAC can't write the content. Start Ollama and ask again."
    return {"title": title, "body": body}


def _reminder_args(text, _messages):
    body = after(text, r"\bremind me\b(\s+to)?") or text
    m = re.search(r"\s+(tomorrow.*|tonight|today.*|next \w+.*|on .+|at .+|in \d+ \w+|by .+)$", body, re.IGNORECASE)
    return {"text": (body[: m.start()] if m else body).strip(" .?!"), "when": m.group(1).strip(" .?!") if m else ""}


p.mock_rule(8, ("pdf",), "make_pdf", _pdf_args)
p.mock_rule(12, ("remind me",), "reminder", _reminder_args)
p.mock_rule(13, ("what did i say", "what did i tell", "do you remember", "recall"), "memory_recall",
            lambda t, m: {"query": t})
p.mock_rule(14, ("remember",), "memory_save", lambda t, m: {"text": after(t, r"\bremember\b(\s+that)?") or t})
# General memory questions go last, so "what's my LeetCode profile" reaches LeetCode first.
p.mock_rule(99, ("when is", "when's", "what is my", "what's my", "what was my", "where is my"), "memory_recall",
            lambda t, m: {"query": t})


# ---------------------------------------------------------------- About you (the Settings page)

@p.routes
def api(router) -> None:
    from pydantic import BaseModel, Field

    class About(BaseModel):
        name: str | None = Field(default=None, max_length=400)
        studies: str | None = Field(default=None, max_length=400)
        college: str | None = Field(default=None, max_length=400)
        interests: str | None = Field(default=None, max_length=400)
        goals: str | None = Field(default=None, max_length=400)
        other: str | None = Field(default=None, max_length=400)

    @router.get("/profile")
    def read_profile():
        return {"profile": memory.profile(), "labels": memory.PROFILE_FIELDS}

    @router.post("/profile")
    def write_profile(body: About):
        return {"profile": memory.save_profile(body.model_dump()), "labels": memory.PROFILE_FIELDS}

