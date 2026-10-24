"""Attachments: the paperclip in every chat (Main chat, each app's chat, Research "Write").

    POST /api/upload  {section, name, data_base64}   (also /api/plugins/attachments/upload)
    GET  /api/plugins/attachments/{id}               status, preview text
    GET  /api/plugins/attachments/{id}/file          the photo itself (images only), for the preview

A file is checked before it's kept: size, an allowlist of types, the file's first bytes must match its
type (a renamed .exe is refused), a safe file name, and Office files (which are zip archives) are
measured before they're opened, so a "zip bomb" can't fill the disk or the memory.
Kept in data/uploads/<section>/. Its text is read in the background, one file at a time (this laptop
has 8 GB): PDF pages, PowerPoint slides, Word, Excel/CSV sheets, text and Markdown; photos with OCR
(RapidOCR, offline); audio with speech-to-text (faster-whisper, offline). The text is cut into passages
and indexed in the knowledge store (shelf "@<section>"), so the chat answers from the right part of a
long file and names the page.
"""
import base64
import io
import logging
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from airtac import config, db
from airtac.plugin_api import plugin

p = plugin(__name__)
log = logging.getLogger("airtac.attachments")

p.sql("""
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY,
    section TEXT NOT NULL,
    name TEXT NOT NULL,
    stored TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER DEFAULT 0,
    status TEXT DEFAULT 'reading',
    message TEXT DEFAULT '',
    chars INTEGER DEFAULT 0,
    parts INTEGER DEFAULT 0,
    doc_id INTEGER,
    text TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
""")

MAX_MB = 30
MAX_TEXT = 400_000              # characters kept per file (a 300-page book)
ZIP_MAX_FILES = 3000
ZIP_MAX_TOTAL = 250_000_000     # bytes once unpacked
ZIP_MAX_RATIO = 200             # a member 200x bigger unpacked than packed is a bomb
CONTEXT_CHARS = 5000            # how much of the attached text a small model can take with a question
SECTION_RE = re.compile(r"^[a-z0-9-]{1,30}$")

# type -> (kind, how to recognise its first bytes)
TYPES = {
    ".pdf": "pdf", ".pptx": "pptx", ".ppt": "ppt", ".docx": "docx", ".doc": "doc", ".xlsx": "xlsx", ".csv": "csv",
    ".txt": "text", ".md": "text", ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio", ".webm": "audio", ".ics": "calendar",
}
OOXML = {".docx": "word/", ".pptx": "ppt/", ".xlsx": "xl/"}
ACCEPT = sorted(TYPES)


class Refused(Exception):
    """The file isn't accepted. The message is a sentence for the page."""


class Unavailable(Exception):
    """A reader (OCR, speech-to-text) isn't installed. The message says how to add it."""


# ---------------------------------------------------------------- checks

def safe_name(name: str) -> str:
    """A file name that can't climb out of its folder or confuse Windows: letters, digits, . _ - only."""
    base = Path(str(name).replace("\\", "/")).name
    stem, ext = Path(base).stem, Path(base).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")[:60] or "file"
    return stem + ext


def _magic_ok(ext: str, raw: bytes) -> bool:
    head = raw[:16]
    if ext == ".pdf":
        return raw[:1024].find(b"%PDF-") >= 0
    if ext in OOXML:
        return head.startswith(b"PK\x03\x04")
    if ext in (".doc", ".ppt"):
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".mp3":
        return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".m4a":
        return head[4:8] == b"ftyp"
    if ext == ".ogg":
        return head.startswith(b"OggS")
    if ext == ".webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if ext in (".txt", ".md", ".csv"):
        return b"\x00" not in raw[:8192]          # text never holds NUL bytes; programs do
    if ext == ".ics":
        return raw.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"BEGIN:VCALENDAR")
    return False


def _check_zip(ext: str, raw: bytes) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise Refused("That Office file is damaged.")
    infos = z.infolist()
    names = {i.filename for i in infos}
    if "[Content_Types].xml" not in names or not any(n.startswith(OOXML[ext]) for n in names):
        raise Refused(f"That isn't a real {ext[1:].upper()} file.")
    if len(infos) > ZIP_MAX_FILES or sum(i.file_size for i in infos) > ZIP_MAX_TOTAL:
        raise Refused("That file unpacks to something far too big, so it wasn't opened.")
    for i in infos:
        if i.compress_size and i.file_size / i.compress_size > ZIP_MAX_RATIO and i.file_size > 1_000_000:
            raise Refused("That file looks like a zip bomb, so it wasn't opened.")


