"""Search everything (Ctrl+K): one query over your chats, research notes, knowledge library, schedule,
files, apps and settings. Read only, nothing leaves this PC, and every match is a plain word match
(all words must appear), so it's fast and works without a model.

    GET /api/plugins/finder?q=dbms exam&kinds=chats,schedule
"""
from airtac import config, db, memory
from airtac.plugin_api import plugin

p = plugin(__name__)
KINDS = ("chats", "notes", "knowledge", "schedule", "files", "apps", "settings")

# The Settings page and its neighbours, so "theme" or "token warning" finds the right place.
SETTINGS = [
    ("Theme", "colours dark light contrast crimson day arc gilded look", "settings", "set-themes"),
    ("Open social sites in", "browser chrome default social apps", "settings", "set-browser"),
    ("Default search engine", "google bing duckduckgo brave perplexity web search engine", "settings", "set-engine"),
    ("Token warning", "tokens limit warning session cost", "settings", "set-limit"),
    ("Default brain", "brain model llm ollama cloud failover", "settings", "set-brain"),
    ("Master passcode", "passwords vault passcode change lock", "settings", "set-pw-change"),
    ("APIs and keys", "api key connections tavily github nebius groq google", "connections", ""),
    ("Brains and plugins", "plugins brains enable disable", "plugins", ""),
    ("AIRTAC doctor", "doctor check health problems server", "settings", "airtac-doctor"),
    ("Dev tools", "developer tools install python node git", "devtools", ""),
]


def _has_all(text: str, words: list[str]) -> bool:
    low = text.lower()
    return all(w in low for w in words)


def _snippet(text: str, words: list[str], width: int = 120) -> str:
    low = text.lower()
    at = min((low.find(w) for w in words if w in low), default=0)
    start = max(0, at - 30)
    return ("..." if start else "") + " ".join(text[start:start + width].split()) + ("..." if len(text) > start + width else "")


def chats(words, limit):
    rows = memory.search_messages(" ".join(words), limit)
    return [{"kind": "chats", "title": r["text"][:90], "snippet": "", "at": r["at"], "go": {"view": "history", "q": " ".join(words)}}
            for r in rows]


def _table_search(sql_from: str, cols: tuple, words, limit):
    sql = f"SELECT * FROM {sql_from} WHERE 1=1"  # nosec B608 (placeholders only)
    args = []
    for w in words:
        sql += " AND (" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in cols) + ")"
        like = "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like] * len(cols)
    with db.connect() as conn:
        try:
            return db.rows(conn.execute(sql + f" ORDER BY rowid DESC LIMIT {int(limit)}", args))
        except Exception:   # that plugin's table doesn't exist yet
            return []


def notes(words, limit):
    return [{"kind": "notes", "title": r["title"], "snippet": _snippet(r["body"] or "", words), "at": r.get("updated_at", ""),
             "go": {"view": "research", "note": r["id"]}} for r in _table_search("research_notes", ("title", "body"), words, limit)]


def schedule(words, limit):
    return [{"kind": "schedule", "title": r["title"], "snippet": f"{r['date']} {r.get('time') or ''}".strip()
             + (f" · {r['notes'][:80]}" if r.get("notes") else ""), "at": r["date"], "go": {"view": "app-schedule"}}
            for r in _table_search("events", ("title", "notes", "category"), words, limit)]


def knowledge(words, limit):
    try:
        from plugins.knowledge import store
        found = store.chunks([i for i, _ in store.keyword_hits(words, limit)])
    except Exception:
        return []
    return [{"kind": "knowledge", "title": c.get("title") or c.get("path", "Library"), "snippet": _snippet(c.get("text", ""), words),
             "go": {"view": "app-knowledge"}} for c in found[:limit]]


def files(words, limit):
    if not config.OUTPUTS_DIR.exists():
        return []
    out = []
    for f in sorted(config.OUTPUTS_DIR.rglob("*"), key=lambda f: -f.stat().st_mtime):
        if f.is_file() and _has_all(f.name, words):
            out.append({"kind": "files", "title": f.name, "snippet": f.parent.name,
                        "go": {"url": "/files/" + f.relative_to(config.OUTPUTS_DIR).as_posix()}})
        if len(out) >= limit:
            break
    return out


def apps(words, limit):
    from plugins import desktop
    out = []
    for a in desktop.all_apps():
        if _has_all(f"{a['name']} {a.get('about', '')} {a['section']}", words):
            name = a["name"].lower()
            rank = 0 if name.startswith(words[0]) else 1 if _has_all(name, words) else 2   # name matches first
            out.append((rank, {"kind": "apps", "title": a["name"], "snippet": a.get("about", ""), "logo": a.get("logo") or a.get("favicon", ""),
                               "go": {"app": a["id"], "open_as": a["open_as"], "url": a.get("url", ""), "page": a.get("app", "")}}))
    return [r for _, r in sorted(out, key=lambda x: x[0])][:limit]


def settings(words, limit):
    return [{"kind": "settings", "title": name, "snippet": "Settings" if view == "settings" else view.title(),
             "go": {"view": view, "focus": focus}} for name, keys, view, focus in SETTINGS if _has_all(f"{name} {keys}", words)][:limit]


SEARCHERS = {"chats": chats, "notes": notes, "knowledge": knowledge, "schedule": schedule, "files": files,
             "apps": apps, "settings": settings}


def search(q: str, kinds: list[str] | None = None, limit: int = 6) -> dict:
    words = [w.lower() for w in q.split()[:8] if w.strip()]
    if not words:
        return {"query": q, "results": [], "counts": {}}
    out, counts = [], {}
    for kind in kinds or KINDS:
        try:
            found = SEARCHERS[kind](words, limit)
        except Exception:   # one store failing shouldn't empty the palette
            found = []
        counts[kind] = len(found)
        out += found
    return {"query": q, "results": out, "counts": counts}


@p.routes
def api(router) -> None:
    from fastapi import HTTPException

    @router.get("")
    def find(q: str = "", kinds: str = "", limit: int = 6):
        chosen = [k for k in kinds.split(",") if k] or None
        if chosen and any(k not in KINDS for k in chosen):
            raise HTTPException(400, "Unknown kind of result.")
        return search(q[:200], chosen, max(1, min(limit, 20)))
