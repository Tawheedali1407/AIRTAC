"""Web: search the internet like a search engine and read pages like a browser's reader view.

Search needs no key. It tries, in order: your own SearXNG server (if you set its address),
Tavily (if TAVILY_API_KEY is in .env), DuckDuckGo (the open source `ddgs` package) and
Wikipedia. The reader opens one page and keeps only its text, so the model can summarise it;
it never opens addresses inside your own network (see airtac/websearch.py).
With [general] offline = true it shows labelled sample results instead.

Also here: start_work, so you can start a Work mode job by voice ("in work mode, research ...").
"""
import re
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from airtac import config, connections, websearch
from airtac.plugin_api import SAMPLE_TAG, Tool, ToolResult, after, plugin, schema

p = plugin(__name__)


def _test_tavily(http) -> tuple[bool, str]:
    r = http.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {p.secret('TAVILY_API_KEY')}"},
                  json={"query": "AIRTAC connection test", "max_results": 1})
    if r.status_code in (401, 403):
        return False, "Tavily refused the key. Copy it again from app.tavily.com."
    if r.status_code in (429, 432, 433):
        return True, "The key works, but this month's free searches are used up. DuckDuckGo answers until they reset."
    r.raise_for_status()
    return True, "Connected. Tavily answers first; DuckDuckGo and Wikipedia stay as backups."


def _test_gcse(http) -> tuple[bool, str]:
    r = http.get("https://www.googleapis.com/customsearch/v1", params={"cx": p.secret("GOOGLE_CSE_ID"), "q": "test", "num": 1},
                 headers={"X-goog-api-key": p.secret("GOOGLE_CSE_KEY")})
    if r.status_code in (400, 401, 403):
        return False, "Google refused the key or the engine ID. Check both in Google Cloud and Programmable Search."
    r.raise_for_status()
    return True, "Connected. Google (your key) is added to All results."


p.connector(id="google_cse", name="Google Programmable Search", order=21,
            get_key="https://developers.google.com/custom-search/v1/overview",
            about="Optional: Google results through your own key and search engine ID. Google closed it to new "
                  "customers and ends it on 1 January 2027; the free Google engine in All results needs no key.",
            powers=["web search"], fields=[connections.Field("GOOGLE_CSE_KEY", "API key", placeholder="AIza..."),
                                           connections.Field("GOOGLE_CSE_ID", "Search engine ID", placeholder="0123...:abc")],
            test=_test_gcse, group="Search and dev tools", logo="google")
p.connector(id="tavily", name="Tavily web search", order=20, get_key="https://app.tavily.com/",
            about="Better search results for the Web page, Research and Work mode. Search works without it (DuckDuckGo).",
            powers=["web search", "Research sources"], fields=[connections.Field("TAVILY_API_KEY", "API key", placeholder="tvly-...")],
            test=_test_tavily, group="Search and dev tools")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
PROVIDER_NAMES = {"ddg": "DuckDuckGo", "bing": "Bing", "brave": "Brave", "google": "Google", "wikipedia": "Wikipedia",
                  "tavily": "Tavily", "searxng": "your SearXNG", "gcse": "Google (your key)", "sample": "sample data"}
# "All results" asks these at once (no keys needed), plus any you've set up: SearXNG, Tavily, Google's key.
FREE_ENGINES = ["ddg", "bing", "brave", "google", "wikipedia"]


class PageError(Exception):
    """A page couldn't be read. The message is a sentence for the user."""


def providers() -> list[str]:
    chosen = ["searxng"] if p.setting("searxng_url") else []
    if p.secret("TAVILY_API_KEY"):
        chosen.append("tavily")
    return chosen + ["ddg", "wikipedia"]


def search_results(query: str, n: int = 8) -> tuple[list[dict], str, bool]:
    """(results, which search answered, sample?). Results: title, url, snippet, source."""
    if config.OFFLINE:
        rows = [{"title": r["title"], "url": r["url"], "snippet": r["content"], "source": "Sample"}
                for r in p.sample("web_search")["results"]]
        return rows[:n], "sample", True
    found, provider = websearch.search(query, n, providers(), p.setting("searxng_url", ""), p.secret("TAVILY_API_KEY"),
                                       p.setting("region", "in-en"))
    return found, provider, False


def engines() -> list[str]:
    """The engines the Web page can ask: the free ones, plus the ones you've set up."""
    extra = (["searxng"] if p.setting("searxng_url") else []) + (["tavily"] if p.secret("TAVILY_API_KEY") else [])
    extra += ["gcse"] if p.secret("GOOGLE_CSE_KEY") and p.secret("GOOGLE_CSE_ID") else []
    return FREE_ENGINES + extra


