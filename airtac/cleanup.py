"""Clean up what AIRTAC keeps: chats in History, and the Memory and cache panel.

Everything here runs only when you press a button (History, Settings > Memory and cache); nothing is
deleted on a timer. What can go:
- a question in History (with the answer that followed it), a whole chat, or all of History;
- what AIRTAC remembers: saved facts ("remember that..."), chat summaries (session memory, used for
  "last time you were working on..."), notifications;
- caches: the 5-minute web cache, the brains' model lists, the tool-use counts.
Lessons, skills, the schedule, knowledge, uploads, files and the vault are NOT touched here: they have
their own pages. Deleting a chat also drops its token count, feedback and summary, so nothing of it
lingers in another table.
"""
from airtac import db


def _sessions_of(conn, ids: list[int]) -> set[str]:
    marks = ",".join("?" * len(ids))
    return {r[0] for r in conn.execute(f"SELECT DISTINCT session_id FROM messages WHERE id IN ({marks})", ids)}  # nosec B608 (placeholders only)


def _after_change(sessions: set[str]) -> None:
    """Rebuild (or drop) the summary of each chat that lost messages."""
    try:
        from plugins import recall
    except Exception:
        return
    with db.connect() as conn:
        left = {s for s in sessions if conn.execute("SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (s,)).fetchone()}
        for s in sessions - left:
            conn.execute("DELETE FROM session_memory WHERE session_id = ?", (s,))
            conn.execute("DELETE FROM kv WHERE plugin = 'session_tokens' AND key = ?", (s,))
    for s in left:
        try:
            with db.connect() as conn:
                conn.execute("DELETE FROM session_memory WHERE session_id = ?", (s,))
            recall.update(s)
        except Exception:
            pass


def delete_questions(ids: list[int]) -> int:
    """Delete questions (your messages) and the answer right after each one. Returns how many rows went."""
    ids = [int(i) for i in ids][:500]
    if not ids:
        return 0
    gone = []
    with db.connect() as conn:
        sessions = _sessions_of(conn, ids)
        for mid in ids:
            row = conn.execute("SELECT session_id FROM messages WHERE id = ? AND role = 'user'", (mid,)).fetchone()
            if not row:
                continue
            gone.append(mid)
            nxt = conn.execute("SELECT id, role FROM messages WHERE session_id = ? AND id > ? ORDER BY id LIMIT 1",
                               (row[0], mid)).fetchone()
            if nxt and nxt[1] == "assistant":
                gone.append(nxt[0])
        if gone:
            marks = ",".join("?" * len(gone))
            conn.execute(f"DELETE FROM feedback WHERE message_id IN ({marks})", gone)  # nosec B608 (placeholders only)
            conn.execute(f"DELETE FROM messages WHERE id IN ({marks})", gone)  # nosec B608 (placeholders only)
    _after_change(sessions)
    return len(gone)


def delete_chats(session_ids: list[str]) -> int:
    session_ids = [str(s)[:120] for s in session_ids][:200]
    if not session_ids:
        return 0
    marks = ",".join("?" * len(session_ids))
    with db.connect() as conn:
        conn.execute(f"DELETE FROM feedback WHERE message_id IN (SELECT id FROM messages WHERE session_id IN ({marks}))", session_ids)  # nosec B608 (placeholders only)
        n = conn.execute(f"DELETE FROM messages WHERE session_id IN ({marks})", session_ids).rowcount  # nosec B608 (placeholders only)
        conn.execute(f"DELETE FROM kv WHERE plugin IN ('session_tokens', 'chat_title') AND key IN ({marks})", session_ids)  # nosec B608 (placeholders only)
        try:
            conn.execute(f"DELETE FROM session_memory WHERE session_id IN ({marks})", session_ids)  # nosec B608 (placeholders only)
        except Exception:      # the recall plugin is off: no table
            pass
    return n


def clear_history() -> int:
    with db.connect() as conn:
        sessions = [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM messages")]
    return delete_chats(sessions) if sessions else 0


def chats(limit: int = 100) -> list[dict]:
    """Your chats, newest first: id, title (from session memory, else the first question), questions, last time."""
    with db.connect() as conn:
        rows = conn.execute("SELECT session_id, MIN(id), MAX(created_at), SUM(role = 'user') FROM messages "
                            "GROUP BY session_id ORDER BY MAX(id) DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        out = []
        for sid, first, last, asked in rows:
            title = ""
            named = conn.execute("SELECT value FROM kv WHERE plugin = 'chat_title' AND key = ?", (sid,)).fetchone()
            if named and named[0]:
                out.append({"id": sid, "title": named[0], "questions": int(asked or 0), "at": db.iso(last)})
                continue
            try:
                found = conn.execute("SELECT title FROM session_memory WHERE session_id = ?", (sid,)).fetchone()
                title = found[0] if found else ""
            except Exception:
                pass
            if not title:
                q = conn.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id LIMIT 1",
                                 (sid,)).fetchone()
                title = (q[0] if q else "")[:80]
            out.append({"id": sid, "title": title, "questions": int(asked or 0), "at": db.iso(last)})
    return out


# ---------------------------------------------------------------- Memory and cache

KINDS = {
    "facts": "Saved facts (\"remember that...\")",
    "summaries": "Chat summaries (session memory)",
    "notifications": "Notifications",
    "tool_runs": "Tool-use counts (the numbers on app pages)",
    "web_cache": "Web cache (pages and searches from the last 5 minutes)",
    "model_lists": "Brain model lists (asked again when you open the menu)",
}


def _count(conn, sql: str) -> int:
    try:
        return int(conn.execute(sql).fetchone()[0])
    except Exception:
        return 0


def summary() -> list[dict]:
    from airtac import plugin_api
    with db.connect() as conn:
        counts = {"facts": _count(conn, "SELECT COUNT(*) FROM facts"),
                  "summaries": _count(conn, "SELECT COUNT(*) FROM session_memory"),
                  "notifications": _count(conn, "SELECT COUNT(*) FROM notifications"),
                  "tool_runs": _count(conn, "SELECT COUNT(*) FROM tool_runs"),
                  "model_lists": _count(conn, "SELECT COUNT(*) FROM kv WHERE plugin = 'brain_models'")}
        chats_n = _count(conn, "SELECT COUNT(DISTINCT session_id) FROM messages")
    counts["web_cache"] = len(plugin_api._cache)
    return [{"id": "history", "label": "Chats in History", "count": chats_n, "items": False}] + \
        [{"id": k, "label": v, "count": counts[k], "items": k in ("facts", "summaries", "notifications")} for k, v in KINDS.items()]


def items(kind: str, limit: int = 200) -> list[dict]:
    sql = {"facts": "SELECT id, text, created_at FROM facts ORDER BY id DESC LIMIT ?",
           "summaries": "SELECT session_id, title || CASE WHEN summary != '' THEN ': ' || summary ELSE '' END, updated "
                        "FROM session_memory ORDER BY updated DESC LIMIT ?",
           "notifications": "SELECT id, title || CASE WHEN body != '' THEN ': ' || body ELSE '' END, created_at "
                            "FROM notifications ORDER BY id DESC LIMIT ?"}.get(kind)
    if sql is None:
        raise ValueError("That kind has no list.")
    with db.connect() as conn:
        try:
            rows = conn.execute(sql, (max(1, min(limit, 500)),)).fetchall()
        except Exception:
            rows = []
    return [{"id": str(i), "text": (t or "")[:300], "at": a or ""} for i, t, a in rows]


def delete_item(kind: str, item_id: str) -> bool:
    with db.connect() as conn:
        if kind == "facts":
            n = conn.execute("DELETE FROM facts WHERE id = ?", (int(item_id),)).rowcount
            if db.USE_FTS:
                conn.execute("DELETE FROM facts_fts WHERE rowid = ?", (int(item_id),))
        elif kind == "summaries":
            n = conn.execute("DELETE FROM session_memory WHERE session_id = ?", (item_id[:120],)).rowcount
        elif kind == "notifications":
            n = conn.execute("DELETE FROM notifications WHERE id = ?", (int(item_id),)).rowcount
        else:
            raise ValueError("That kind can't be deleted one by one.")
    return bool(n)


def clear(kind: str) -> int:
    from airtac import plugin_api
    if kind == "history":
        return clear_history()
    if kind == "web_cache":
        n = len(plugin_api._cache)
        plugin_api._cache.clear()
        return n
    table = {"facts": "facts", "summaries": "session_memory", "notifications": "notifications", "tool_runs": "tool_runs"}.get(kind)
    with db.connect() as conn:
        if kind == "model_lists":
            return conn.execute("DELETE FROM kv WHERE plugin = 'brain_models'").rowcount
        if table is None:
            raise ValueError("Unknown kind.")
        n = conn.execute(f"DELETE FROM {table}").rowcount  # nosec B608 (placeholders only)
        if kind == "facts" and db.USE_FTS:
            conn.execute("DELETE FROM facts_fts")
    return n

