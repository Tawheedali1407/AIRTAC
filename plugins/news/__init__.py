"""News for a CS student: Hacker News, new arXiv papers and Codeforces contests, as tools and as
the "News around you" section on Home. Public data: no key needed.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from airtac.plugin_api import SAMPLE_TAG, Tool, ToolResult, fetch, plugin, schema, unreachable

p = plugin(__name__)


# --- hn_top

def _hn_result(hits: list[dict], tag: str = "") -> ToolResult:
    top = hits[:5]
    if not top:
        return ToolResult("Hacker News returned no stories right now.")
    spoken = f"The top Hacker News story is {top[0]['title']}, with {top[0]['points']} points. The rest are on screen."
    lines = [f"Hacker News top stories {tag}".strip()]
    for i, h in enumerate(top, 1):
        link = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        lines.append(f"{i}. {h['title']} ({h['points']} points, {h['num_comments']} comments)\n   {link}")
    return ToolResult(spoken, "\n".join(lines))


def hn_top_fixture() -> ToolResult:
    return _hn_result(p.sample("hn")["hits"], SAMPLE_TAG)


def hn_top_live() -> ToolResult:
    # Algolia's HN API gives the whole front page in one call (the official API needs one call per story).
    data = fetch("https://hn.algolia.com/api/v1/search", params={"tags": "front_page", "hitsPerPage": 5})
    if data is None:
        return unreachable("Hacker News")
    return _hn_result(sorted(data["hits"], key=lambda h: h.get("points") or 0, reverse=True))


# --- arxiv_search

def _arxiv_result(query: str, papers: list[dict], tag: str = "") -> ToolResult:
    if not papers:
        return ToolResult(f"I couldn't find papers on {query}.")
    p = papers[0]
    spoken = f"I found {len(papers)} papers on {query}. The first is {p['title']}. Details are on screen."
    lines = [f"arXiv: {query} {tag}".strip()]
    for p in papers:
        lines.append(f"- {p['title']} ({p['published']}, {', '.join(p['authors'][:2])})\n"
                     f"  {p['summary'][:160]}\n  https://arxiv.org/abs/{p['id']}")
    return ToolResult(spoken, "\n".join(lines))


def arxiv_search_fixture(query: str) -> ToolResult:
    papers = p.sample("arxiv")["papers"]
    words = set(re.findall(r"[a-z]{3,}", query.lower()))

    def score(p):  # how many query words appear in the title or summary
        return sum(w in (p["title"] + " " + p["summary"]).lower() for w in words)

    ranked = sorted(papers, key=score, reverse=True)
    matches = [p for p in ranked if score(p) > 0] or ranked  # no match: show the sample list anyway
    return _arxiv_result(query, matches[:4], SAMPLE_TAG)


def arxiv_search_live(query: str) -> ToolResult:
    xml = fetch("https://export.arxiv.org/api/query", as_text=True, params={
        "search_query": f'all:"{query}"', "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 4})
    if xml is None:
        return unreachable("arXiv")
    return _arxiv_result(query, parse_arxiv(xml))


def parse_arxiv(xml: str) -> list[dict]:
    """arXiv answers in Atom XML; turn each entry into the same dict shape as tests/fixtures/arxiv.json."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    papers = []
    # A feed never needs a DOCTYPE or entities; refusing them stops "billion laughs" style XML bombs.
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        return papers
    for e in ET.fromstring(xml).findall("a:entry", ns):  # nosec B314 (Python's bundled expat refuses entity expansion attacks; no external entities)
        papers.append({
            "id": e.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1],
            "title": " ".join(e.findtext("a:title", "", ns).split()),
            "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
            "published": e.findtext("a:published", "", ns)[:7],
            "summary": " ".join(e.findtext("a:summary", "", ns).split()),
        })
    return papers


# --- cf_contests

def _cf_result(contests: list[dict], tag: str = "") -> ToolResult:
    upcoming = sorted((c for c in contests if c["phase"] == "BEFORE"), key=lambda c: c["startTimeSeconds"])[:5]
    if not upcoming:
        return ToolResult("There are no upcoming Codeforces contests listed.")

    def when(c):  # local time of this machine
        return datetime.fromtimestamp(c["startTimeSeconds"]).strftime("%a %d %b, %I:%M %p")

    first = upcoming[0]
    spoken = f"The next Codeforces contest is {first['name']} on {when(first)}. {len(upcoming)} are listed on screen."
    lines = [f"Upcoming Codeforces contests {tag}".strip()]
    for c in upcoming:
        lines.append(f"- {c['name']}: {when(c)}, {c['durationSeconds'] // 3600}h{c['durationSeconds'] % 3600 // 60:02d}m")
    return ToolResult(spoken, "\n".join(lines))


