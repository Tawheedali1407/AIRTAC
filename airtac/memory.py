"""Core memory: chat messages, saved facts, tool runs, notifications and a small key-value store.

Facts are searched with SQLite's FTS5 index when the SQLite build has it, else with LIKE.
Plugins keep their own data in their own tables (see plugins/practice/store.py for an example).
"""
import json
import re

from airtac import db

# Small words that carry no meaning for search ("when is my exam" -> "exam").
_STOPWORDS = set(
    "a an and are about at be can did do does for from have how i is it me my of on "
    "or say said tell that the this to was what whats when where which who you your "
    "remember recall told".split()
)


# ---------------------------------------------------------------- messages

def add_message(session_id: str, role: str, content: str) -> int:
    with db.connect() as conn:
        return conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                            (session_id, role, content)).lastrowid


def last_messages(n: int, session_id: str = "default") -> list[dict]:
    """The last n messages of a session, oldest first, in chat format."""
    with db.connect() as conn:
        found = conn.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                             (session_id, n)).fetchall()
    return [{"role": role, "content": content} for role, content in reversed(found)]


def session_messages(session_id: str, limit: int = 40) -> list[dict]:
    """Both sides of one conversation with times (for an app's chat panel)."""
    with db.connect() as conn:
        found = conn.execute("SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                             (session_id, limit)).fetchall()
    return [{"role": r, "content": c, "at": db.iso(t)} for r, c, t in reversed(found)]


def thread(session_id: str, limit: int = 200) -> list[dict]:
    """One chat for the Chat page: every message with its id, oldest first."""
    with db.connect() as conn:
        found = conn.execute("SELECT id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                             (session_id, max(1, min(limit, 500)))).fetchall()
    return [{"id": i, "role": r, "content": c, "at": db.iso(t)} for i, r, c, t in reversed(found)]


def last_question(session_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT id, content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
                           (session_id,)).fetchone()
    return {"id": row[0], "content": row[1]} if row else None


def question(session_id: str, message_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT id, content FROM messages WHERE session_id = ? AND id = ? AND role = 'user'",
                           (session_id, message_id)).fetchone()
    return {"id": row[0], "content": row[1]} if row else None


def cut_thread(session_id: str, from_id: int) -> int:
    """Regenerate and Edit on the Chat page (only when you press them): the question from_id and everything after it
    in this chat are replaced by the new answer."""
    with db.connect() as conn:
        n = conn.execute("DELETE FROM messages WHERE session_id = ? AND id >= ?", (session_id, from_id)).rowcount
    try:
        from airtac import cleanup
        cleanup._after_change({session_id})   # the chat's summary (session memory) follows
    except Exception:
        pass
    return n


def search_messages(query: str = "", limit: int = 60) -> list[dict]:
    """Your past requests, newest first. query filters with a simple LIKE match."""
    sql, args = "SELECT id, session_id, content, created_at FROM messages WHERE role = 'user'", []
    # Every word must appear, in any order ("exam dbms" finds "When is my DBMS exam?"). % and _ are
    # escaped, so they're searched for as themselves.
    for word in query.split()[:8]:
        sql += " AND content LIKE ? ESCAPE '\\'"
        args.append("%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 200)))
    with db.connect() as conn:
        found = conn.execute(sql, args).fetchall()
    return [{"id": i, "session_id": s, "text": c, "at": db.iso(t)} for i, s, c, t in found]


def activity(days: int = 7) -> list[dict]:
    """Per local day for the last `days` days: how many requests you made."""
    with db.connect() as conn:
        asks = dict(conn.execute(
            "SELECT date(created_at, 'localtime') d, count(*) FROM messages WHERE role = 'user' "
            "AND date(created_at, 'localtime') >= date('now', 'localtime', ?) GROUP BY d", (f"-{days - 1} days",)).fetchall())
        dates = [r[0] for r in conn.execute(
            "WITH RECURSIVE n(i) AS (SELECT 0 UNION ALL SELECT i + 1 FROM n WHERE i < ?) "
            "SELECT date('now', 'localtime', '-' || (? - 1 - i) || ' days') FROM n", (days - 1, days)).fetchall()]
    return [{"date": d, "asks": asks.get(d, 0)} for d in dates]


def you(today: str | None = None) -> dict:
    """Your own numbers for "Your progress": chats this week, the day streak, and schedule tasks.
    Streak: days in a row with at least one question, ending today (or yesterday, so it doesn't
    look broken first thing in the morning before you've asked anything)."""
    with db.connect() as conn:
        days = {r[0] for r in conn.execute(
            "SELECT DISTINCT date(created_at, 'localtime') FROM messages WHERE role = 'user'")}
        today = today or conn.execute("SELECT date('now', 'localtime')").fetchone()[0]
        week = conn.execute(
            "SELECT count(DISTINCT session_id), count(*) FROM messages WHERE role = 'user' "
            "AND date(created_at, 'localtime') > date(?, '-7 days')", (today,)).fetchone()
        try:
            done, total = conn.execute("SELECT COALESCE(SUM(done = 1), 0), count(*) FROM events").fetchone()
        except Exception:      # the schedule plugin is off: no events table
            done, total = 0, 0
    from datetime import date, timedelta
    day = date.fromisoformat(today)
    if day.isoformat() not in days:
        day -= timedelta(days=1)
    streak = 0
    while day.isoformat() in days:
        streak += 1
        day -= timedelta(days=1)
    return {"streak": streak, "asked_today": today in days, "chats_week": week[0], "questions_week": week[1],
            "tasks_done": int(done or 0), "tasks_total": int(total or 0), "ever": bool(days)}


# ---------------------------------------------------------------- facts

# ---------------------------------------------------------------- About you (Settings)
# A few lines about the user that every chat gets, so "who is Ali?" or "plan my week around college"
# has the answer. Kept in AIRTAC's database; edited on the Settings page.
PROFILE_FIELDS = {"name": "Name", "studies": "Studies", "college": "College", "interests": "Interests",
                  "goals": "Goals", "other": "Other details"}


def profile() -> dict:
    try:
        saved = json.loads(kv_get("memory", "profile") or "{}")
    except ValueError:
        saved = {}
    return {k: str(saved.get(k, ""))[:400] for k in PROFILE_FIELDS}


def save_profile(changes: dict) -> dict:
    current = profile()
    for k, v in changes.items():
        if k in PROFILE_FIELDS and v is not None:
            current[k] = " ".join(str(v).split())[:400]
    kv_set("memory", "profile", json.dumps(current))
    return current


def profile_text() -> str:
    """One compact paragraph for the model ("" when nothing is filled in)."""
    p = profile()
    return "; ".join(f"{label}: {p[k]}" for k, label in PROFILE_FIELDS.items() if p[k])[:1200]


def save_fact(text: str) -> None:
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO facts (text) VALUES (?)", (text,))
        if db.USE_FTS:
            # Same id in both tables, so search hits map back to the fact row.
            conn.execute("INSERT INTO facts_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))


def _terms(query: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 1 and w not in _STOPWORDS]


def recall(query: str, k: int = 5) -> list[str]:
    """Up to k saved facts that match the query, best first.
    With no useful search words ("what did I say?") return the latest facts."""
    terms = _terms(query)
    with db.connect() as conn:
        if not terms:
            return [r[0] for r in conn.execute("SELECT text FROM facts ORDER BY id DESC LIMIT ?", (k,)).fetchall()]
        if db.USE_FTS:
            # Quote each word so your text can't break FTS syntax; * allows "exam" -> "exams".
            match = " OR ".join(f'"{t}"*' for t in terms)
            found = conn.execute("SELECT f.text FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                                 "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?", (match, k)).fetchall()
            return [r[0] for r in found]
        where = " OR ".join("text LIKE ?" for _ in terms)
        found = conn.execute(f"SELECT text FROM facts WHERE {where} ORDER BY id DESC LIMIT 50",  # nosec B608 (placeholders only)
                             [f"%{t}%" for t in terms]).fetchall()
    texts = [r[0] for r in found]
    texts.sort(key=lambda t: sum(term in t.lower() for term in terms), reverse=True)
    return texts[:k]


def count(table: str, where: str = "", args: tuple = ()) -> int:
    """Small counting helper for dashboards. `table` is always one of ours, never user input."""
    with db.connect() as conn:
        return conn.execute(f"SELECT count(*) FROM {table}" + (f" WHERE {where}" if where else ""), args).fetchone()[0]  # nosec B608 (placeholders only)


# ---------------------------------------------------------------- tool runs

def log_tool(tool: str, plugin: str = "", ok: bool = True) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO tool_runs (tool, plugin, ok) VALUES (?, ?, ?)", (tool, plugin, int(ok)))


def usage_by_plugin() -> dict:
    """How many times each plugin's tools ran: {"schedule": 12, ...} (for "most used apps first")."""
    with db.connect() as conn:
        return {p: n for p, n in conn.execute("SELECT plugin, count(*) FROM tool_runs GROUP BY plugin").fetchall()}


def plugin_usage(plugin: str) -> dict:
    with db.connect() as conn:
        n, last = conn.execute("SELECT count(*), max(created_at) FROM tool_runs WHERE plugin = ?", (plugin,)).fetchone()
    return {"uses": n, "last": db.iso(last or "")}


# ---------------------------------------------------------------- notifications

def notify(title: str, body: str = "", spoken: str = "", plugin: str = "") -> int:
    with db.connect() as conn:
        return conn.execute("INSERT INTO notifications (plugin, title, body, spoken) VALUES (?, ?, ?, ?)",
                            (plugin, title[:200], body[:1000], spoken[:400])).lastrowid


def notifications_after(after_id: int = 0, limit: int = 20) -> list[dict]:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT * FROM notifications WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)))
    for n in found:
        n["created_at"] = db.iso(n["created_at"])
    return found


def last_notification_id() -> int:
    with db.connect() as conn:
        return conn.execute("SELECT coalesce(max(id), 0) FROM notifications").fetchone()[0]


# ---------------------------------------------------------------- key-value store for plugins

def kv_get(plugin: str, key: str, default: str = "") -> str:
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM kv WHERE plugin = ? AND key = ?", (plugin, key)).fetchone()
    return row[0] if row else default


def kv_set(plugin: str, key: str, value: str) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO kv (plugin, key, value) VALUES (?, ?, ?) "
                     "ON CONFLICT(plugin, key) DO UPDATE SET value = excluded.value", (plugin, key, value))


# ---------------------------------------------------------------- feedback and lessons (how AIRTAC improves)

def add_feedback(message_id: int, good: bool, note: str = "") -> dict:
    """Store a thumbs up/down on an answer. A thumbs down with a correction becomes an active lesson."""
    with db.connect() as conn:
        row = conn.execute("SELECT session_id, content FROM messages WHERE id = ? AND role = 'assistant'",
                           (message_id,)).fetchone()
        if row is None:
            return {"ok": False}
        session, answer = row
        q = conn.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' AND id < ? "
                         "ORDER BY id DESC LIMIT 1", (session, message_id)).fetchone()
        question = q[0] if q else ""
        conn.execute("INSERT INTO feedback (message_id, good, note, question, answer) VALUES (?, ?, ?, ?, ?)",
                     (message_id, int(good), note[:500], question[:500], answer[:1000]))
    lesson = None
    if not good and note.strip():
        lesson = add_lesson(f'When asked "{_clip(question, 120)}": {note.strip()[:300]}', "you", True)
    return {"ok": True, "lesson": lesson}


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def add_lesson(text: str, source: str = "you", active: bool = True) -> int:
    with db.connect() as conn:
        return conn.execute("INSERT INTO lessons (text, source, active) VALUES (?, ?, ?)",
                            (text.strip()[:500], source, int(active))).lastrowid


