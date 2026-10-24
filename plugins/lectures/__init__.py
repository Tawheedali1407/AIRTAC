"""Study analyzer + lecture voice (Part E).

Input: a file you attached anywhere in AIRTAC (EduSkills outlines, research PDFs, notes; they keep their
"[p. 3]" page marks), or pasted text. Never a page behind a login: you download or paste it yourself.
Output, grounded ONLY in that source:
  summary · key concepts + glossary · a lecture script in a teacher's voice (hook -> big picture ->
  step by step with examples and analogies -> recap) · 5 quiz questions · citations (file, page) ·
  "not in the source" for anything missing.
Mode "conversation": the script is a two-voice talk (Teacher / Student), like an audio overview.
Saved as data/lectures/<id>.json and as Markdown in knowledge/lectures/ (so Knowledge and chats find it).

Play: the page reads it with this PC's own voices (Windows voices run offline), with speed, pause and
resume, and lets you preview three voices. With Piper installed ([voice] tts = "local"), "Save audio"
writes data/lectures/<id>.wav.
"""
import json
import re
import time
from datetime import datetime

from airtac import brains, config
from airtac.plugin_api import Tool, ToolResult, plugin, schema

p = plugin(__name__)
MAX_SOURCE = 24000      # characters of the source sent to the brain (a long PDF: its first part, and it says so)

PROMPT = """You are a patient teacher. Use ONLY the SOURCE below; never add facts that aren't in it.
Return JSON only, with these keys:
"summary": 4-6 sentences;
"concepts": [{{"term": "...", "meaning": "...", "where": "p. N"}}] (5-10 key concepts, a glossary);
"script": {script_rule};
"quiz": [{{"q": "...", "answer": "...", "where": "p. N"}}] (exactly 5);
"missing": topics the title promises that the source doesn't cover ("" if none).
Cite pages with the [p. N] marks in the source (write "p. N"). Plain words for a second-year engineering student.

TITLE: {title}
SOURCE ({note}):
{source}"""
TEACHER = ('a list of {{"speaker": "Teacher", "text": "..."}} paragraphs: a hook, the big picture, the ideas step by step '
           'with examples and analogies, a recap. About 600-900 words in all')
CONVERSATION = ('a list of {{"speaker": "Teacher" or "Student", "text": "..."}} turns: a friendly two-voice talk where '
                'the Student asks what a learner would ask and the Teacher explains with examples; about 700-1000 words')


def folder():
    path = config.DB_PATH.parent / "lectures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _source(upload_id: int | None, text: str) -> tuple[str, str, str]:
    """(title, text, file name) from an attached file or pasted text."""
    if upload_id:
        from plugins import attachments
        row = attachments.get(int(upload_id))
        if not row or row["status"] != "ready" or not row.get("text"):
            raise ValueError("That file isn't ready yet (or has no text). Attach it again.")
        return row["name"].rsplit(".", 1)[0], row["text"], row["name"]
    text = (text or "").strip()
    if len(text) < 200:
        raise ValueError("Paste at least a paragraph of notes (200 characters), or pick an attached file.")
    return "", text, "pasted notes"


def _json(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        raise ValueError("The brain didn't return a lecture. Try again, or pick another brain in the chat menu.")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise ValueError("The brain's answer was cut off. Try a shorter file or a cloud brain.")
    if not isinstance(data.get("script"), list) or not data.get("summary"):
        raise ValueError("The lecture came back incomplete. Try again.")
    data["script"] = [{"speaker": str(x.get("speaker", "Teacher"))[:20], "text": str(x.get("text", ""))[:4000]}
                      for x in data["script"] if isinstance(x, dict) and x.get("text")]
    data["concepts"] = [c for c in data.get("concepts", []) if isinstance(c, dict)][:12]
    data["quiz"] = [q for q in data.get("quiz", []) if isinstance(q, dict)][:5]
    return data


def ask_brain(prompt: str) -> str:
    """The lecture is long writing: one call to the general brain (cloud first if set up), no tools."""
    llm = brains.get("general")
    return llm.chat([{"role": "system", "content": "You write study material as strict JSON."},
                     {"role": "user", "content": prompt}], [], max_tokens=3500).text


def make(upload_id: int | None = None, text: str = "", title: str = "", mode: str = "teacher") -> dict:
    name_title, source, file_name = _source(upload_id, text)
    title = (title or name_title or "My notes").strip()[:100]
    note = f"from {file_name}" + (f", the first {MAX_SOURCE:,} characters of {len(source):,}" if len(source) > MAX_SOURCE else "")
    prompt = PROMPT.format(script_rule=CONVERSATION if mode == "conversation" else TEACHER, title=title, note=note,
                           source=source[:MAX_SOURCE])
    data = _json(ask_brain(prompt))
    lecture_id = f"{datetime.now():%Y%m%d-%H%M%S}"
    lecture = {"id": lecture_id, "title": title, "mode": mode, "file": file_name, "upload_id": upload_id, "made": time.time(),
               "partial": len(source) > MAX_SOURCE, **{k: data.get(k) for k in ("summary", "concepts", "script", "quiz", "missing")}}
    (folder() / f"{lecture_id}.json").write_text(json.dumps(lecture, ensure_ascii=False, indent=1), encoding="utf-8")
    _to_knowledge(lecture)
    return lecture


def _to_knowledge(lec: dict) -> None:
    """A Markdown copy in knowledge/lectures/, so the library and chats can use it."""
    lines = [f"# Lecture: {lec['title']}", f"Source: {lec['file']}", "", "## Summary", lec["summary"] or "", "", "## Key concepts"]
    lines += [f"- **{c.get('term', '')}**: {c.get('meaning', '')} ({c.get('where', '')})" for c in lec["concepts"] or []]
    lines += ["", "## Lecture"] + [f"{x['speaker']}: {x['text']}" for x in lec["script"]]
    lines += ["", "## Quiz"] + [f"{i}. {q.get('q', '')} Answer: {q.get('answer', '')} ({q.get('where', '')})"
                                 for i, q in enumerate(lec["quiz"] or [], 1)]
    if lec.get("missing"):
        lines += ["", f"Not in the source: {lec['missing']}"]
    out = config.KNOWLEDGE_DIR / "lectures"
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9 _-]+", "", lec["title"]).strip()[:60] or "lecture"
    (out / f"{lec['id']} {safe}.md").write_text("\n".join(lines), encoding="utf-8")


