"""Study: your syllabus, practice questions made from it, and study PDFs.

Rules: you supply the syllabus and AIRTAC never invents one. Every generated question and answer
is labelled AI-generated. PDFs go to outputs/study/ (never shared unless you share them).
"""
import base64
import io
import json
import logging
import re
from datetime import datetime

from fastapi import HTTPException
from pydantic import BaseModel, Field

from airtac import config, pdfgen
from airtac.llm import ask_json, brain_on
from airtac.plugin_api import SAMPLE_TAG, Tool, ToolResult, plugin, schema, unreachable

from . import store

p = plugin(__name__)
p.sql(store.SCHEMA)
log = logging.getLogger("airtac.study")

MAX_QUESTIONS = 20
AI_LABEL = "AI-generated"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:30] or "notes"


def _unit_name(unit) -> str:
    """'2', 'unit 2', 'Unit-II' -> 'Unit 2' so spoken and stored units match."""
    text = str(unit or "").strip()
    if not text:
        return ""
    roman = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}
    m = re.search(r"(\d+|\b[ivx]+\b)\s*$", text.lower().replace("-", " "))
    if m:
        n = m.group(1)
        return f"Unit {roman.get(n, n)}"
    return text.title()


# ---------------------------------------------------------------- set_syllabus

_UNIT_LINE = re.compile(r"^\s*(unit|module|chapter)\s*[-:.]?\s*([0-9]+|[ivx]+)\b[\s:.\-–]*(.*)$", re.IGNORECASE)


def parse_syllabus(text: str) -> list[tuple[str, str]]:
    """Turn syllabus text into (unit, topic) rows. Lines like 'UNIT-II: Stacks, Queues'
    start a unit; topics are split on commas, semicolons and new lines."""
    rows, unit = [], ""
    for raw in text.splitlines():
        line = raw.strip(" \t•-*·")
        if not line:
            continue
        m = _UNIT_LINE.match(line)
        if m:
            unit = _unit_name(m.group(2))
            line = m.group(3)
        for topic in re.split(r"[,;]|\s–\s", line):
            topic = topic.strip(" .:-")
            if 2 < len(topic) < 120:
                rows.append((unit or "Unit 1", topic))
    return rows


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # imported here so startup stays fast
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:30])


def set_syllabus(subject: str = "", text: str = "", pdf_base64: str = "") -> ToolResult:
    subject = (subject or "").strip()[:60]
    if not subject:
        return ToolResult("Which subject is this syllabus for?")
    if pdf_base64:
        try:
            text = pdf_text(base64.b64decode(pdf_base64))
        except Exception:
            return ToolResult("I couldn't read that PDF. Try pasting the syllabus text instead.", ok=False)
    rows = parse_syllabus(text or "")
    if not rows:
        return ToolResult("I couldn't find any topics. Paste the syllabus with one unit per line.")
    store.save_syllabus(subject, rows)
    units = sorted({u for u, _ in rows})
    screen = f"Syllabus saved for {subject}:\n" + "\n".join(
        f"{u}: " + ", ".join(t for uu, t in rows if uu == u) for u in units)
    return ToolResult(f"Saved the {subject} syllabus: {len(units)} units and {len(rows)} topics.", screen)


# ---------------------------------------------------------------- make_questions

def _syllabus_rows(subject: str, unit: str) -> list[dict]:
    return store.get_syllabus(subject or None, unit or None)


def _questions_result(subject, unit, items, qtype, tag="") -> ToolResult:
    where = f"{unit} of {subject}" if unit else subject
    lines = [f"{len(items)} {qtype} questions · {where} · {AI_LABEL} {tag}".strip()]
    for n, q in enumerate(items, 1):
        lines.append(f"\nQ{n}. {q['question']}")
        for opt in q.get("options") or []:
            lines.append(f"   {opt}")
        lines.append(f"   Answer: {q.get('answer', '')}")
    return ToolResult(f"I made {len(items)} {qtype} questions from {where}. They're on screen, "
                      "and you can ask for a study PDF.", "\n".join(lines))


def _store_questions(subject, unit, items, qtype, difficulty) -> None:
    for q in items:
        q.update({"subject": subject, "unit": unit, "type": qtype, "difficulty": difficulty,
                  "options": json.dumps(q.get("options") or [])})
    store.save_questions(items)
    for q in items:  # back to a list for the screen text
        q["options"] = json.loads(q["options"])


