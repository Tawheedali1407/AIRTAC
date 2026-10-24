"""The knowledge library's tables: documents, their passages (chunks), a full-text index and,
when an embedding model is set, one vector per passage (float32 bytes)."""
from airtac import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS kdocs (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    shelf TEXT DEFAULT '',
    mtime REAL DEFAULT 0,
    chunks INTEGER DEFAULT 0,
    embedded INTEGER DEFAULT 0,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kchunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL,
    n INTEGER NOT NULL,
    place TEXT DEFAULT '',
    text TEXT NOT NULL,
    vec BLOB
);
CREATE INDEX IF NOT EXISTS kchunks_doc ON kchunks(doc_id);
"""
FTS = "CREATE VIRTUAL TABLE IF NOT EXISTS kchunks_fts USING fts5(text)"


def _fts(conn) -> bool:
    if db.USE_FTS:
        conn.execute(FTS)
    return bool(db.USE_FTS)


def get_doc(path: str) -> dict | None:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT * FROM kdocs WHERE path = ?", (path,)))
    return found[0] if found else None


def remove_doc(doc_id: int) -> None:
    with db.connect() as conn:
        if _fts(conn):
            conn.execute("DELETE FROM kchunks_fts WHERE rowid IN (SELECT id FROM kchunks WHERE doc_id = ?)", (doc_id,))
        conn.execute("DELETE FROM kchunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM kdocs WHERE id = ?", (doc_id,))


def save_doc(path: str, title: str, shelf: str, mtime: float, chunks: list[tuple[str, str]],
             vectors: list[bytes | None]) -> int:
    """Replace a document and its passages. chunks = [(place, text)], vectors in the same order."""
    old = get_doc(path)
    if old:
        remove_doc(old["id"])
    with db.connect() as conn:
        doc_id = conn.execute("INSERT INTO kdocs (path, title, shelf, mtime, chunks, embedded) VALUES (?, ?, ?, ?, ?, ?)",
                              (path, title, shelf, mtime, len(chunks), int(any(vectors)))).lastrowid
        fts = _fts(conn)
        for n, ((place, text), vec) in enumerate(zip(chunks, vectors)):
            cid = conn.execute("INSERT INTO kchunks (doc_id, n, place, text, vec) VALUES (?, ?, ?, ?, ?)",
                               (doc_id, n, place, text, vec)).lastrowid
            if fts:
                conn.execute("INSERT INTO kchunks_fts (rowid, text) VALUES (?, ?)", (cid, text))
    return doc_id


# Attached files (plugins/attachments) live in shelves that start with "@" (e.g. "@schedule"). The
# library's own searches leave them out; a search for given documents or a "@..." shelf finds them.
def _where(shelf: str = "", doc_ids: list[int] | None = None) -> tuple[str, list]:
    if doc_ids is not None:
        return f" AND d.id IN ({', '.join('?' * len(doc_ids)) or 'NULL'})", list(doc_ids)
    if shelf == "*":
        return "", []
    if shelf:
        return " AND d.shelf = ?", [shelf]
    return " AND d.shelf NOT LIKE '@%'", []


def docs(attached: bool = False) -> list[dict]:
    """The library's documents (attached files too, with attached=True)."""
    sql = "SELECT id, path, title, shelf, chunks, embedded, added_at FROM kdocs"
    with db.connect() as conn:
        return db.rows(conn.execute(sql + ("" if attached else " WHERE shelf NOT LIKE '@%'") + " ORDER BY shelf, title"))


def keyword_hits(terms: list[str], limit: int = 20, shelf: str = "", doc_ids: list[int] | None = None) -> list[tuple[int, float]]:
    """(chunk id, score) best first, from the full-text index (or LIKE when FTS5 is missing)."""
    if not terms:
        return []
    shelf_sql, shelf_args = _where(shelf, doc_ids)
    with db.connect() as conn:
        if _fts(conn):
            match = " OR ".join(f'"{t}"*' for t in terms)
            found = conn.execute(
                "SELECT c.id, bm25(kchunks_fts) FROM kchunks_fts JOIN kchunks c ON c.id = kchunks_fts.rowid "  # nosec B608 (placeholders only)
                f"JOIN kdocs d ON d.id = c.doc_id WHERE kchunks_fts MATCH ?{shelf_sql} ORDER BY bm25(kchunks_fts) LIMIT ?",
                [match, *shelf_args, limit]).fetchall()
            return [(i, -s) for i, s in found]  # bm25: lower is better
        where = " OR ".join("lower(c.text) LIKE ?" for _ in terms)
        found = conn.execute(f"SELECT c.id, c.text FROM kchunks c JOIN kdocs d ON d.id = c.doc_id WHERE ({where}){shelf_sql} "  # nosec B608 (placeholders only)
                             "LIMIT 400", [f"%{t}%" for t in terms] + shelf_args).fetchall()
    scored = sorted(((sum(text.lower().count(t) for t in terms), i) for i, text in found), reverse=True)
    return [(i, float(s)) for s, i in scored[:limit]]


def vectors(shelf: str = "", doc_ids: list[int] | None = None) -> list[tuple[int, bytes]]:
    sql = "SELECT c.id, c.vec FROM kchunks c JOIN kdocs d ON d.id = c.doc_id WHERE c.vec IS NOT NULL"
    extra, args = _where(shelf, doc_ids)
    with db.connect() as conn:
        return conn.execute(sql + extra, args).fetchall()


def chunks(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    with db.connect() as conn:
        found = db.rows(conn.execute(
            f"SELECT c.id, c.place, c.text, d.title, d.shelf, d.path FROM kchunks c JOIN kdocs d ON d.id = c.doc_id "  # nosec B608 (placeholders only)
            f"WHERE c.id IN ({', '.join('?' * len(ids))})", ids))
    order = {i: n for n, i in enumerate(ids)}
    return sorted(found, key=lambda r: order[r["id"]])


def counts() -> dict:
    """The library's numbers (attached files aren't counted)."""
    lib = "shelf NOT LIKE '@%'"
    with db.connect() as conn:
        docs_n, chunks_n = conn.execute(f"SELECT count(*), coalesce(sum(chunks), 0) FROM kdocs WHERE {lib}").fetchone()  # nosec B608 (placeholders only)
        embedded = conn.execute(f"SELECT count(*) FROM kchunks c JOIN kdocs d ON d.id = c.doc_id WHERE c.vec IS NOT NULL AND d.{lib}").fetchone()[0]  # nosec B608 (placeholders only)
        shelves = conn.execute(f"SELECT shelf, count(*) FROM kdocs WHERE {lib} GROUP BY shelf ORDER BY shelf").fetchall()  # nosec B608 (placeholders only)
    return {"docs": docs_n, "chunks": chunks_n, "embedded": embedded, "shelves": dict(shelves)}
