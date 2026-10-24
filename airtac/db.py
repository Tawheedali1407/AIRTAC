"""One SQLite file for everything (data/airtac.db). Plugins add their own tables with p.sql().

A fresh connection per call: SQLite is fast enough for that, and it avoids sharing one
connection across FastAPI's worker threads and the background scheduler.
"""
import sqlite3
from contextlib import contextmanager

from airtac import config

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- One row per tool run, so each app page can show how much you've used it.
CREATE TABLE IF NOT EXISTS tool_runs (
    id INTEGER PRIMARY KEY,
    tool TEXT NOT NULL,
    plugin TEXT DEFAULT '',
    ok INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Things the background scheduler wants to tell you (a reminder, the morning briefing).
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    plugin TEXT DEFAULT '',
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    spoken TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Your thumbs up / down on answers, with what the right answer should have been.
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    message_id INTEGER,
    good INTEGER NOT NULL,
    note TEXT DEFAULT '',
    question TEXT DEFAULT '',
    answer TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Lessons: short rules AIRTAC adds to its instructions when they match a question. From your
-- corrections (active straight away) or from its weekly self-review (suggested until you approve).
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    source TEXT DEFAULT 'you',
    active INTEGER DEFAULT 1,
    uses INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Work mode jobs (plan, steps, notes, result as JSON) and saved skills (a job worth repeating).
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    steps TEXT DEFAULT '[]',
    runs INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Small key-value store for plugins ("last reminder sent", "morning briefing done today").
CREATE TABLE IF NOT EXISTS kv (
    plugin TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT DEFAULT '',
    PRIMARY KEY (plugin, key)
);
"""
FTS_SCHEMA = "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text)"

PLUGIN_SQL: list[str] = []   # extra CREATE TABLE statements from plugins
USE_FTS: bool | None = None  # None = not checked yet; tests set False to exercise the LIKE path


def add_schema(sql: str) -> None:
    if sql not in PLUGIN_SQL:
        PLUGIN_SQL.append(sql)


@contextmanager
def connect():
    global USE_FTS
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.executescript(CORE_SCHEMA + "\n".join(PLUGIN_SQL))
        if USE_FTS is None:
            try:
                conn.execute(FTS_SCHEMA)
                USE_FTS = True
            except sqlite3.OperationalError:  # "no such module: fts5"
                USE_FTS = False
        elif USE_FTS:
            conn.execute(FTS_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(cur) -> list[dict]:
    names = [c[0] for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def iso(ts: str) -> str:
    """SQLite's CURRENT_TIMESTAMP is UTC 'YYYY-MM-DD HH:MM:SS'; the page wants ISO with Z."""
    return ts.replace(" ", "T") + "Z" if ts else ""