def check(name: str, raw: bytes) -> str:
    """The file's kind, or Refused with the reason."""
    ext = Path(safe_name(name)).suffix.lower()
    if ext not in TYPES:
        raise Refused(f"AIRTAC takes {', '.join(x[1:].upper() for x in ACCEPT)} files.")
    if not raw:
        raise Refused("That file is empty.")
    if len(raw) > MAX_MB * 1_000_000:
        raise Refused(f"Files up to {MAX_MB} MB, please.")
    if not _magic_ok(ext, raw):
        raise Refused(f"That file doesn't look like a real {ext[1:].upper()} file, so it wasn't kept.")
    if ext in OOXML:
        _check_zip(ext, raw)
    return TYPES[ext]


# ---------------------------------------------------------------- readers

def _text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1252", errors="replace")


def read_pdf(path: Path) -> tuple[list[tuple[str, str]], str]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts, scanned, note = [], 0, ""
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if len(text) < 20 and i <= 40:          # a scanned page: read its picture
            try:
                text = "\n".join(ocr_bytes(img.data) for img in page.images[:3]).strip() or text
                scanned += 1
            except Unavailable as e:
                note = str(e)
            except Exception:
                pass
        parts.append((f"p. {i}", text))
    if scanned:
        note = f"{scanned} scanned page{'s' if scanned != 1 else ''} read with OCR."
    return parts, note


def read_pptx(path: Path) -> list[tuple[str, str]]:
    from pptx import Presentation
    parts = []
    for n, slide in enumerate(Presentation(str(path)).slides, 1):
        lines = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                lines.append(shape.text_frame.text.strip())
            if getattr(shape, "has_table", False):
                lines += [" | ".join(c.text for c in row.cells) for row in shape.table.rows]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            lines.append("Notes: " + slide.notes_slide.notes_text_frame.text.strip())
        parts.append((f"slide {n}", "\n".join(lines)))
    return parts


