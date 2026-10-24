"""Knowledge library: your own books and notes (medicine, engineering, coding), searched by meaning
and quoted with sources.

Put files in the knowledge/ folder (one subfolder per shelf, e.g. knowledge/medicine,
knowledge/engineering, knowledge/coding), or drop them on the Knowledge page. AIRTAC reads
PDF, Markdown, text, HTML and Word files, cuts them into passages and indexes them:
  - always with SQLite's full-text index (finds the same words), and
  - with an embedding model when one is set in [brains] embed (finds the same meaning, e.g.
    "heart attack" also finds "myocardial infarction"). Default: nomic-embed-text in Ollama.
Before every answer, the best passages for your question are added to the model's
instructions, with the book and page, so the answer can say where it came from.

Free books to start with are listed in docs/KNOWLEDGE.md (OpenStax textbooks, official docs).
Everything stays on this laptop. A few thousand passages search in well under a second; for a
whole encyclopedia, see the sqlite-vec step on the roadmap.
"""
import base64
import logging
import math
import re
import time
from array import array
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from airtac import brains, config, memory, websearch
from airtac.plugin_api import Tool, ToolResult, after, local_only, plugin, schema

from . import store

p = plugin(__name__)
p.sql(store.SCHEMA)
log = logging.getLogger("airtac.knowledge")

TYPES = {".pdf", ".md", ".txt", ".html", ".htm", ".docx"}
CHUNK = 900            # characters per passage: about a paragraph or two
OVERLAP = 150          # passages overlap a little so a sentence cut in two is still found
MAX_FILE_MB = 60
EMBED_BATCH = 16
MIN_COSINE = 0.55      # below this a passage is too far from the question to add to an answer
_status = {"indexing": False, "last": "", "error": ""}


def library() -> Path:
    folder = config.KNOWLEDGE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------- reading files