def search_engines(query: str, chosen: list[str], n: int = 8) -> dict:
    """Ask the chosen engines at once. One engine: its own list. Several: one merged "All results" list."""
    if config.OFFLINE:
        rows = [{"title": r["title"], "url": r["url"], "snippet": r["content"], "source": "Sample"}
                for r in p.sample("web_search")["results"]]
        return {"results": rows[:n], "answered": ["sample"], "failed": {}, "sample": True}
    got, failed = websearch.search_many(query, chosen, n, searxng_url=p.setting("searxng_url", ""),
                                        tavily_key=p.secret("TAVILY_API_KEY"), gcse_key=p.secret("GOOGLE_CSE_KEY"),
                                        gcse_cx=p.secret("GOOGLE_CSE_ID"), region=p.setting("region", "in-en"))
    answered = [e for e in chosen if got.get(e)]
    for e in chosen:
        if e in got and not got[e]:
            failed[e] = "found nothing"
    rows = got[chosen[0]] if len(chosen) == 1 else websearch.merge({e: got[e] for e in answered}, max(n, 12))
    return {"results": rows or [], "answered": answered, "failed": failed, "sample": False}


def read_url(url: str) -> tuple[dict, bool]:
    """(page, sample?). Raises PageError."""
    url = (url or "").strip()
    if config.OFFLINE:
        pages = p.sample("web_pages")["pages"]
        if url in pages:
            return {"url": url, **pages[url], "truncated": False, "links": []}, True
        raise PageError("AIRTAC is in offline mode, so it can't open web pages.")
    try:
        return websearch.read(url), False
    except websearch.Blocked as e:
        raise PageError(str(e))
    except httpx.HTTPStatusError as e:
        raise PageError(f"That site answered with an error ({e.response.status_code}).")
    except httpx.HTTPError:
        raise PageError("I couldn't reach that page. Check the address and try again.")


def domain(url: str) -> str:
    host = urlparse(url).hostname or url
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------- tools

def web_search(query: str) -> ToolResult:
    rows, provider, sample = search_results(query, 6)
    if not rows:
        return ToolResult(f"I found nothing for {query}. Try other words.", ok=provider != "", data={"links": []})
    top = rows[0]
    first = re.split(r"(?<=[.!?])\s+", top["snippet"])[0].rstrip(".") if top["snippet"] else ""
    spoken = f"Top result for {query}: {top['title']}." + (f" {first}." if first else "")
    tag = f"{SAMPLE_TAG} canned results" if sample else f"via {PROVIDER_NAMES.get(provider, provider)}"
    lines = [f"Web search: {query} ({tag})"]
    lines += [f"{i}. {r['title']} ({domain(r['url'])})\n   {r['snippet'][:180]}\n   {r['url']}" for i, r in enumerate(rows[:5], 1)]
    return ToolResult(spoken, "\n".join(lines), data={"links": [r["url"] for r in rows], "results": rows})


def read_page(url: str) -> ToolResult:
    match = URL_RE.search(url or "")
    if not match:
        return ToolResult("Which page? Give me its full address, starting with http.", ok=False)
    try:
        page, sample = read_url(match.group(0))
    except PageError as e:
        return ToolResult(str(e), str(e), ok=False)
    gist = websearch.summary_lines(page["text"], 2)
    spoken = f"I opened {page['title'] or domain(page['url'])}." + (f" It says: {gist}" if gist else "")
    head = f"{page['title']}\n{page['url']}" + (f"\n{SAMPLE_TAG}" if sample else "")
    return ToolResult(spoken, f"{head}\n\n{page['text'][:1300]}", data={"page": page})


def start_work(goal: str) -> ToolResult:
    from airtac import work  # imported here: work imports the tool registry
    goal = (goal or "").strip()
    if len(goal) < 3:
        return ToolResult("What should the job be? For example: research free GPU options for students.")
    job = work.start(goal)
    return ToolResult("Started that job in Work mode. I'll plan it, work through the steps and write a report. "
                      "Open Work mode in the menu to follow it.", f"Job {job['id']}: {goal}")