def read_docx(path: Path) -> list[tuple[str, str]]:
    import docx
    d = docx.Document(str(path))
    lines = [par.text for par in d.paragraphs if par.text.strip()]
    for table in d.tables:
        lines += [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
    return [("", "\n".join(lines))]


def read_xlsx(path: Path) -> list[tuple[str, str]]:
    from openpyxl import load_workbook
    wb = load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets[:20]:
        rows = []
        for row in ws.iter_rows(max_row=2000, max_col=50, values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(cells):
                rows.append(" | ".join(cells).rstrip(" |"))
        parts.append((f"sheet {ws.title}", "\n".join(rows)))
    wb.close()
    return parts


def read_csv(path: Path) -> list[tuple[str, str]]:
    import csv
    lines = []
    for n, row in enumerate(csv.reader(io.StringIO(_text_file(path)))):
        if n >= 5000:
            break
        lines.append(" | ".join(row))
    return [("", "\n".join(lines))]


_ocr = {"engine": None}
_ocr_lock = threading.Lock()


def ocr_bytes(data: bytes) -> str:
    """Text in a picture. RapidOCR (offline, Apache-2.0) if installed, else Tesseract if installed."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 40_000_000           # a decompression bomb stops here
    img = Image.open(io.BytesIO(data))
    img.verify()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    try:
        from rapidocr import RapidOCR
    except ImportError:
        RapidOCR = None
    if RapidOCR is not None:
        with _ocr_lock:
            if _ocr["engine"] is None:
                _ocr["engine"] = RapidOCR()
            result = _ocr["engine"](img)
        return "\n".join(result.txts or ()) if result is not None else ""
    try:
        import pytesseract
        return pytesseract.image_to_string(img)
    except Exception:
        raise Unavailable("OCR isn't installed, so text in pictures can't be read yet. "
                          "Install it with: .venv\\Scripts\\python -m pip install rapidocr==3.10.0 onnxruntime==1.31.0")


_whisper = {"model": None}
_whisper_lock = threading.Lock()
WHISPER_MODEL = "base"   # about 145 MB, MIT; good enough on a laptop processor (int8)


def transcribe(path: Path) -> list[tuple[str, str]]:
    """Speech to text with faster-whisper (offline once the model is downloaded into models/whisper)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise Unavailable("Speech-to-text isn't installed, so audio can't be read yet. Install it with: "
                          ".venv\\Scripts\\python -m pip install faster-whisper==1.2.1")
    folder = config.ROOT / "models" / "whisper"
    folder.mkdir(parents=True, exist_ok=True)
    with _whisper_lock:
        if _whisper["model"] is None:
            _whisper["model"] = WhisperModel(p.setting("whisper_model", WHISPER_MODEL), device="cpu",
                                             compute_type="int8", download_root=str(folder))
        segments, info = _whisper["model"].transcribe(str(path), vad_filter=True, beam_size=1)
        parts, buf, start = [], [], 0.0
        for seg in segments:
            if not buf:
                start = seg.start
            buf.append(seg.text.strip())
            if seg.end - start > 60:                    # one passage per minute, labelled with its time
                parts.append((f"{int(start // 60)}:{int(start % 60):02d}", " ".join(buf)))
                buf = []
        if buf:
            parts.append((f"{int(start // 60)}:{int(start % 60):02d}", " ".join(buf)))
    return parts


def extract(path: Path, kind: str) -> tuple[list[tuple[str, str]], str]:
    """([(place, text)], a note for the page). Raises Unavailable or the reader's own error."""
    if kind == "pdf":
        return read_pdf(path)
    if kind == "pptx":
        return read_pptx(path), ""
    if kind == "docx":
        return read_docx(path), ""
    if kind == "xlsx":
        return read_xlsx(path), ""
    if kind == "csv":
        return read_csv(path), ""
    if kind in ("text", "calendar"):
        return [("", _text_file(path))], ""
    if kind == "image":
        text = ocr_bytes(path.read_bytes())
        return [("", text)], "" if text.strip() else "No text found in the picture."
    if kind == "audio":
        return transcribe(path), ""
    if kind in ("doc", "ppt"):
        raise Unavailable(f"Old .{kind} files can't be read. Open it and save it as .{kind}x, then attach that.")
    return [], ""


# ---------------------------------------------------------------- storing and indexing

def folder(section: str) -> Path:
    path = config.DB_PATH.parent / "uploads" / section
    path.mkdir(parents=True, exist_ok=True)
    return path


def get(upload_id: int) -> dict | None:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)))
    return found[0] if found else None


def _update(upload_id: int, **fields) -> None:
    sql = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE uploads SET {sql} WHERE id = ?", (*fields.values(), upload_id))  # nosec B608 (placeholders only)


def public(row: dict) -> dict:
    """What the page sees: never the folder path."""
    return {"id": row["id"], "section": row["section"], "name": row["name"], "kind": row["kind"], "size": row["size"],
            "status": row["status"], "message": row["message"], "chars": row["chars"], "parts": row["parts"],
            "preview": (row.get("text") or "")[:600]}


def save(section: str, name: str, raw: bytes) -> dict:
    """Check and keep a file, then read it in the background. Raises Refused."""
    if not SECTION_RE.match(section or ""):
        raise Refused("Unknown section.")
    kind = check(name, raw)
    clean = safe_name(name)
    with db.connect() as conn:
        upload_id = conn.execute("INSERT INTO uploads (section, name, stored, kind, size) VALUES (?, ?, '', ?, ?)",
                                 (section, clean, kind, len(raw))).lastrowid
    stored = f"{upload_id}-{clean}"
    (folder(section) / stored).write_bytes(raw)
    _update(upload_id, stored=stored)
    _pool.submit(read_now, upload_id)
    return public(get(upload_id))


_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="attachments")   # one at a time: OCR and audio are heavy


_reading = threading.Lock()


def read_now(upload_id: int) -> dict:
    """Read one file's text and index it, once. Called in the background (and directly by the tests)."""
    with _reading:
        row = get(upload_id)
        if row is None:
            return {}
        if row["status"] != "reading":       # already read
            return public(row)
        return _read(row)