def _pdf_pages(path: Path) -> list[tuple[str, str]]:
    from pypdf import PdfReader  # imported here so start-up stays fast
    reader = PdfReader(str(path))
    return [(f"p. {i}", page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]


def _docx_text(path: Path) -> str:
    import docx  # python-docx, from the documents plugin's packages
    return "\n".join(par.text for par in docx.Document(str(path)).paragraphs)


def read_file(path: Path) -> list[tuple[str, str]]:
    """[(place, text)]: one entry per PDF page, or one for the whole file."""
    kind = path.suffix.lower()
    if kind == ".pdf":
        return _pdf_pages(path)
    if kind == ".docx":
        return [("", _docx_text(path))]
    raw = path.read_text(encoding="utf-8", errors="replace")
    if kind in (".html", ".htm"):
        page = websearch.extract(raw, "")
        return [("", page["text"])]
    return [("", raw)]


def split(parts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Cut text into ~CHUNK-character passages on paragraph and sentence boundaries."""
    out = []
    for place, text in parts:
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not text:
            continue
        paras = [x.strip() for x in re.split(r"\n\s*\n|\n(?=[A-Z#\-•])", text) if x.strip()]
        buf = ""
        for para in paras:
            while len(para) > CHUNK:  # a very long paragraph: cut at a sentence end
                cut = para.rfind(". ", 0, CHUNK)
                cut = cut + 1 if cut > CHUNK // 2 else CHUNK
                if buf:
                    out.append((place, buf))
                    buf = ""
                out.append((place, para[:cut].strip()))
                para = para[max(0, cut - OVERLAP):].strip()
            if len(buf) + len(para) + 1 > CHUNK and buf:
                out.append((place, buf))
                buf = buf[-OVERLAP:] + " " + para
            else:
                buf = f"{buf} {para}".strip()
        if buf:
            out.append((place, buf))
    return [(pl, t) for pl, t in out if len(t) > 40]


# ---------------------------------------------------------------- embeddings (meaning search)

_EMBED_DOWN = {"until": 0.0}
EMBED_RETRY = 300           # after a failure, keyword search only for 5 minutes (each try cost about 6 s)


def embed(texts: list[str]) -> list[list[float]] | None:
    """Vectors from Ollama's /api/embed, or None when no embedding model is set or reachable."""
    import time
    model = brains.embed_model()
    if not model or config.LLM_PROVIDER != "ollama" or not texts or time.time() < _EMBED_DOWN["until"]:
        return None
    try:
        r = httpx.post(f"{config.LLM_BASE_URL}/api/embed", json={"model": model, "input": texts}, timeout=120)
        r.raise_for_status()
        vecs = r.json().get("embeddings")
        return vecs if isinstance(vecs, list) and len(vecs) == len(texts) else None
    except (httpx.HTTPError, ValueError):
        log.warning("embedding model %s isn't available; using keyword search only for %d minutes", model, EMBED_RETRY // 60)
        _EMBED_DOWN["until"] = time.time() + EMBED_RETRY
        return None


def _pack(vec: list[float]) -> bytes:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array("f", (x / norm for x in vec)).tobytes()


def _unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def cosine_top(query_vec: list[float], rows: list[tuple[int, bytes]], k: int) -> list[tuple[int, float]]:
    """Best k (chunk id, cosine). Vectors are stored normalised, so cosine is a dot product."""
    q = _unpack(_pack(query_vec))
    try:
        import numpy as np  # much faster when installed
        ids = [i for i, _ in rows]
        mat = np.frombuffer(b"".join(b for _, b in rows), dtype=np.float32).reshape(len(rows), len(q))
        scores = mat @ np.frombuffer(q.tobytes(), dtype=np.float32)
        best = np.argsort(-scores)[:k]
        return [(ids[i], float(scores[i])) for i in best]
    except (ImportError, ValueError):
        scored = [(sum(a * b for a, b in zip(q, _unpack(blob))), i) for i, blob in rows]
        scored.sort(reverse=True)
        return [(i, s) for s, i in scored[:k]]


# ---------------------------------------------------------------- indexing

def index_file(path: Path) -> int:
    """(Re)index one file. Returns the number of passages."""
    rel = path.relative_to(library()).as_posix()
    shelf = rel.split("/")[0] if "/" in rel else ""
    chunks = split(read_file(path))
    vectors: list[bytes | None] = [None] * len(chunks)
    for start in range(0, len(chunks), EMBED_BATCH):
        vecs = embed([t for _, t in chunks[start:start + EMBED_BATCH]])
        if vecs is None:
            break
        for j, v in enumerate(vecs):
            vectors[start + j] = _pack(v)
    title = re.sub(r"[_-]+", " ", path.stem).strip() or path.name
    store.save_doc(rel, title, shelf, path.stat().st_mtime, chunks, vectors)
    return len(chunks)


def index_all() -> dict:
    """Index new and changed files, forget deleted ones. Safe to call often (it skips what's done)."""
    if _status["indexing"]:
        return {"added": 0, "busy": True}
    _status.update(indexing=True, error="")
    added, removed, failed = 0, 0, []
    can_embed = None  # checked once, only if needed
    try:
        root = library()
        seen = set()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TYPES or path.name.startswith("."):
                continue
            rel = path.relative_to(root).as_posix()
            seen.add(rel)
            if path.stat().st_size > MAX_FILE_MB * 1_000_000:
                failed.append(f"{rel} (bigger than {MAX_FILE_MB} MB)")
                continue
            old = store.get_doc(rel)
            if old and abs(old["mtime"] - path.stat().st_mtime) < 1:
                if old["embedded"]:
                    continue
                if can_embed is None:  # an unchanged file is read again only to add meaning search
                    can_embed = embed(["test"]) is not None
                if not can_embed:
                    continue
            try:
                index_file(path)
                added += 1
            except Exception as e:
                log.exception("couldn't index %s", rel)
                failed.append(f"{rel} ({type(e).__name__})")
        for d in store.docs():
            if d["path"] not in seen:
                store.remove_doc(d["id"])
                removed += 1
    finally:
        _status.update(indexing=False, last=time.strftime("%Y-%m-%d %H:%M"), error="; ".join(failed)[:500])
    return {"added": added, "removed": removed, "failed": failed}


# ---------------------------------------------------------------- searching

def search(query: str, k: int = 4, shelf: str = "", collection: str | None = None,
           doc_ids: list[int] | None = None) -> list[dict]:
    """THE search for your library: best passages for a question, keyword (FTS5) and meaning (Ollama
    embeddings) results merged by reciprocal rank. Each result: text, file, page, score, plus title,
    place, shelf and path. collection = a shelf ("coding"), "*" = library and attached files,
    "@<section>" = files attached in that section; doc_ids = only these documents.
    AIRTAC 1 can replace what's behind this function without changing its callers."""
    shelf = collection if collection is not None else shelf
    terms = memory._terms(query)
    ranked: dict[int, float] = {}
    meaning: dict[int, float] = {}
    for rank, (cid, _score) in enumerate(store.keyword_hits(terms, 20, shelf, doc_ids)):
        ranked[cid] = ranked.get(cid, 0) + 1 / (60 + rank)
    rows = store.vectors(shelf, doc_ids)
    if rows:
        qvec = embed([query])
        if qvec:
            for rank, (cid, cos) in enumerate(cosine_top(qvec[0], rows, 20)):
                meaning[cid] = cos
                ranked[cid] = ranked.get(cid, 0) + 1 / (60 + rank)
    best = sorted(ranked, key=lambda c: -ranked[c])[:k]
    out = store.chunks(best)
    for c in out:
        c["cosine"] = meaning.get(c["id"])
        low = c["text"].lower()
        c["matches"] = sum(1 for t in terms if t not in GENERIC and len(t) > 2
                           and re.search(rf"\b{re.escape(t)}\w{{0,2}}\b", low))   # whole words ("one" isn't in "someone")
        c["file"] = c["path"].rsplit("/", 1)[-1]
        page = re.match(r"p\. (\d+)", c["place"] or "")
        c["page"] = int(page.group(1)) if page else None
        c["score"] = round(ranked[c["id"]] * 60 / 2, 3)     # 1.0 = first in both lists
    return out


def cited_answer(query: str, found: list[dict]) -> dict:
    """A short answer quoted from your own files, each line with its source [n] (file, page). No model:
    the words are the book's own, so nothing is made up."""
    lines, sources = [], []
    for c in found[:3]:
        if c["cosine"] is not None and c["cosine"] < MIN_COSINE and not c["matches"]:
            continue
        sources.append({"n": len(sources) + 1, "file": c["file"], "page": c["page"], "title": c["title"]})
        lines.append(f"{best_sentence(c['text'], query)} [{len(sources)}]")
    return {"text": " ".join(lines), "sources": sources}


def best_sentence(text: str, query: str) -> str:
    """The sentence of a passage that shares the most words with the question (for a spoken answer)."""
    terms = memory._terms(query)
    sentences = [x.strip(" #-") for x in re.split(r"(?<=[.!?])\s+|\n+", text) if len(x.strip()) > 20]
    if not sentences:
        return text[:200]
    return max(sentences, key=lambda x: sum(t in x.lower() for t in terms))[:240]


def _source(c: dict) -> str:
    return f"{c['title']}" + (f", {c['place']}" if c["place"] else "")


# Words in almost every question that say nothing about its subject ("answer in one word").
GENERIC = set("answer one two word words short simple explain please give tell line lines brief briefly example "
              "examples thing things way ways using use make need want know like just also".split())


def relevant(passages: list[dict], terms: int) -> list[dict]:
    """Keep passages close enough to the question to be worth the model's attention."""
    keep = []
    for c in passages:
        if c["cosine"] is not None and c["cosine"] >= MIN_COSINE:
            keep.append(c)
        elif c["cosine"] is None and c["matches"] >= max(2, min(terms, 3)):
            keep.append(c)
    return keep


# ---------------------------------------------------------------- tools

def knowledge_search(query: str, shelf: str = "") -> ToolResult:
    if not store.counts()["docs"]:
        return ToolResult("Your knowledge library is empty. Add books or notes on the Knowledge page first.")
    found = search(query, 4, shelf.strip().lower())
    if not found:
        return ToolResult(f"I didn't find anything about {query} in your library.")
    top = found[0]
    spoken = f"From {top['title']}: {best_sentence(top['text'], query)}"
    screen = f"Library results for: {query}\n" + "\n\n".join(
        f"[{i}] {_source(c)}\n{c['text'][:450]}" for i, c in enumerate(found, 1))
    return ToolResult(spoken, screen, data={"passages": found})


def knowledge_index() -> ToolResult:
    result = index_all()
    if result.get("busy"):
        return ToolResult("I'm already reading your library. Try again in a minute.")
    c = store.counts()
    spoken = f"Your library has {c['docs']} documents and {c['chunks']} passages."
    if result["added"]:
        spoken = f"I read {result['added']} new or changed file{'s' if result['added'] != 1 else ''}. " + spoken
    screen = spoken + (f"\nCouldn't read: {', '.join(result['failed'])}" if result["failed"] else "")
    return ToolResult(spoken, screen)


p.add(Tool("knowledge_search", "Search the user's own library of books and notes (medicine, engineering, coding and more) "
           "and return the best passages with their sources.",
           schema({"query": {"type": "string", "description": "What to look for"},
                   "shelf": {"type": "string", "description": "Optional shelf (folder), e.g. medicine"}},
                  required=("query",)),
           "local", knowledge_search))
p.add(Tool("knowledge_index", "Read new or changed files in the user's knowledge folder so they can be searched.",
           schema(), "local", knowledge_index))


# ---------------------------------------------------------------- passages for every answer

@p.context
def passages(message: str) -> dict | None:
    if not store.counts()["chunks"]:
        return None
    terms = len([t for t in memory._terms(message) if t not in GENERIC and len(t) > 2])
    if not terms:
        return None          # "what is 2 + 2?" has nothing to look up in your library
    found = relevant(search(message, 3), terms)
    if not found:
        return None
    text = "\n\n".join(f"[{_source(c)}]\n{c['text'][:700]}" for c in found)
    return {"text": text, "sources": [{"title": c["title"], "where": c["place"], "shelf": c["shelf"]} for c in found]}


# ---------------------------------------------------------------- the Knowledge page

@p.dashboard
def page(pg: dict) -> None:
    c = store.counts()
    embed_model = brains.embed_model()
    pg["stats"] = [{"label": "Documents", "value": c["docs"]}, {"label": "Passages", "value": c["chunks"]},
                   {"label": "Meaning search" if c["embedded"] or not embed_model else f"Meaning search (needs {embed_model})",
                    "value": f"{c['embedded']} passages" if c["embedded"] else "off" if not embed_model else "waiting"}]
    if c["shelves"]:
        pg["bars"] = {"title": "Documents by shelf", "items": [{"label": s or "(main)", "value": n} for s, n in c["shelves"].items()]}
    pg["lists"] = [{"title": "Your library", "empty": "Empty. Drop PDFs or notes here, or into the knowledge folder.",
                    "items": [{"title": d["title"], "meta": f"{d['shelf'] or 'main'}, {d['chunks']} passages"
                               + ("" if d["embedded"] else ", keyword search only")} for d in store.docs()[:30]]}]
    if _status["error"]:
        pg["lists"].append({"title": "Couldn't read", "items": [{"title": x} for x in _status["error"].split("; ")]})
    pg["library"] = {"folder": str(library()), "indexing": _status["indexing"], "last": _status["last"],
                     "types": sorted(TYPES)}


# ---------------------------------------------------------------- a GitHub repository as a collection

REPO_RE = re.compile(r"^(?:https://github\.com/)?([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")
REPO_MAX_MB = 150                       # the documents, all together
REPO_TYPES = {".md", ".txt", ".pdf", ".html", ".htm", ".rst"}   # docs only: no programs are kept or run
REPO_MAX_FILES = 2000
_repo_status = {"busy": False, "last": "", "error": ""}


def repo_name(text: str) -> tuple[str, str]:
    m = REPO_RE.match((text or "").strip())
    if not m:
        raise ValueError("Give a repository as owner/name or https://github.com/owner/name.")
    return m.group(1), m.group(2)


def add_repo(text: str, shelf: str = "") -> dict:
    """Download a public GitHub repository's documents (Markdown, text, PDF, HTML) into
    knowledge/<shelf>/<owner>-<name>/ and index them, so Research can use it as a sub-brain.
    Only the documents are fetched, one by one, from GitHub's list of files (a tutorial repository can be
    hundreds of MB of programs and pictures). The licence and address go to SOURCE.md next to the files.
    Programs are never kept or run. Updating = adding it again (files are replaced)."""
    owner, name = repo_name(text)
    shelf = re.sub(r"[^a-z0-9-]+", "-", (shelf or name).lower()).strip("-")[:40] or "repos"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "AIRTAC"}
    token = p.secret("GITHUB_TOKEN")
    if token:                                      # only raises GitHub's rate limit; sent in a header, never the address
        headers["Authorization"] = f"Bearer {token}"
    info = httpx.get(f"https://api.github.com/repos/{owner}/{name}", headers=headers, timeout=15)
    if info.status_code == 404:
        raise ValueError(f"GitHub has no public repository {owner}/{name}.")
    info.raise_for_status()
    meta = info.json()
    if meta.get("private"):
        raise ValueError("Only public repositories can be added.")
    branch = meta.get("default_branch") or "main"
    tree = httpx.get(f"https://api.github.com/repos/{owner}/{name}/git/trees/{branch}", params={"recursive": "1"},
                     headers=headers, timeout=30)
    tree.raise_for_status()
    wanted = []
    for item in tree.json().get("tree", []):
        path = item.get("path", "")
        parts = path.split("/")
        if item.get("type") != "blob" or any(x in ("..", "") or x.startswith(".") for x in parts):
            continue                                # no hidden files, no climbing out of the folder
        if Path(path).suffix.lower() in REPO_TYPES and item.get("size", 0) <= MAX_FILE_MB * 1_000_000:
            wanted.append((path, item.get("size", 0)))
    wanted = wanted[:REPO_MAX_FILES]
    if sum(size for _, size in wanted) > REPO_MAX_MB * 1_000_000:
        raise ValueError(f"Its documents add up to more than {REPO_MAX_MB} MB, so it wasn't added.")
    dest = library() / shelf / f"{owner}-{name}".lower()
    dest.mkdir(parents=True, exist_ok=True)
    kept, failed = 0, 0
    with httpx.Client(timeout=60, headers={"User-Agent": "AIRTAC"}, follow_redirects=False) as client:
        for path, _ in wanted:
            r = client.get(f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/" + "/".join(
                quote(x) for x in path.split("/")))
            if r.status_code != 200 or len(r.content) > MAX_FILE_MB * 1_000_000:
                failed += 1
                continue
            out = dest.joinpath(*[re.sub(r"[^A-Za-z0-9._-]+", "-", x)[:80] for x in path.split("/")])
            if out.suffix.lower() == ".rst":
                out = out.with_suffix(".txt")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(r.content)
            kept += 1
    licence = (meta.get("license") or {}).get("spdx_id") or "none stated"
    (dest / "SOURCE.md").write_text(
        f"# Source: {meta.get('full_name', owner + '/' + name)}\n\n{meta.get('description') or ''}\n\n"
        f"- Address: {meta.get('html_url', f'https://github.com/{owner}/{name}')}\n- Licence: {licence}\n"
        f"- Branch: {branch}\n- Added: {time.strftime('%Y-%m-%d %H:%M')}\n", encoding="utf-8")
    passages = 0
    for path in sorted(dest.rglob("*")):
        if path.is_file() and path.suffix.lower() in TYPES:
            _repo_status["last"] = f"Indexing {path.name}..."
            try:
                passages += index_file(path)
            except Exception:
                log.exception("couldn't index %s", path)
    return {"repo": f"{owner}/{name}", "collection": shelf, "files": kept, "failed": failed, "passages": passages,
            "licence": licence}


def collections() -> list[dict]:
    """The library's shelves, for Research's sub-brain picker."""
    return [{"id": s, "docs": n} for s, n in store.counts()["shelves"].items() if s]


class Upload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    shelf: str = Field(default="", max_length=40)
    data_base64: str = Field(min_length=1, max_length=90_000_000)


@p.routes
def api(router) -> None:
    @router.post("/upload")
    def upload(body: Upload):
        """Save a file into knowledge/<shelf>/ and index it. Base64 keeps this free of upload libraries."""
        name = Path(body.name).name
        if Path(name).suffix.lower() not in TYPES:
            raise HTTPException(422, f"AIRTAC reads {', '.join(sorted(TYPES))} files.")
        shelf = re.sub(r"[^a-z0-9-]+", "-", body.shelf.lower()).strip("-")
        try:
            raw = base64.b64decode(body.data_base64, validate=True)
        except ValueError:
            raise HTTPException(422, "That file didn't arrive in one piece. Try again.")
        if len(raw) > MAX_FILE_MB * 1_000_000:
            raise HTTPException(413, f"Files up to {MAX_FILE_MB} MB, please.")
        folder = library() / shelf if shelf else library()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(raw)
        try:
            n = index_file(path)
        except Exception as e:
            raise HTTPException(422, f"I saved it but couldn't read it ({type(e).__name__}).")
        return {"ok": True, "passages": n, "path": path.relative_to(library()).as_posix()}

    @router.post("/reindex")
    def reindex():
        return index_all()

    class Repo(BaseModel):
        repo: str = Field(min_length=3, max_length=200)
        shelf: str = Field(default="", max_length=40)

    @router.post("/github")
    def github(body: Repo, request: Request):
        """Add a public GitHub repository's documents as a collection (this PC only: it downloads).
        It runs in the background (a big tutorial takes minutes to index); GET /github shows progress."""
        import contextvars
        import threading
        local_only(request)
        try:
            repo_name(body.repo)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if _repo_status["busy"]:
            raise HTTPException(409, "Already adding a repository. Try again when it's done.")
        _repo_status.update(busy=True, error="", last=f"Adding {body.repo.strip()}...")

        def run():
            try:
                out = add_repo(body.repo, body.shelf)
                _repo_status["last"] = (f"Added {out['repo']} as the \"{out['collection']}\" collection: {out['files']} files, "
                                        f"{out['passages']} passages (licence: {out['licence']}).")
            except ValueError as e:
                _repo_status.update(error=str(e), last="")
            except httpx.HTTPError:
                _repo_status.update(error="GitHub couldn't be reached. Check the internet and try again.", last="")
            except Exception as e:
                log.exception("adding %s failed", body.repo)
                _repo_status.update(error=f"Adding it failed ({type(e).__name__}).", last="")
            finally:
                _repo_status["busy"] = False
        threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True).start()
        return {"started": True, **_repo_status}

    @router.get("/github")
    def github_status():
        return dict(_repo_status)

    @router.get("/collections")
    def list_collections():
        return {"collections": collections()}

    @router.get("/search")
    def find(q: str, shelf: str = ""):
        found = search(q[:300], 6, shelf)
        return {"results": [{k: v for k, v in c.items() if k != "path"} for c in found],
                "answer": cited_answer(q, found)}


@p.job(300)
def watch_folder(_now) -> list[dict]:
    """Every 5 minutes: index files you copied into the knowledge folder. Silent unless something's new."""
    result = index_all()
    if result.get("added"):
        n = result["added"]
        return [{"title": "Knowledge library updated", "body": f"Read {n} new or changed file{'s' if n != 1 else ''}.",
                 "spoken": ""}]
    return []


p.mock_rule(24.8, ("my library", "my books", "my notes", "knowledge library", "in my library"), "knowledge_search",
            lambda t, m: {"query": after(t, r"\b(search|look|find)\b.*?\b(my library|my books|my notes)\b(\s+for)?") or t})
