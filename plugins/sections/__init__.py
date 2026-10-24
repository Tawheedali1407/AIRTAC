"""Sections (Ali, 18 Oct: "in settings keep an add section place where we could add sections like research section").

A section is a chat with its own setup, made in Settings > Sections:
- a name and an icon (it gets its own entry in the menu),
- instructions the brain follows in that section ("You are my research assistant: cite sources ..."),
- which plugins (tools) it may use (none ticked = AIRTAC picks, as in the normal chat),
- documents it always reads (files added with the paperclip; read and indexed by plugins/attachments).

The Chat page sends section=<id>; airtac/main.py asks get_section() for the instructions and plugins, and
agent.chat reads the documents as attachments. Kept in the kv table (one JSON list), so a backup includes them.
"""
import json
import re
import time

from airtac import memory
from airtac.plugin_api import plugin

p = plugin(__name__)

MAX_SECTIONS = 30
ICONS = ["spark", "steps", "book", "code", "brain", "globe", "library", "paper", "people", "trophy", "work", "music",
         "calendar", "chip", "pin", "question", "tree", "image", "mail", "folder"]
STARTERS = [
    {"name": "Research", "icon": "steps", "prompt": "You are my research assistant. Break the question into parts, "
     "search the web when facts are needed, compare sources, and end with a short list of the sources you used. "
     "Say clearly what is uncertain.", "plugins": ["web", "research", "knowledge"]},
    {"name": "Coding tutor", "icon": "code", "prompt": "You are my coding tutor. Explain the idea first, then the code "
     "(Python unless I say otherwise), then its time and space complexity. Ask me a quick check question at the end.",
     "plugins": []},
    {"name": "Exam prep", "icon": "book", "prompt": "Help me prepare for my exams: short explanations, worked examples, "
     "and 3 practice questions with answers hidden under 'Answers' at the end.", "plugins": ["study", "knowledge"]},
]


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "section"
    slug, n = base, 2
    while slug in taken:
        slug = f"{base[:27]}-{n}"
        n += 1
    return slug


def all_sections() -> list[dict]:
    try:
        found = json.loads(memory.kv_get("sections", "list") or "[]")
    except ValueError:
        found = []
    return found if isinstance(found, list) else []


def _keep(items: list[dict]) -> None:
    memory.kv_set("sections", "list", json.dumps(items))


def get_section(section_id: str) -> dict | None:
    return next((s for s in all_sections() if s.get("id") == section_id), None)


def _clean(body: dict) -> dict:
    from plugins.attachments import get as get_upload
    name = " ".join(str(body.get("name", "")).split())[:40]
    if len(name) < 2:
        raise ValueError("Give the section a name (at least 2 letters).")
    icon = body.get("icon") if body.get("icon") in ICONS else "spark"
    prompt = str(body.get("prompt", "")).strip()[:4000]
    from airtac import plugins as loaded
    known = {x.id for x in loaded.enabled()}
    chosen = [x for x in body.get("plugins") or [] if isinstance(x, str) and x in known][:20]
    docs = []
    for d in (body.get("docs") or [])[:10]:
        try:
            row = get_upload(int(d))
        except (TypeError, ValueError):
            continue
        if row:
            docs.append(row["id"])
    return {"name": name, "icon": icon, "prompt": prompt, "plugins": chosen, "docs": docs}


def add(body: dict) -> dict:
    items = all_sections()
    if len(items) >= MAX_SECTIONS:
        raise ValueError(f"You have {MAX_SECTIONS} sections, the most AIRTAC keeps. Delete one first.")
    fields = _clean(body)
    item = {"id": _slug(fields["name"], {s["id"] for s in items}), **fields, "created": time.time()}
    items.append(item)
    _keep(items)
    return item


def update(section_id: str, body: dict) -> dict:
    items = all_sections()
    for i, s in enumerate(items):
        if s["id"] == section_id:
            items[i] = {**s, **_clean(body)}
            _keep(items)
            return items[i]
    raise KeyError(section_id)


def remove(section_id: str) -> bool:
    """Only when you press Delete. The section's chats stay in History; its documents stay in data/uploads."""
    items = all_sections()
    left = [s for s in items if s["id"] != section_id]
    if len(left) == len(items):
        return False
    _keep(left)
    return True


def documents(section_id: str) -> list[int]:
    s = get_section(section_id)
    return list(s.get("docs") or []) if s else []


def _with_docs(s: dict) -> dict:
    from plugins.attachments import get as get_upload, public
    docs = [public(r) for r in (get_upload(i) for i in s.get("docs") or []) if r]
    return {**s, "documents": [{k: d[k] for k in ("id", "name", "status", "message")} for d in docs]}


@p.routes
def api(router) -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel, Field

    class Section(BaseModel):
        name: str = Field(min_length=1, max_length=40)
        icon: str = Field(default="spark", max_length=20)
        prompt: str = Field(default="", max_length=4000)
        plugins: list[str] = Field(default_factory=list, max_length=40)
        docs: list[int] = Field(default_factory=list, max_length=10)

    @router.get("")
    def listing():
        from airtac import plugins as loaded
        tools = [{"id": x.id, "name": x.name, "icon": x.manifest.get("icon", "puzzle")} for x in loaded.enabled() if x.tools]
        return {"sections": [_with_docs(s) for s in all_sections()], "icons": ICONS, "plugins": tools,
                "starters": STARTERS}

    @router.post("")
    def create(body: Section):
        try:
            return _with_docs(add(body.model_dump()))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post("/{section_id}")
    def change(section_id: str, body: Section):
        try:
            return _with_docs(update(section_id, body.model_dump()))
        except KeyError:
            raise HTTPException(404, "That section doesn't exist any more.")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post("/{section_id}/delete")
    def delete(section_id: str):
        if not remove(section_id):
            raise HTTPException(404, "That section doesn't exist any more.")
        return {"deleted": section_id}
