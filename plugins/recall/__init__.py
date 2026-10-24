"""Session memory: AIRTAC remembers what each chat was about, and when you start a new chat it brings
up anything relevant you discussed before (like claude-mem, but local and built in).

How it works, all on your PC:
- after a few exchanges, each chat gets a short title and summary. With a model connected it writes a
  one-line summary; with no model it lists the main things you asked. Stored in AIRTAC's database.
- on every new question, the @p.context hook looks for past chats that match what you're asking and
  adds a short "From earlier chats" note to what the model reads, so it can say "last time you were
  working on X". It never pulls in the current chat, and never more than a few lines.

This is about the shape of past chats, not secrets: it stores summaries of your own messages, which
already live in AIRTAC's history. Nothing leaves the PC.
"""
import re
from datetime import datetime

from airtac import db, memory
from airtac.plugin_api import CURRENT_SESSION, Tool, ToolResult, plugin, schema

p = plugin(__name__)
p.sql("""
CREATE TABLE IF NOT EXISTS session_memory (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '',
    turns INTEGER NOT NULL DEFAULT 0,
    updated TEXT NOT NULL DEFAULT ''
);
""")

STOP = set("the a an and or of to in on for with is are was were be been it this that these those my your our "
           "i you we me us he she they them what when where how why who which can could would should do does did "
           "please tell show give make help about into from at as by so if then than me's".split())
MIN_TURNS = 2              # wait for a real conversation before remembering it
MAX_PER_SESSION = 10       # user lines kept per session summary


def _keywords(text: str, n: int = 8) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9+#.-]{2,}", text.lower()) if w not in STOP]
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:n]


def _is_app(session_id: str) -> bool:
    return session_id.startswith("app:")   # app chats have their own page; keep them out of general recall


# ---------------------------------------------------------------- building a session's summary

def update(session_id: str) -> None:
    """Rebuild this chat's title and summary from its user messages. Called after a turn finishes."""
    if _is_app(session_id):
        return
    msgs = [m["content"] for m in memory.session_messages(session_id, 60) if m["role"] == "user"]
    if len(msgs) < MIN_TURNS:
        return
    lines = [re.sub(r"\s+", " ", m).strip()[:160] for m in msgs][-MAX_PER_SESSION:]
    title = _title(lines)
    summary = _summary(lines)
    keywords = " ".join(dict.fromkeys(sum((_keywords(m) for m in lines), [])))[:300]
    with db.connect() as conn:
        conn.execute("INSERT INTO session_memory (session_id, title, summary, keywords, turns, updated) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET title=excluded.title, "
                     "summary=excluded.summary, keywords=excluded.keywords, turns=excluded.turns, updated=excluded.updated",
                     (session_id, title, summary, keywords, len(msgs), datetime.now().strftime("%Y-%m-%d %H:%M")))


def _title(lines: list[str]) -> str:
    return lines[0][:70] if lines else "A chat"


def _summary(lines: list[str]) -> str:
    """One line a model writes, or a plain list of what you asked (so it works with no model)."""
    from airtac import llm
    joined = " / ".join(lines)
    if llm.brain_on():
        try:
            text = llm.ask_text("Summarise what the user worked on in this chat in one short sentence, "
                                "third person, no preamble.", joined, max_tokens=60)
            if text and len(text) < 300:
                return re.sub(r"\s+", " ", text).strip()
        except Exception:
            pass
    return "You asked about: " + "; ".join(lines[:4])


# ---------------------------------------------------------------- bringing past chats into a new one

def related(message: str, session_id: str, limit: int = 2) -> list[dict]:
    want = set(_keywords(message, 10))
    if not want:
        return []
    with db.connect() as conn:
        rows = conn.execute("SELECT session_id, title, summary, keywords, updated FROM session_memory "
                            "WHERE session_id != ? ORDER BY updated DESC LIMIT 100", (session_id,)).fetchall()
    scored = []
    for sid, title, summary, keywords, updated in rows:
        overlap = want & set(keywords.split())
        if overlap:
            scored.append((len(overlap), updated, {"title": title, "summary": summary, "updated": updated}))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r for _, _, r in scored[:limit]]


@p.context
def earlier(message: str, session_id: str = "default") -> dict:
    found = related(message, session_id)
    if not found:
        return {}
    lines = [f"- {r['updated']}: {r['summary']}" for r in found]
    return {"text": "From earlier chats with this user (mention only if useful):\n" + "\n".join(lines),
            "sources": [{"title": r["title"], "where": f"chat on {r['updated']}"} for r in found]}


@p.observe
def after_turn(message: str, tools: list) -> None:
    # The agent calls observers with the message it just handled. Rebuild the summary for the live chat.
    update(CURRENT_SESSION.get())


# ---------------------------------------------------------------- a tool: "what did we talk about?"

def past_chats(query: str = "") -> ToolResult:
    with db.connect() as conn:
        if query.strip():
            like = f"%{query.strip()}%"
            rows = conn.execute("SELECT title, summary, updated FROM session_memory WHERE summary LIKE ? OR keywords LIKE ? "
                               "ORDER BY updated DESC LIMIT 8", (like, like)).fetchall()
        else:
            rows = conn.execute("SELECT title, summary, updated FROM session_memory ORDER BY updated DESC LIMIT 8").fetchall()
    if not rows:
        return ToolResult("I don't have any earlier chats saved yet.")
    spoken = f"Last time, {rows[0][1]}" if not query else f"We talked about that: {rows[0][1]}"
    lines = ["Earlier chats:"] + [f"- {u}: {s}" for _, s, u in rows]
    return ToolResult(spoken[:280], "\n".join(lines))


p.add(Tool("past_chats", "Recall what earlier chats with the user were about (optionally matching a topic).",
           schema({"query": {"type": "string", "description": "A topic to look for (optional)"}}),
           "local", past_chats))
p.mock_rule(40, ("last time", "earlier chat", "talk about", "we discuss", "previous chat", "past chats"), "past_chats",
            lambda text, _m: {"query": ""})
