"""Research: a split workspace. Your notes (Markdown) on the left; the co-pilot on the right.

The co-pilot works on your notes, or on the part you selected:
    summarise   the main points in a few bullets
    outline     headings and sub-points to structure a write-up
    analyse     claims, gaps, open questions and what to check next
    ask         answer a question using your notes
    sources     search the web for the topic and list links to read (the Web plugin)
The result is Markdown you insert into your notes with one tap. Notes are saved as you type, in
AIRTAC's database, and never leave the PC unless a cloud brain tier is set up ([failover]).
With no model, the co-pilot still works: it picks out sentences and headings itself, and says so.
"""
import re
from collections import Counter
from datetime import datetime

from fastapi import HTTPException
from pydantic import BaseModel, Field

from airtac import config, db, llm, memory
from airtac.plugin_api import plugin

p = plugin(__name__)

p.sql("""
CREATE TABLE IF NOT EXISTS research_notes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'Untitled notes',
    body TEXT NOT NULL DEFAULT '',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
""")

MAX_BODY = 200_000
ACTIONS = ("summarise", "outline", "analyse", "ask", "sources")
PROMPTS = {
    "summarise": "Summarise these research notes in 4 to 7 short Markdown bullets. Keep facts, numbers and names exactly "
                 "as written. Don't add anything that isn't in the notes.",
    "outline": "Turn these research notes into a Markdown outline for a write-up: ## headings with - bullets under each. "
               "Use only what is in the notes; mark gaps as '- (to research: ...)'.",
    "analyse": "Analyse these research notes. Reply in Markdown with three short sections: **Main claims**, "
               "**Gaps and contradictions**, **What to check next**. Be specific and don't invent sources.",
    "ask": "Answer the question in short Markdown. Use, in this order, what is given below: the user's notes, the "
           "attached files, passages from the user's library, and what is known about the user; name which one you "
           "used (file and page when given). If none of them covers it, answer from your own knowledge and start that "
           "part with '_From general knowledge:_'. For an algorithm or data structure, give the idea, the steps and a "
           "small worked example.",
}
STOP = set("the a an and or of to in on for with is are was were be been it this that these those as at by from "
           "into about than then so but not no yes we you they he she i our your their its can could should would "
           "will just also more most very such which who what when where how why there here".split())


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- notes

def list_notes() -> list[dict]:
    with db.connect() as conn:
        return db.rows(conn.execute("SELECT id, title, updated_at, length(body) AS size FROM research_notes "
                                    "ORDER BY updated_at DESC, id DESC"))


def get_note(note_id: int) -> dict | None:
    with db.connect() as conn:
        rows = db.rows(conn.execute("SELECT * FROM research_notes WHERE id = ?", (note_id,)))
    return rows[0] if rows else None


def create_note(title: str = "", body: str = "") -> int:
    with db.connect() as conn:
        return conn.execute("INSERT INTO research_notes (title, body, updated_at) VALUES (?, ?, ?)",
                            ((title or "Untitled notes").strip()[:120], body[:MAX_BODY], _now())).lastrowid


def save_note(note_id: int, title: str | None = None, body: str | None = None) -> bool:
    fields = {}
    if title is not None:
        fields["title"] = title.strip()[:120] or "Untitled notes"
    if body is not None:
        fields["body"] = body[:MAX_BODY]
    if not fields:
        return False
    fields["updated_at"] = _now()
    with db.connect() as conn:
        sql = ", ".join(f"{k} = ?" for k in fields)
        return conn.execute(f"UPDATE research_notes SET {sql} WHERE id = ?", (*fields.values(), note_id)).rowcount == 1  # nosec B608 (placeholders only)


def delete_note(note_id: int) -> bool:
    with db.connect() as conn:
        return conn.execute("DELETE FROM research_notes WHERE id = ?", (note_id,)).rowcount == 1


# ---------------------------------------------------------------- the co-pilot

def sentences(text: str) -> list[str]:
    plain = re.sub(r"```.*?```", " ", text, flags=re.S)
    plain = re.sub(r"^\s*(#+|[-*+]|\d+\.)\s*", "", plain, flags=re.M)
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}|\n(?=\S)", plain)
    return [re.sub(r"\s+", " ", s).strip() for s in parts if len(s.split()) >= 4]