def cf_contests_fixture() -> ToolResult:
    return _cf_result(p.sample("codeforces")["result"], SAMPLE_TAG)


def cf_contests_live() -> ToolResult:
    data = fetch("https://codeforces.com/api/contest.list", params={"gym": "false"})
    if not data or data.get("status") != "OK":
        return unreachable("Codeforces")
    return _cf_result(data["result"])


QUERY_ARG = {"query": {"type": "string", "description": "What to search for"}}

p.add(Tool("hn_top", "Get the current top stories on Hacker News.",
              schema(), "read", hn_top_fixture, hn_top_live))
p.add(Tool("arxiv_search", "Find recent research papers on arXiv about a topic.",
              schema(QUERY_ARG, required=("query",)), "read", arxiv_search_fixture, arxiv_search_live))
p.add(Tool("cf_contests", "List upcoming Codeforces programming contests.",
              schema(), "read", cf_contests_fixture, cf_contests_live))


# ---------------------------------------------------------------- "News around you" on Home
#
# Why it's built like this (it used to take 16 s when arXiv was slow):
# - the three sources are fetched side by side, each with a short timeout, so one slow site can't hold up the rest;
# - every good answer is saved (memory kv "news/last:<id>"); when a source fails, the last good copy is shown
#   with its time, and the note says which site didn't answer. Nothing is ever made up;
# - arXiv's search API is often slow, so its RSS feed (rss.arxiv.org, new cs.AI papers) is the fallback.

FEED_TIMEOUT = 6           # seconds per source on Home (the tools in chat keep fetch()'s usual 8)
ARXIV_RSS = "https://rss.arxiv.org/rss/cs.AI"


def _section(key: str, title: str, source: str, items=None, sample=False, note="") -> dict:
    return {"id": key, "title": title, "source": source, "items": items or [], "sample": sample, "note": note}


def _offline() -> bool:
    from airtac import config
    return config.OFFLINE


def _remember(section: dict) -> dict:
    """Keep a good answer as the fallback for next time."""
    if section["items"] and not section["sample"]:
        from airtac import memory
        import json
        memory.kv_set("news", f"last:{section['id']}", json.dumps({"items": section["items"], "at": datetime.now().isoformat(timespec="minutes")}))
    return section


def _last_good(section: dict, why: str) -> dict:
    """A source failed: show its last good copy (with when), or just say what failed."""
    from airtac import memory
    import json
    try:
        saved = json.loads(memory.kv_get("news", f"last:{section['id']}") or "null")
    except ValueError:
        saved = None
    if saved and saved.get("items"):
        when = datetime.fromisoformat(saved["at"]).strftime("%d %b, %I:%M %p")
        return {**section, "items": saved["items"], "note": f"{why} Showing the copy from {when}.", "stale": True}
    return {**section, "note": f"{why} Try again soon."}


