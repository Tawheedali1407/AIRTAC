"""Your syllabus (one row per topic) and the practice questions made from it."""
from airtac import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS syllabus (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    unit TEXT NOT NULL,
    topic TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qa (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    unit TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    type TEXT DEFAULT 'short',
    difficulty TEXT DEFAULT '',
    question TEXT NOT NULL,
    options TEXT DEFAULT '',
    answer TEXT DEFAULT '',
    explanation TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
QA_FIELDS = ("subject", "unit", "topic", "type", "difficulty", "question", "options", "answer", "explanation")


def save_syllabus(subject: str, rows: list[tuple[str, str]]) -> int:
    """Replace the stored syllabus for one subject. rows = [(unit, topic), ...]."""
    with db.connect() as conn:
        conn.execute("DELETE FROM syllabus WHERE lower(subject) = lower(?)", (subject,))
        conn.executemany("INSERT INTO syllabus (subject, unit, topic) VALUES (?, ?, ?)",
                         [(subject, unit, topic) for unit, topic in rows])
    return len(rows)


def get_syllabus(subject: str | None = None, unit: str | None = None) -> list[dict]:
    where, args = [], []
    if subject:
        where.append("lower(subject) = lower(?)")
        args.append(subject)
    if unit:
        where.append("lower(unit) = lower(?)")
        args.append(unit)
    sql = "SELECT subject, unit, topic FROM syllabus" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    with db.connect() as conn:
        return db.rows(conn.execute(sql, args))


def save_questions(items: list[dict]) -> int:
    with db.connect() as conn:
        conn.executemany(f"INSERT INTO qa ({', '.join(QA_FIELDS)}) VALUES ({', '.join('?' * len(QA_FIELDS))})",  # nosec B608 (placeholders only)
                         [[str(item.get(k) or "") for k in QA_FIELDS] for item in items])
    return len(items)


def list_questions(subject: str, unit: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM qa WHERE lower(subject) = lower(?)", [subject]
    if unit:
        sql += " AND lower(unit) = lower(?)"
        args.append(unit)
    with db.connect() as conn:
        return db.rows(conn.execute(sql + " ORDER BY id", args))


def last_question_subject() -> str:
    """Subject of the newest question, so "make a study PDF" works without naming it."""
    with db.connect() as conn:
        row = conn.execute("SELECT subject FROM qa ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else ""


def count(table: str, where: str = "", args: tuple = ()) -> int:
    with db.connect() as conn:
        return conn.execute(f"SELECT count(*) FROM {table}" + (f" WHERE {where}" if where else ""), args).fetchone()[0]