def list_lessons() -> list[dict]:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT * FROM lessons ORDER BY active DESC, id DESC LIMIT 200"))
    for x in found:
        x["created_at"] = db.iso(x["created_at"])
    return found


def set_lesson(lesson_id: int, active: bool) -> bool:
    with db.connect() as conn:
        return conn.execute("UPDATE lessons SET active = ? WHERE id = ?", (int(active), lesson_id)).rowcount == 1


def delete_lesson(lesson_id: int) -> bool:
    with db.connect() as conn:
        return conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,)).rowcount == 1


def lessons_for(message: str, k: int = 3) -> list[str]:
    """Active lessons that share words with the message, best first. Counted as used."""
    terms = _terms(message)
    if not terms:
        return []
    with db.connect() as conn:
        found = conn.execute("SELECT id, text FROM lessons WHERE active = 1").fetchall()
        scored = sorted(((sum(t in text.lower() for t in terms), i, text) for i, text in found), reverse=True)
        best = [(i, text) for score, i, text in scored if score > 0][:k]
        if best:
            conn.execute(f"UPDATE lessons SET uses = uses + 1 WHERE id IN ({', '.join('?' * len(best))})",  # nosec B608 (placeholders only)
                         [i for i, _ in best])
    return [text for _, text in best]


def feedback_summary(days: int = 7) -> dict:
    with db.connect() as conn:
        good, bad = conn.execute(
            "SELECT coalesce(sum(good), 0), coalesce(sum(1 - good), 0) FROM feedback "
            "WHERE created_at >= datetime('now', ?)", (f"-{days} days",)).fetchone()
        corrections = db.rows(conn.execute(
            "SELECT question, answer, note FROM feedback WHERE good = 0 AND created_at >= datetime('now', ?) "
            "ORDER BY id DESC LIMIT 20", (f"-{days} days",)))
        failures = conn.execute(
            "SELECT tool, count(*) FROM tool_runs WHERE ok = 0 AND created_at >= datetime('now', ?) "
            "GROUP BY tool ORDER BY count(*) DESC LIMIT 5", (f"-{days} days",)).fetchall()
        asks = conn.execute("SELECT count(*) FROM messages WHERE role = 'user' AND created_at >= datetime('now', ?)",
                            (f"-{days} days",)).fetchone()[0]
    return {"good": good, "bad": bad, "asks": asks, "corrections": corrections,
            "tool_failures": [{"tool": t, "count": n} for t, n in failures]}