def _read(row: dict) -> dict:
    upload_id = row["id"]
    path = folder(row["section"]) / row["stored"]
    try:
        parts, note = extract(path, row["kind"])
    except Unavailable as e:
        _update(upload_id, status="error", message=str(e))
        return public(get(upload_id))
    except Exception as e:
        log.exception("couldn't read %s", row["stored"])
        _update(upload_id, status="error", message=f"AIRTAC couldn't read this file ({type(e).__name__}). Is it damaged or password-protected?")
        return public(get(upload_id))
    parts = [(pl, t.strip()) for pl, t in parts if t and t.strip()]
    text = "\n\n".join(f"[{pl}]\n{t}" if pl else t for pl, t in parts)[:MAX_TEXT]
    if not text:
        _update(upload_id, status="error", message=note or "No text found in this file.")
        return public(get(upload_id))
    doc_id = None
    try:
        from plugins.knowledge import EMBED_BATCH, _pack, embed, split, store
        chunks = split(parts)
        vectors = [None] * len(chunks)
        for start in range(0, len(chunks), EMBED_BATCH):
            vecs = embed([t for _, t in chunks[start:start + EMBED_BATCH]])
            if vecs is None:
                break
            for j, v in enumerate(vecs):
                vectors[start + j] = _pack(v)
        doc_id = store.save_doc(f"@uploads/{row['section']}/{row['stored']}", row["name"], f"@{row['section']}",
                                time.time(), chunks, vectors)
    except Exception:   # the chat still gets the text; only the search over long files is lost
        log.exception("couldn't index %s", row["stored"])
    _update(upload_id, status="ready", message=note, chars=len(text), parts=len(parts), doc_id=doc_id, text=text)
    return public(get(upload_id))


# ---------------------------------------------------------------- what the chat reads

@p.attached
def context_for(ids: list[int], message: str) -> dict:
    """The attached files' text for this question: all of it when it's short, else the passages that
    match the question best (with their page), plus how each file starts."""
    rows = [r for r in (get(int(i)) for i in ids[:10]) if r]
    if not rows:
        return {}
    notes = [f"(The file {r['name']} couldn't be read: {r['message']})" for r in rows if r["status"] == "error"]
    notes += [f"(The file {r['name']} is still being read; answer from the others.)" for r in rows if r["status"] == "reading"]
    ready = [r for r in rows if r["status"] == "ready"]
    total = sum(r["chars"] for r in ready)
    blocks, sources = [], []
    if total <= CONTEXT_CHARS:
        blocks = [f"=== {r['name']} ===\n{r['text']}" for r in ready]
        sources = [{"title": r["name"], "where": "attached"} for r in ready]
    else:
        from plugins.knowledge import search
        heads = [f"=== {r['name']} (start) ===\n{r['text'][:400]}" for r in ready]
        found = search(message, 6, doc_ids=[r["doc_id"] for r in ready if r["doc_id"]])
        blocks = heads + [f"=== {c['title']}, {c['place'] or 'passage'} ===\n{c['text'][:900]}" for c in found]
        sources = [{"title": c["title"], "where": c["place"] or "attached"} for c in found] or \
                  [{"title": r["name"], "where": "attached"} for r in ready]
    text = "\n\n".join(notes + blocks)
    return {"text": text[:CONTEXT_CHARS + 2400], "sources": sources}


# ---------------------------------------------------------------- routes

class Upload(BaseModel):
    section: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=200)
    data_base64: str = Field(min_length=4, max_length=int(MAX_MB * 1_000_000 * 1.37) + 100)


def upload_route(body: Upload):
    from fastapi import HTTPException
    try:
        raw = base64.b64decode(body.data_base64, validate=True)
    except ValueError:
        raise HTTPException(422, "That file didn't arrive in one piece. Try again.")
    try:
        return save(body.section, body.name, raw)
    except Refused as e:
        raise HTTPException(422, str(e))


@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Response

    @router.post("/upload")
    def upload(body: Upload):
        return upload_route(body)

    @router.get("/types")
    def types():
        return {"accept": ACCEPT, "max_mb": MAX_MB}

    @router.get("/{upload_id}")
    def one(upload_id: int):
        row = get(upload_id)
        if row is None:
            raise HTTPException(404, "That attachment isn't here any more.")
        return {**public(row), "text": (row.get("text") or "")[:4000]}

    @router.get("/{upload_id}/file")
    def file(upload_id: int):
        row = get(upload_id)
        if row is None or row["kind"] != "image":
            raise HTTPException(404, "No picture.")
        data = (folder(row["section"]) / row["stored"]).read_bytes()
        kind = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
        return Response(data, media_type=kind, headers={"Content-Security-Policy": "default-src 'none'",
                                                        "Cache-Control": "private, max-age=3600"})