def convert_report(format: str = "pdf") -> ToolResult:
    """Your latest Work mode report as a PDF, Word or PowerPoint file (only when you ask)."""
    from airtac import work
    fmt = {"word": "docx", "doc": "docx", "docx": "docx", "powerpoint": "pptx", "ppt": "pptx", "pptx": "pptx",
           "slides": "pptx"}.get((format or "pdf").lower().strip(), "pdf")
    job = work.latest_report()
    if job is None:
        return ToolResult("There's no finished Work mode report to convert yet.", ok=False)
    try:
        url = work.convert(job["id"], fmt)
    except Exception as e:
        log_name = type(e).__name__
        return ToolResult(f"I couldn't make that file ({log_name}).", ok=False)
    return ToolResult(f"Done. {job['title']} is now a {work.FORMATS[fmt]} in your files.", f"{work.FORMATS[fmt]}: {url}",
                      data={"links": [url]})


p.add(Tool("web_search", "Search the web for anything the other tools don't cover. Returns titles, snippets and links.",
           schema({"query": {"type": "string", "description": "What to search for"}}, required=("query",)),
           "read", web_search))
p.add(Tool("read_page", "Open one web page by its address and read its text, to summarise it or answer questions about it.",
           schema({"url": {"type": "string", "description": "The full address, starting with http"}}, required=("url",)),
           "read", read_page))
p.add(Tool("start_work", "Start a background job with several steps (research, reading pages, writing a report) "
           "when the user asks for a bigger piece of work.",
           schema({"goal": {"type": "string", "description": "The whole job in the user's words"}}, required=("goal",)),
           "local", start_work))
p.add(Tool("convert_report", "Turn the latest Work mode report into a file, only when the user asks to convert it: "
           "pdf, docx (Word) or pptx (PowerPoint).",
           schema({"format": {"type": "string", "description": "pdf, docx or pptx"}}), "local", convert_report))


# ---------------------------------------------------------------- the Web page

@p.routes
def api(router) -> None:
    @router.get("/engines")
    def list_engines():
        return {"engines": [{"id": e, "name": PROVIDER_NAMES[e]} for e in engines()]}

    @router.get("/search")
    def search(q: str, n: int = 8, provider: str = "auto"):
        """provider: auto (the first engine that answers), all (every engine at once, merged), or one engine."""
        if provider != "auto":
            chosen = engines() if provider == "all" else [provider] if provider in engines() else []
            if not chosen:
                raise HTTPException(400, "That search engine isn't available.")
            out = search_engines(q[:300], chosen, max(1, min(n, 10)))
            names = [PROVIDER_NAMES.get(e, e) for e in out["answered"]]
            note = ("Sample results: AIRTAC is in offline mode." if out["sample"] else
                    "No results right now. The search engines may be busy; try again in a minute." if not out["results"] else "")
            return {"query": q, "provider": provider, "results": out["results"], "answered": names, "sample": out["sample"],
                    "failed": {PROVIDER_NAMES.get(k, k): v for k, v in out["failed"].items()}, "note": note}
        rows, provider, sample = search_results(q[:300], max(1, min(n, 10)))
        note = ""
        if sample:
            note = "Sample results: AIRTAC is in offline mode."
        elif not rows:
            note = "No results right now. The search services may be busy; try again in a minute."
        return {"query": q, "results": rows, "provider": provider, "sample": sample, "note": note}

    @router.get("/read")
    def read(url: str):
        try:
            page, sample = read_url(url[:2000])
        except PageError as e:
            raise HTTPException(422, str(e))
        return {**page, "sample": sample}


@p.dashboard
def page(pg: dict) -> None:
    from airtac import memory
    pg["stats"] = [{"label": "Searches and pages read", "value": memory.plugin_usage(p.id)["uses"]},
                   {"label": "Search order", "value": " > ".join(PROVIDER_NAMES[x] for x in providers())}]


# ---------------------------------------------------------------- rules for the no-model mode

p.mock_rule(-2, ("convert", "as a pdf", "into a pdf", "as word", "as a word", "into slides", "as powerpoint"), "convert_report",
            lambda t, m: {"format": next((f for f in ("word", "docx", "powerpoint", "pptx", "slides", "ppt") if f in t.lower()), "pdf")})
p.mock_rule(-1, ("work mode", "start a job", "background job"), "start_work",
            lambda t, m: {"goal": after(t, r"\b(in work mode|work mode|start a job|background job)\b\s*(to|for)?") or t})
p.mock_rule(5.5, ("this page", "this link", "this article", "read page", "read the page", "http", "https"), "read_page",
            lambda t, m: {"url": t})
p.mock_rule(25, ("search", "look up", "google"), "web_search",
            lambda t, m: {"query": after(t, r"\b(search(\s+the\s+web)?(\s+for)?|look up|google)\b") or t})