# ---------------------------------------------------------------- Work mode jobs and skills

def save_job(job: dict) -> None:
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO jobs (id, data, created_at) VALUES (?, ?, ?)",
                     (job["id"], json.dumps(job), job["created_at"]))


def get_job(job_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return json.loads(row[0]) if row else None


def list_jobs(limit: int = 20) -> list[dict]:
    with db.connect() as conn:
        found = conn.execute("SELECT data FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [json.loads(r[0]) for r in found]


def save_skill(name: str, goal: str, steps: list[dict]) -> int:
    with db.connect() as conn:
        return conn.execute("INSERT INTO skills (name, goal, steps) VALUES (?, ?, ?)",
                            (name.strip()[:80], goal.strip()[:1500], json.dumps(steps))).lastrowid


def list_skills() -> list[dict]:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT * FROM skills ORDER BY runs DESC, id DESC LIMIT 50"))
    for s in found:
        s["steps"] = json.loads(s["steps"] or "[]")
    return found


def get_skill(skill_id: int) -> dict | None:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)))
    if not found:
        return None
    found[0]["steps"] = json.loads(found[0]["steps"] or "[]")
    return found[0]


def count_skill_run(skill_id: int) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE skills SET runs = runs + 1 WHERE id = ?", (skill_id,))


def delete_skill(skill_id: int) -> bool:
    with db.connect() as conn:
        return conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,)).rowcount == 1