def _clean_args(subject, unit, count, qtype):
    count = max(1, min(int(count or 5), MAX_QUESTIONS))
    qtype = qtype if qtype in ("mcq", "short", "long") else "short"
    return (subject or "").strip(), _unit_name(unit), count, qtype


def make_questions_fixture(subject: str = "", unit: str = "", count: int = 5, type: str = "short",
                           difficulty: str = "medium") -> ToolResult:
    subject, unit, count, qtype = _clean_args(subject, unit, count, type)
    rows = _syllabus_rows(subject, unit)
    if not rows:
        return ToolResult("I don't have a syllabus for that yet. Paste it in the Syllabus box first. "
                          "I never make one up.")
    subject = rows[0]["subject"]
    pool = [q for q in p.sample("qa")["questions"] if not unit or q["unit"].lower() == unit.lower()] \
        or p.sample("qa")["questions"]
    picked = [dict(q) for q in (pool * (count // len(pool) + 1))[:count]]
    _store_questions(subject, unit, picked, qtype, difficulty)
    return _questions_result(subject, unit, picked, qtype, SAMPLE_TAG)


QUESTION_PROMPT = (
    "You write exam practice questions for a B.Tech student. Use ONLY the topics given. "
    "Reply with a JSON list only, no markdown. Each item: "
    '{"question": "...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."] (mcq only), '
    '"answer": "...", "explanation": "one or two sentences", "topic": "one of the given topics"}.')


def make_questions_live(subject: str = "", unit: str = "", count: int = 5, type: str = "short",
                        difficulty: str = "medium") -> ToolResult:
    subject, unit, count, qtype = _clean_args(subject, unit, count, type)
    rows = _syllabus_rows(subject, unit)
    if not rows:
        return ToolResult("I don't have a syllabus for that yet. Paste it in the Syllabus box first. "
                          "I never make one up.")
    subject = rows[0]["subject"]
    topics = ", ".join(r["topic"] for r in rows)
    data = ask_json(QUESTION_PROMPT, role="general", content=f"Subject: {subject}\nUnit: {unit or 'all'}\nTopics: {topics}\n"
                                     f"Make {count} {qtype} questions, difficulty {difficulty}.")
    items = [q for q in (data if isinstance(data, list) else []) if isinstance(q, dict) and q.get("question")]
    if not items:
        return unreachable("the model in the right format")
    items = items[:count]
    _store_questions(subject, unit, items, qtype, difficulty)
    return _questions_result(subject, unit, items, qtype)


# ---------------------------------------------------------------- make_study_pdf

def make_study_pdf(subject: str = "", unit: str = "", mode: str = "questions_answers",
                   include_images: bool = False) -> ToolResult:
    unit = _unit_name(unit)
    mode = mode if mode in ("questions_answers", "answers_only") else "questions_answers"
    tag = ""
    subject = (subject or "").strip() or store.last_question_subject()
    questions = store.list_questions(subject, unit or None) if subject else []
    if not questions and config.OFFLINE:   # the offline tests only
        demo = p.sample("qa")["questions"]
        questions = [q for q in demo if (not subject or q["subject"].lower() == subject.lower())
                     and (not unit or q["unit"].lower() == unit.lower())]
        tag = SAMPLE_TAG
    if not questions:
        return ToolResult("I don't have questions for that yet. Ask me to make some questions first.")
    subject = subject or questions[0]["subject"]
    images = []
    diagrams = config.OUTPUTS_DIR / "diagrams"
    if include_images and diagrams.exists():
        images = sorted(diagrams.glob("*.png"), key=lambda f: f.stat().st_mtime)[-pdfgen.MAX_IMAGES_PER_PDF:]
    path = p.output_dir() / f"study_{_slug(subject)}_{datetime.now():%Y%m%d-%H%M%S}.pdf"
    pdfgen.render(pdfgen.study_sections(subject, unit, questions, mode, images), path, pdfgen.STUDY_FOOTER)
    return ToolResult(f"Your study PDF for {subject} is ready with {len(questions)} questions. It's in your files list.",
                      f"Saved {path.name} {tag}\n{len(questions)} questions, mode: {mode}, images: {len(images)}")



p.add(Tool("set_syllabus", "Store the syllabus the user pasted, split into units and topics.",
              schema({"subject": {"type": "string"}, "text": {"type": "string", "description": "The syllabus text"}},
                     required=("subject", "text")),
              "local", set_syllabus))
p.add(Tool("make_questions", "Make practice exam questions from the user's stored syllabus (refuses if none is stored).",
              schema({"subject": {"type": "string"}, "unit": {"type": "string", "description": "e.g. Unit 2; empty for all"},
                      "count": {"type": "integer", "minimum": 1, "maximum": MAX_QUESTIONS},
                      "type": {"type": "string", "enum": ["mcq", "short", "long"]},
                      "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]}}),
              "local", make_questions_fixture, make_questions_live, ("brain",)))
p.add(Tool("make_study_pdf", "Build a study PDF from the practice questions already made for a subject.",
              schema({"subject": {"type": "string"}, "unit": {"type": "string"},
                      "mode": {"type": "string", "enum": ["questions_answers", "answers_only"]},
                      "include_images": {"type": "boolean"}}),
              "local", make_study_pdf))


# ---------------------------------------------------------------- app page and the Syllabus box

@p.dashboard
def page(pg: dict) -> None:
    rows = store.get_syllabus()
    subjects: dict[str, dict] = {}
    for r in rows:
        s = subjects.setdefault(r["subject"], {"units": set(), "topics": 0})
        s["units"].add(r["unit"])
        s["topics"] += 1
    by_type = {t: store.count("qa", "type = ?", (t,)) for t in ("mcq", "short", "long")}
    pdfs = sorted(p.output_dir().glob("study_*.pdf"), key=lambda f: f.stat().st_mtime, reverse=True)
    pg["stats"] = [{"label": "Subjects", "value": len(subjects)}, {"label": "Questions made", "value": store.count("qa")},
                   {"label": "Study PDFs", "value": len(pdfs)}]
    pg["bars"] = {"title": "Questions by type",
                  "items": [{"label": {"mcq": "MCQ", "short": "Short", "long": "Long"}[t], "value": n} for t, n in by_type.items()]}
    pg["lists"] = [{"title": "Your syllabus", "empty": "No syllabus yet. Add yours and AIRTAC can make questions from it.",
                    "items": [{"title": s, "meta": f"{len(v['units'])} units, {v['topics']} topics"} for s, v in subjects.items()]},
                   {"title": "Study PDFs", "empty": "No study PDFs yet.",
                    "items": [{"title": f.name, "meta": f"{max(1, f.stat().st_size // 1024)} KB", "url": f"/files/study/{f.name}"}
                              for f in pdfs[:6]]}]


@p.progress
def numbers() -> dict:
    return {"order": 40, "counts": [{"label": "Questions made", "value": store.count("qa")}]}


class SyllabusIn(BaseModel):
    subject: str = Field(min_length=1, max_length=60)
    text: str = Field(default="", max_length=30000)
    pdf_base64: str = Field(default="", max_length=14_000_000)


@p.routes
def api(router) -> None:
    @router.post("/syllabus")
    def syllabus(body: SyllabusIn):
        """The Syllabus box sends pasted text or a PDF (as base64, so no upload library is needed)."""
        result = set_syllabus(body.subject, body.text, body.pdf_base64)
        return {"spoken": result.spoken, "screen": result.screen, "ok": result.ok}


# ---------------------------------------------------------------- rules for the no-model mode

def _unit(text: str) -> str:
    m = re.search(r"\bunit\s*[-]?\s*(\d+|[ivx]+)\b", text, re.IGNORECASE)
    return f"Unit {m.group(1)}" if m else ""


def _questions_args(text, _messages):
    low = text.lower()
    m = re.search(r"\b(\d+)\s+(?:\w+\s+)?questions", low)
    qtype = "mcq" if ("mcq" in low or "multiple choice" in low) else "long" if "long" in low else "short"
    difficulty = next((d for d in ("easy", "medium", "hard") if d in low), "medium")
    return {"subject": "", "unit": _unit(text), "count": int(m.group(1)) if m else 5, "type": qtype,
            "difficulty": difficulty}


def _study_pdf_args(text, _messages):
    low = text.lower()
    return {"subject": "", "unit": _unit(text), "mode": "answers_only" if "answers only" in low else "questions_answers",
            "include_images": "image" in low or "diagram" in low}


p.mock_rule(7, ("study pdf",), "make_study_pdf", _study_pdf_args)
p.mock_rule(9, ("questions", "mcq", "mcqs"), "make_questions", _questions_args)
p.mock_rule(11, ("syllabus",), "set_syllabus",
            lambda t, m: {"subject": "", "text": t.split(":", 1)[1] if ":" in t else ""})