def key_terms(text: str, n: int = 8) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9+#-]{2,}", text.lower()) if w not in STOP]
    return [w for w, _ in Counter(words).most_common(n)]


def offline(action: str, text: str, question: str = "") -> str:
    """A useful answer with no model: pick out the most telling sentences and headings. Labelled."""
    sents = sentences(text)
    terms = key_terms(text)
    score = lambda s: sum(t in s.lower() for t in terms) + (1 if re.search(r"\d", s) else 0)  # noqa: E731
    note = "\n\n_Made without a model (keyword picks). Start Ollama for a real summary._"
    if not sents:
        return "_There's nothing to work with yet. Write or paste some notes first._"
    if action == "summarise":
        best = sorted(sents, key=score, reverse=True)[:5]
        best = [s for s in sents if s in best]   # keep the notes' own order
        return "\n".join(f"- {s}" for s in best) + note
    if action == "outline":
        heads = re.findall(r"^#+\s*(.+)$", text, flags=re.M)
        out = [f"## {h}" for h in heads] or [f"## {t.title()}\n- " + "\n- ".join(
            s for s in sents if t in s.lower())[:600] for t in terms[:4]]
        return "\n".join(out) + note
    if action == "analyse":
        numbers = [s for s in sents if re.search(r"\d", s)][:4]
        questions = [s for s in sents if s.endswith("?")][:4]
        return ("**Main terms:** " + ", ".join(terms) + "\n\n**Sentences with numbers to check:**\n" +
                ("\n".join(f"- {s}" for s in numbers) or "- none") + "\n\n**Open questions in your notes:**\n" +
                ("\n".join(f"- {s}" for s in questions) or "- none") + note)
    if action == "ask":
        words = set(key_terms(question, 6))
        hits = sorted(sents, key=lambda s: sum(w in s.lower() for w in words), reverse=True)[:3]
        hits = [h for h in hits if any(w in h.lower() for w in words)]
        return ("Closest lines in your notes:\n" + "\n".join(f"- {h}" for h in hits) if hits
                else "Your notes don't seem to cover that yet.") + note
    return ""


def find_sources(topic: str) -> str:
    try:
        from plugins.web import search_results
    except Exception:
        return "_Find sources needs the Web plugin, which is off._"
    rows, provider, sample = search_results(topic[:200], 6)
    if not rows:
        return f"_No sources found for {topic}._"
    head = f"### Sources to read: {topic}" + (" (sample results)" if sample else f" (via {provider})")
    return head + "\n" + "\n".join(f"- [{r['title']}]({r['url']}): {r['snippet'][:140]}" for r in rows)


ABOUT_USER = re.compile(r"\b(i|me|my|mine|myself|ali)\b", re.I)


def gather(question: str, attachments: list[int] | None, collection: str = "") -> tuple[str, list[str]]:
    """Everything besides the notes that can answer: attached files, the library (or one collection,
    the "sub-brain"), About you. (text for the model, labels of what was used)."""
    parts, used = [], []
    if attachments:
        try:
            from plugins.attachments import context_for
            found = context_for(attachments, question)
            if found.get("text"):
                parts.append("Attached files:\n" + found["text"])
                used.append("attached files")
        except Exception:
            pass
    if question:
        try:
            from plugins.knowledge import relevant, search
            if collection:     # a chosen sub-brain: its best passages, even weaker matches
                hits = [c for c in search(question, 5, collection=collection)
                        if c["matches"] or (c["cosine"] or 0) >= 0.45]
            else:
                hits = relevant(search(question, 3), len(memory._terms(question)))
            if hits:
                label = f"the \"{collection}\" collection" if collection else "the user's library"
                parts.append(f"From {label}:\n" + "\n\n".join(
                    f"[{c['title']} ({c['file']}{', ' + c['place'] if c['place'] else ''})]\n{c['text'][:700]}" for c in hits))
                used.append(f"{collection} collection" if collection else "your library")
        except Exception:
            pass
        name = (config.USER_NAME or "").lower()
        if ABOUT_USER.search(question) or (name and name in question.lower()):
            about = memory.profile_text()
            facts = memory.recall(question, 8) or memory.recall("", 8)
            if about or facts:
                parts.append(f"About the user ({config.USER_NAME or 'the user'}):\n" + "\n".join(
                    ([about] if about else []) + [f"- {f}" for f in facts]))
                used.append("About you")
    return "\n\n".join(parts), used