def tech() -> dict:
    sec = _section("tech", "Tech", "Hacker News")
    if _offline():
        hits = p.sample("hn")["hits"]
    else:
        data = fetch("https://hn.algolia.com/api/v1/search", params={"tags": "front_page", "hitsPerPage": 5}, timeout=FEED_TIMEOUT, retry=False)
        if data is None:
            return _last_good(sec, "Hacker News didn't answer.")
        hits = data.get("hits", [])
    hits = sorted(hits, key=lambda h: h.get("points") or 0, reverse=True)[:5]
    items = [{"title": h["title"], "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
              "meta": f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments"} for h in hits]
    return _remember({**sec, "items": items, "sample": _offline()})


def parse_arxiv_rss(xml: str, topic: str, n: int = 3) -> list[dict]:
    """New cs.AI papers from arXiv's RSS feed, the ones matching the topic's words first."""
    if not xml or "<!DOCTYPE" in xml or "<!ENTITY" in xml:     # a feed never needs these (XML bombs)
        return []
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    try:
        root = ET.fromstring(xml)  # nosec B314 (Python's bundled expat refuses entity expansion attacks; no external entities)
    except ET.ParseError:
        return []
    words = [w for w in re.findall(r"[a-z]{3,}", topic.lower()) if w not in ("and", "the", "for")]
    papers = []
    for item in root.iter("item"):
        title = " ".join((item.findtext("title") or "").split())
        link = (item.findtext("link") or "").strip()
        text = (title + " " + (item.findtext("description") or "")).lower()
        if not title or not link.startswith("https://arxiv.org/abs/"):
            continue
        creators = (item.findtext("dc:creator", "", ns) or "").split(",")
        papers.append({"title": title, "url": link, "authors": [c.strip() for c in creators if c.strip()][:2],
                       "score": sum(w in text for w in words)})
    papers.sort(key=lambda x: x["score"], reverse=True)       # stable: equal scores keep the feed's order
    return papers[:n]


def research() -> dict:
    topic = p.setting("topic", "LLM agents")
    sec = _section("research", f"Research on {topic}", "arXiv")
    if _offline():
        items = [{"title": x["title"], "url": f"https://arxiv.org/abs/{x['id']}",
                  "meta": f"{', '.join(x['authors'][:2])}, {x['published']}"} for x in p.sample("arxiv")["papers"][:3]]
        return {**sec, "items": items, "sample": True}
    xml = fetch("https://export.arxiv.org/api/query", as_text=True, timeout=FEED_TIMEOUT, retry=False, params={
        "search_query": f'all:"{topic}"', "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 3})
    papers = parse_arxiv(xml) if xml else []
    if papers:
        items = [{"title": x["title"], "url": f"https://arxiv.org/abs/{x['id']}",
                  "meta": f"{', '.join(x['authors'][:2])}, {x['published']}"} for x in papers]
        return _remember({**sec, "items": items})
    rss = parse_arxiv_rss(fetch(ARXIV_RSS, as_text=True, timeout=FEED_TIMEOUT, retry=False), topic)   # the fallback
    if rss:
        items = [{"title": x["title"], "url": x["url"], "meta": ", ".join(x["authors"]) or "new on arXiv"} for x in rss]
        return _remember({**sec, "items": items, "source": "arXiv (new cs.AI papers)",
                          "note": "arXiv's search didn't answer, so these are today's new AI papers instead."})
    return _last_good(sec, "arXiv didn't answer.")


def contests() -> dict:
    sec = _section("contests", "Contests", "Codeforces")
    if _offline():
        rows = p.sample("codeforces")["result"]
    else:
        data = fetch("https://codeforces.com/api/contest.list", params={"gym": "false"}, timeout=FEED_TIMEOUT, retry=False)
        if not data or data.get("status") != "OK":
            return _last_good(sec, "Codeforces didn't answer.")
        rows = data["result"]
    upcoming = sorted((c for c in rows if c["phase"] == "BEFORE"), key=lambda c: c["startTimeSeconds"])[:3]
    items = []
    for c in upcoming:
        start = datetime.fromtimestamp(c["startTimeSeconds"])
        hours, minutes = divmod(c["durationSeconds"] // 60, 60)
        items.append({"title": c["name"], "url": f"https://codeforces.com/contests/{c['id']}",
                      "meta": f"{start:%a %d %b, %I:%M %p}, {hours} h {minutes:02d} min"})
    return _remember({**sec, "items": items, "sample": _offline()})


def all_sections() -> list[dict]:
    """The three sources side by side. Each thread gets a copy of this request's context, so fetch()
    still checks the news plugin's permissions."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(contextvars.copy_context().run, fn) for fn in (tech, research, contests)]
        return [j.result() for j in jobs]


@p.feed
def news() -> list[dict]:
    return all_sections()


@p.dashboard
def page(pg: dict) -> None:
    t, r, c = all_sections()
    pg["stats"] = [{"label": "Upcoming contests", "value": len(c["items"])}]
    pg["lists"] = [{"title": r["title"], "items": r["items"], "empty": r["note"] or "No papers."},
                   {"title": "Tech news", "items": t["items"], "empty": t["note"] or "No stories."},
                   {"title": "Next contests", "items": c["items"], "empty": c["note"] or "No upcoming contests."}]


# ---------------------------------------------------------------- rules for the no-model mode

def _arxiv_args(text, _messages):
    filler = r"\b(new|latest|recent|papers?|on|about|arxiv|find|show|me|any|some|search|for|the|get|research|today'?s?|what|are)\b"
    query = " ".join(re.sub(r"[^\w\s-]", " ", re.sub(filler, " ", text, flags=re.IGNORECASE)).split())
    return {"query": query or "AI agents"}


p.mock_rule(22, ("hacker news", "hn", "top stories"), "hn_top")
p.mock_rule(23, ("paper", "papers", "arxiv"), "arxiv_search", _arxiv_args)
p.mock_rule(24, ("contest", "contests", "codeforces"), "cf_contests")
