"""The problems table: practice problems you solved with AIRTAC.
`statement` stays on this laptop only; shareable PDFs use `summary` (two lines in the model's words)."""
from airtac import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT DEFAULT '',
    difficulty TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    language TEXT DEFAULT 'python',
    statement TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    approach TEXT DEFAULT '',
    code TEXT DEFAULT '',
    complexity TEXT DEFAULT '',
    steps_json TEXT DEFAULT '',
    ai_generated INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
FIELDS = ("site", "title", "url", "difficulty", "tags", "language", "statement", "summary", "approach", "code",
          "complexity", "steps_json")


def save_problem(**fields) -> int:
    data = {k: str(fields.get(k) or "") for k in FIELDS}
    data["site"] = data["site"] or "leetcode"
    data["title"] = data["title"] or "Untitled problem"
    with db.connect() as conn:
        return conn.execute(f"INSERT INTO problems ({', '.join(data)}, ai_generated) VALUES "  # nosec B608 (placeholders only)
                            f"({', '.join('?' * len(data))}, 1)", list(data.values())).lastrowid


def list_problems(site: str | None = None, since: str | None = None, tag: str | None = None) -> list[dict]:
    """Oldest first. since: today, week, all or None. Dates use local time (your today, not UTC)."""
    where, args = [], []
    if site and site != "all":
        where.append("site = ?")
        args.append(site)
    if since == "today":
        where.append("date(created_at, 'localtime') = date('now', 'localtime')")
    elif since == "week":
        where.append("date(created_at, 'localtime') >= date('now', 'localtime', '-6 days')")
    if tag:
        where.append("lower(tags) LIKE ?")
        args.append(f"%{tag.lower()}%")
    sql = "SELECT * FROM problems" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    with db.connect() as conn:
        return db.rows(conn.execute(sql, args))


def get_problem(problem_id: int | None = None) -> dict | None:
    """One problem by id, or the newest when no id is given."""
    with db.connect() as conn:
        if problem_id:
            found = db.rows(conn.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)))
        else:
            found = db.rows(conn.execute("SELECT * FROM problems ORDER BY id DESC LIMIT 1"))
    return found[0] if found else None


def set_problem_steps(problem_id: int, steps_json: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE problems SET steps_json = ? WHERE id = ?", (steps_json, problem_id))