def copilot(action: str, text: str, question: str = "", title: str = "", attachments: list[int] | None = None,
            collection: str = "") -> dict:
    text = (text or "").strip()[:12000]
    if action == "sources":
        topic = question.strip() or title.strip() or " ".join(key_terms(text, 4))
        return {"markdown": find_sources(topic) if topic else "_Give a topic to search for._", "model": "web search"}
    extra, used = gather(question if action == "ask" else (question or title), attachments, collection)
    if not text and not extra and action != "ask":
        return {"markdown": "_There's nothing to work with yet. Write or paste some notes, or attach a file._", "model": ""}
    if not llm.brain_on():
        return {"markdown": offline_answer(action, text, question, extra), "model": "keyword picks"}
    content = (f"Notes titled \"{title}\":\n\n{text}" if text else "The user has no notes on this yet.") + \
              (f"\n\n{extra}" if extra else "") + (f"\n\nQuestion: {question}" if action == "ask" else "")
    prompt = PROMPTS[action] if action == "ask" else PROMPTS[action] + " Attached files count as notes."
    if collection and action == "ask":
        prompt += (f" The user chose the \"{collection}\" collection as the sub-brain for this question: answer mainly "
                   "from its passages and cite each one as (file, page). This is for learning and analysis.")
    try:
        answer = llm.ask_text(prompt, content, role="general", max_tokens=700)
    except llm.LLMUnavailable as e:
        return {"markdown": offline_answer(action, text, question, extra) + f"\n\n_{e}_", "model": "keyword picks"}
    from airtac import providers
    return {"markdown": answer.strip() or "_The model gave no answer. Try again._",
            "model": (providers.LAST.get("model") or "local model") + (f" · used {', '.join(used)}" if used else "")}


def offline_answer(action: str, text: str, question: str, extra: str) -> str:
    """No model running: the closest lines from the notes, the files, the library and About you."""
    if action != "ask":
        return offline(action, "\n\n".join(x for x in (text, extra) if x), question)
    found = offline("ask", "\n\n".join(x for x in (text, extra) if x), question)
    if "don't seem to cover" in found or found.startswith("_There's nothing"):
        return ("_No language model is running, so I can only quote your notes, files, library and About you, and "
                "none of them covers this yet. Start Ollama (or add a cloud brain) to get a full answer._")
    return found


# ---------------------------------------------------------------- routes

class NoteIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, max_length=MAX_BODY)


class CopilotIn(BaseModel):
    action: str = Field(max_length=20)
    text: str = Field(default="", max_length=60000)
    question: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=120)
    attachments: list[int] | None = Field(default=None, max_length=10)
    collection: str = Field(default="", max_length=40, pattern=r"^[a-z0-9-]*$")   # the sub-brain (a knowledge shelf)


@p.routes
def api(router) -> None:
    @router.get("/notes")
    def notes():
        rows = list_notes()
        if not rows:  # a first notebook to start in
            create_note("My research notes", "# My research notes\n\nPaste findings here, then ask the co-pilot "
                                             "on the right to summarise, outline or analyse them.\n")
            rows = list_notes()
        return {"notes": rows}

    @router.post("/notes")
    def new(body: NoteIn):
        return get_note(create_note(body.title or "", body.body or ""))

    @router.get("/notes/{note_id}")
    def one(note_id: int):
        note = get_note(note_id)
        if note is None:
            raise HTTPException(404, "No notes with that id.")
        return note

    @router.put("/notes/{note_id}")
    def save(note_id: int, body: NoteIn):
        if not save_note(note_id, body.title, body.body):
            raise HTTPException(404, "No notes with that id.")
        return {"ok": True, "updated_at": get_note(note_id)["updated_at"]}

    @router.delete("/notes/{note_id}")
    def remove(note_id: int):
        if not delete_note(note_id):
            raise HTTPException(404, "No notes with that id.")
        return {"ok": True}

    @router.post("/copilot")
    def ask(body: CopilotIn):
        if body.action not in ACTIONS:
            raise HTTPException(400, "Unknown action.")
        return copilot(body.action, body.text, body.question, body.title, body.attachments, body.collection)

