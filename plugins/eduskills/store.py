"""Your EduSkills virtual internships and your learning log for each one."""
from airtac import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS edu_internships (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    partner TEXT DEFAULT '',
    cohort TEXT DEFAULT '',
    start TEXT DEFAULT '',
    end TEXT DEFAULT '',
    assessment TEXT DEFAULT '',
    status TEXT DEFAULT 'registered',
    modules_total INTEGER DEFAULT 0,
    modules_done INTEGER DEFAULT 0,
    score REAL,
    certificate TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS edu_log (
    id INTEGER PRIMARY KEY,
    internship_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
STATUSES = ("registered", "learning", "assessment", "passed", "certificate", "dropped")
FIELDS = ("domain", "partner", "cohort", "start", "end", "assessment", "status", "modules_total", "modules_done",
          "score", "certificate", "notes")


def add(**f) -> int:
    data = {k: f.get(k) for k in FIELDS if f.get(k) not in (None, "")}
    with db.connect() as conn:
        return conn.execute(f"INSERT INTO edu_internships ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",  # nosec B608 (placeholders only)
                            list(data.values())).lastrowid


def update(internship_id: int, **f) -> None:
    data = {k: v for k, v in f.items() if k in FIELDS and v is not None}
    if not data:
        return
    with db.connect() as conn:
        conn.execute(f"UPDATE edu_internships SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?",  # nosec B608 (placeholders only)
                     [*data.values(), internship_id])


def all_internships() -> list[dict]:
    with db.connect() as conn:
        return db.rows(conn.execute("SELECT * FROM edu_internships ORDER BY (status IN ('certificate', 'dropped')), id DESC"))


def find(text: str = "") -> dict | None:
    """The internship whose domain or partner matches the words, else the newest active one."""
    rows = all_internships()
    words = [w for w in (text or "").lower().split() if len(w) > 2]
    for r in rows:
        hay = f"{r['domain']} {r['partner']} {r['cohort']}".lower()
        if words and any(w in hay for w in words):
            return r
    active = [r for r in rows if r["status"] not in ("certificate", "dropped")]
    return (active or rows or [None])[0]


def add_log(internship_id: int, day: str, text: str) -> int:
    with db.connect() as conn:
        return conn.execute("INSERT INTO edu_log (internship_id, day, text) VALUES (?, ?, ?)",
                            (internship_id, day, text.strip()[:1000])).lastrowid


def log(internship_id: int) -> list[dict]:
    with db.connect() as conn:
        return db.rows(conn.execute("SELECT day, text FROM edu_log WHERE internship_id = ? ORDER BY day, id",
                                    (internship_id,)))