def all_lectures() -> list[dict]:
    out = []
    for f in sorted(folder().glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({k: d.get(k) for k in ("id", "title", "mode", "file", "made")} |
                   {"audio": (folder() / f"{d['id']}.wav").exists()})
    return out


def one(lecture_id: str) -> dict:
    if not re.fullmatch(r"\d{8}-\d{6}", lecture_id or ""):
        raise KeyError(lecture_id)
    f = folder() / f"{lecture_id}.json"
    if not f.exists():
        raise KeyError(lecture_id)
    return json.loads(f.read_text(encoding="utf-8"))


def save_audio(lecture_id: str) -> str:
    """WAV with Piper (needs [voice] tts = "local" and a voice file)."""
    from airtac import voice
    if not voice.status()["tts_ready"] or config.TTS != "local":
        raise ValueError("Saving audio needs Piper (see docs/MANUAL.md, Lectures). Play works without it.")
    lec = one(lecture_id)
    wav = voice.synthesize(" ".join(x["text"] for x in lec["script"]))
    path = folder() / f"{lecture_id}.wav"
    path.write_bytes(wav)
    return str(path)


def lecture_tool(upload_id: int = 0, mode: str = "teacher") -> ToolResult:
    if not upload_id:
        from airtac import db
        with db.connect() as conn:
            row = conn.execute("SELECT id FROM uploads WHERE status = 'ready' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return ToolResult("Attach a file first (the paperclip), then ask again.", ok=False)
        upload_id = row[0]
    try:
        lec = make(upload_id, mode=mode)
    except ValueError as e:
        return ToolResult(str(e), ok=False)
    return ToolResult(f"Your lecture on {lec['title']} is ready on the Lectures page.",
                      f"{lec['summary']}\n\nOpen Lectures to play it ({len(lec['script'])} parts, 5 quiz questions).")


p.add(Tool("make_lecture", "Make a lecture (summary, glossary, teacher script, quiz with page citations) from an attached file.",
           schema({"upload_id": {"type": "integer"}, "mode": {"type": "string", "enum": ["teacher", "conversation"]}}),
           "local", lecture_tool))
p.mock_rule(0.9, ("make a lecture", "lecture from"), "make_lecture")


@p.dashboard
def page(pg: dict) -> None:
    items = all_lectures()
    pg["stats"] = [{"label": "Lectures", "value": len(items)}]
    from airtac import db
    try:
        with db.connect() as conn:
            files = [{"id": i, "name": n, "section": s} for i, n, s in conn.execute(
                "SELECT id, name, section FROM uploads WHERE status = 'ready' ORDER BY id DESC LIMIT 40")]
    except Exception:
        files = []
    pg["lectures"] = {"items": items, "files": files, "piper": config.TTS == "local"}


@p.routes
def api(router) -> None:
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field

    class Make(BaseModel):
        upload_id: int | None = None
        text: str = Field(default="", max_length=60000)
        title: str = Field(default="", max_length=100)
        mode: str = Field(default="teacher", pattern="^(teacher|conversation)$")

    @router.get("")
    def list_():
        pg: dict = {}
        page(pg)
        return pg["lectures"]

    @router.post("")
    def create(body: Make):
        try:
            return make(body.upload_id, body.text, body.title, body.mode)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/{lecture_id}")
    def read(lecture_id: str):
        try:
            return one(lecture_id)
        except KeyError:
            raise HTTPException(404, "No such lecture.")

    @router.post("/{lecture_id}/audio")
    def audio(lecture_id: str):
        try:
            return {"file": save_audio(lecture_id)}
        except KeyError:
            raise HTTPException(404, "No such lecture.")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/{lecture_id}/audio")
    def play(lecture_id: str):
        try:
            one(lecture_id)
        except KeyError:
            raise HTTPException(404, "No such lecture.")
        f = folder() / f"{lecture_id}.wav"
        if not f.exists():
            raise HTTPException(404, "No saved audio for this lecture.")
        return FileResponse(f, media_type="audio/wav")

    @router.post("/{lecture_id}/delete")
    def delete(lecture_id: str):
        try:
            one(lecture_id)
        except KeyError:
            raise HTTPException(404, "No such lecture.")
        for ext in ("json", "wav"):
            f = folder() / f"{lecture_id}.{ext}"
            if f.exists():
                f.unlink()
        for f in (config.KNOWLEDGE_DIR / "lectures").glob(f"{lecture_id} *.md"):
            f.unlink()           # its copy in the library goes too
        return {"ok": True}
