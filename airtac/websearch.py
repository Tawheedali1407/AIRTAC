"""Web search and a page reader that need no API key.

    results = search("nebius token factory", providers=["ddg", "wikipedia"])
    page = read("https://example.com/article")

search() tries each provider in turn and returns the first that gives results; search_many() asks
several at once and merge() makes one "All results" list:
    searxng    your own SearXNG server (open source metasearch; set its URL)
    tavily     Tavily's search API (needs a key)
    ddg, bing, brave, google, mojeek   those engines' own results pages, read by the open source `ddgs` package (no key)
    gcse       Google Programmable Search (your key and engine ID; Google ends it on 1 Jan 2027)
    wikipedia  Wikipedia's search API (no key, always there as the last resort)
Each result is {"title", "url", "snippet", "source"}.

read() fetches one page and returns its readable text, like a browser's reader view, so the
assistant can summarise it. It only goes to public internet addresses: a page can't make it
reach your router, your other devices or this computer (that trick is called SSRF).
Same file in AIRTAC (app/websearch.py) and AIRTAC Advance (airtac/websearch.py).
"""
import ipaddress
import logging
import re
import socket
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse

import httpx

log = logging.getLogger("airtac.websearch")

USER_AGENT = "Mozilla/5.0 (compatible; AIRTAC personal assistant)"
# Some sites send other headers to unknown programs; the embed check needs what a browser gets.
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
MAX_PAGE_BYTES = 2_000_000
MAX_TEXT = 20_000
MAX_REDIRECTS = 4


class Blocked(Exception):
    """The address isn't a public web page (or isn't allowed)."""


class NotFound(Blocked):
    """The site's name couldn't be looked up (no internet, or a typo). Worth trying again later."""


# ---------------------------------------------------------------- search providers

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def _result(title, url, snippet, source) -> dict:
    return {"title": _clean(title)[:200] or url, "url": url, "snippet": _clean(snippet)[:400], "source": source}


def search_searxng(query: str, n: int, base_url: str) -> list[dict]:
    r = httpx.get(base_url.rstrip("/") + "/search", params={"q": query, "format": "json"},
                  headers={"User-Agent": USER_AGENT}, timeout=10)
    r.raise_for_status()
    return [_result(x.get("title"), x.get("url"), x.get("content"), "SearXNG")
            for x in r.json().get("results", [])[:n] if x.get("url")]


def search_tavily(query: str, n: int, api_key: str) -> list[dict]:
    r = httpx.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {api_key}"},
                   json={"query": query, "max_results": n}, timeout=12)
    r.raise_for_status()
    return [_result(x.get("title"), x.get("url"), x.get("content"), "Tavily")
            for x in r.json().get("results", [])[:n] if x.get("url")]


# Engines the open source `ddgs` package (MIT) reads without a key: it fetches each engine's own
# results page, the same page your browser gets. (Bing's paid API was retired in 2025; this needs no key.)
DDGS_ENGINES = {"ddg": ("duckduckgo", "DuckDuckGo"), "bing": ("bing", "Bing"), "brave": ("brave", "Brave"),
                "google": ("google", "Google"), "mojeek": ("mojeek", "Mojeek")}


def search_ddgs(query: str, n: int, engine: str = "ddg", region: str = "in-en") -> list[dict]:
    from ddgs import DDGS  # optional package: pip install ddgs
    backend, label = DDGS_ENGINES[engine]
    rows = DDGS().text(query, region=region, safesearch="moderate", max_results=n, backend=backend) or []
    return [_result(x.get("title"), x.get("href") or x.get("url"), x.get("body"), label)
            for x in rows[:n] if x.get("href") or x.get("url")]


def search_ddg(query: str, n: int, region: str = "in-en") -> list[dict]:
    return search_ddgs(query, n, "ddg", region)


def search_google_cse(query: str, n: int, api_key: str, cx: str) -> list[dict]:
    """Google Programmable Search (your own key and engine ID). The key goes in a header, never in the address.
    Google closed it to new customers and ends it on 1 Jan 2027."""
    r = httpx.get("https://www.googleapis.com/customsearch/v1", params={"cx": cx, "q": query, "num": min(n, 10)},
                  headers={"X-goog-api-key": api_key, "User-Agent": USER_AGENT}, timeout=10)
    r.raise_for_status()
    return [_result(x.get("title"), x.get("link"), x.get("snippet"), "Google (your key)")
            for x in r.json().get("items", [])[:n] if x.get("link")]


def search_wikipedia(query: str, n: int, lang: str = "en") -> list[dict]:
    r = httpx.get(f"https://{lang}.wikipedia.org/w/api.php",
                  params={"action": "query", "list": "search", "srsearch": query, "srlimit": n,
                          "format": "json", "utf8": 1},
                  headers={"User-Agent": USER_AGENT}, timeout=10)
    r.raise_for_status()
    return [_result(x["title"], f"https://{lang}.wikipedia.org/wiki/{quote(x['title'].replace(' ', '_'))}",
                    x.get("snippet", ""), "Wikipedia")
            for x in r.json().get("query", {}).get("search", [])[:n]]


def search(query: str, n: int = 8, providers: list[str] | None = None, searxng_url: str = "",
           tavily_key: str = "", region: str = "in-en") -> tuple[list[dict], str]:
    """(results, provider that answered). An empty list when every provider failed or found nothing."""
    query = query.strip()[:300]
    if not query:
        return [], ""
    for name in providers or ["searxng", "tavily", "ddg", "wikipedia"]:
        try:
            if name == "searxng" and searxng_url:
                found = search_searxng(query, n, searxng_url)
            elif name == "tavily" and tavily_key:
                found = search_tavily(query, n, tavily_key)
            elif name == "ddg":
                found = search_ddg(query, n, region)
            elif name in DDGS_ENGINES:
                found = search_ddgs(query, n, name, region)
            elif name == "wikipedia":
                found = search_wikipedia(query, n)
            else:
                continue
        except ImportError:
            log.info("ddgs isn't installed (pip install ddgs); trying the next search provider")
            continue
        except Exception as e:  # one provider down (or rate-limited) shouldn't stop the others
            log.warning("search provider %s failed: %s", name, type(e).__name__)
            continue
        if found:
            return found, name
    return [], ""


def _norm(url: str) -> str:
    """One key per page, so the same result from two engines is shown once."""
    u = urlparse(url)
    host = (u.hostname or "").removeprefix("www.")
    return f"{host}{u.path.rstrip('/')}?{u.query}" if u.query else f"{host}{u.path.rstrip('/')}"


def merge(lists: dict[str, list[dict]], n: int = 12) -> list[dict]:
    """"All results": every engine's list merged by reciprocal rank (a page near the top of several
    lists comes first). Each result says which engines found it."""
    scores, rows = {}, {}
    for engine, found in lists.items():
        for rank, r in enumerate(found):
            key = _norm(r["url"])
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            if key not in rows:
                rows[key] = {**r, "sources": []}
            if r["source"] not in rows[key]["sources"]:
                rows[key]["sources"].append(r["source"])
            if len(r.get("snippet", "")) > len(rows[key]["snippet"]):
                rows[key]["snippet"] = r["snippet"]
    best = sorted(scores, key=lambda k: -scores[k])[:n]
    return [{**rows[k], "source": ", ".join(rows[k]["sources"])} for k in best]


def search_many(query: str, engines: list[str], n: int = 8, budget: float = 12, **keys) -> tuple[dict, dict]:
    """Ask several engines at once. ({engine: results}, {engine: why it failed}). Slow engines are
    left out after `budget` seconds, so one stuck engine can't hold up the page."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor, wait
    query = query.strip()[:300]
    got, failed = {}, {}
    if not query:
        return got, failed

    def one(name):
        if name == "searxng":
            return search_searxng(query, n, keys.get("searxng_url", ""))
        if name == "tavily":
            return search_tavily(query, n, keys.get("tavily_key", ""))
        if name == "gcse":
            return search_google_cse(query, n, keys.get("gcse_key", ""), keys.get("gcse_cx", ""))
        if name == "wikipedia":
            return search_wikipedia(query, n)
        if name == "ddg":
            return search_ddg(query, n, keys.get("region", "in-en"))
        return search_ddgs(query, n, name, keys.get("region", "in-en"))

    pool = ThreadPoolExecutor(max_workers=max(1, len(engines)))
    jobs = {pool.submit(contextvars.copy_context().run, one, e): e for e in engines}
    done, late = wait(jobs, timeout=budget)
    for job in done:
        try:
            got[jobs[job]] = job.result()
        except ImportError:
            failed[jobs[job]] = "the ddgs package isn't installed"
        except Exception as e:  # one engine down (or rate-limited) shouldn't stop the others
            log.warning("search engine %s failed: %s", jobs[job], type(e).__name__)
            failed[jobs[job]] = "didn't answer" if isinstance(e, httpx.TimeoutException) else "failed"
    for job in late:
        failed[jobs[job]] = "too slow"
    pool.shutdown(wait=False, cancel_futures=True)
    return got, failed


# ---------------------------------------------------------------- headers and icons (no page text)

def head(url: str, timeout: float = 8, check=None) -> tuple[str, int, httpx.Headers]:
    """(final address, status, headers) after following up to 4 redirects, each hop checked to be a
    public address. The body isn't downloaded."""
    check = check or check_public
    seen = 0
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": BROWSER_UA}) as client:
        while True:
            check(url)
            with client.stream("GET", url) as r:
                if r.is_redirect and seen < MAX_REDIRECTS:
                    url, seen = urljoin(url, r.headers.get("location", "")), seen + 1
                    continue
                return url, r.status_code, r.headers


ICON_TYPES = {b"\x00\x00\x01\x00": "image/x-icon", b"\x89PNG": "image/png", b"GIF8": "image/gif", b"\xff\xd8\xff": "image/jpeg"}


def icon_type(data: bytes) -> str:
    """The image type from the file's first bytes. SVG is refused: it can carry script."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((t for magic, t in ICON_TYPES.items() if data.startswith(magic)), "")


def fetch_icon(site: str, timeout: float = 6, limit: int = 100_000, check=None) -> tuple[bytes, str]:
    """A site's own favicon (https://host/favicon.ico), at most 100 KB. (b"", "") if there's none."""
    check = check or check_public
    u = urlparse(site)
    url, seen = f"https://{u.hostname}/favicon.ico", 0
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": BROWSER_UA}) as client:
        while True:
            check(url)
            with client.stream("GET", url) as r:
                if r.is_redirect and seen < MAX_REDIRECTS:
                    url, seen = urljoin(url, r.headers.get("location", "")), seen + 1
                    continue
                if r.status_code != 200:
                    return b"", ""
                data = b""
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > limit:
                        return b"", ""
            kind = icon_type(data)
            return (data, kind) if kind else (b"", "")


# ---------------------------------------------------------------- the reader

def check_public(url: str) -> str:
    """Raise Blocked unless url is http(s) to a host whose every address is on the public internet."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Blocked("Only http and https web pages can be opened.")
    if parts.username or parts.password:
        raise Blocked("Addresses with a user name or password aren't opened.")
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise NotFound(f"I couldn't find the website {host}.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise Blocked("That address is inside a private network, so I won't open it.")
    return url


class _Reader(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button",
            "select", "iframe", "template", "canvas"}
    BLOCK = {"p", "div", "section", "article", "main", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
             "blockquote", "tr", "br", "dd", "dt", "figcaption", "td", "th"}

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.skip, self.in_title = base, 0, False
        self.title, self.parts, self.links, self._link = "", [], [], None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")
            if tag in ("h1", "h2", "h3"):
                self.parts.append("## ")
            elif tag == "li":
                self.parts.append("- ")
        elif tag == "a" and not self.skip:
            href = dict(attrs).get("href") or ""
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self._link = [urljoin(self.base, href), ""]

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif tag == "a" and self._link:
            url, text = self._link
            if text.strip() and url.startswith("http") and len(self.links) < 30:
                self.links.append({"text": _clean(text)[:100], "url": url})
            self._link = None
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)
            if self._link:
                self._link[1] += data

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n")]
        lines = [x for x in lines if x and x not in ("-", "##")]
        out, prev = [], ""
        for x in lines:  # drop exact repeats (menus that survived)
            if x != prev:
                out.append(x)
            prev = x
        return "\n".join(out)


def extract(html: str, url: str = "") -> dict:
    """Readable text from HTML: title, text (headings as '## ', list items as '- ') and links."""
    reader = _Reader(url)
    try:
        reader.feed(html)
        reader.close()
    except Exception:  # broken HTML: keep what was read
        pass
    text = reader.text()
    return {"url": url, "title": _clean(reader.title)[:200], "text": text[:MAX_TEXT],
            "truncated": len(text) > MAX_TEXT, "links": reader.links}


def read(url: str, timeout: float = 10, check=check_public) -> dict:
    """Fetch a public web page and return extract()'s dict. Raises Blocked or httpx.HTTPError."""
    seen = 0
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        while True:
            check(url)  # every hop: a public page may redirect to a private address
            with client.stream("GET", url) as r:
                if r.is_redirect and seen < MAX_REDIRECTS:
                    url, seen = urljoin(url, r.headers.get("location", "")), seen + 1
                    continue
                r.raise_for_status()
                kind = r.headers.get("content-type", "").lower()
                if kind and not any(t in kind for t in ("text/html", "text/plain", "application/xhtml")):
                    raise Blocked("That link isn't a web page (it may be a PDF or a download).")
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        break
                html = body.decode(r.encoding or "utf-8", errors="replace")
            if "text/plain" in kind:
                return {"url": url, "title": url, "text": html[:MAX_TEXT], "truncated": len(html) > MAX_TEXT, "links": []}
            return extract(html, url)


def summary_lines(text: str, limit: int = 3) -> str:
    """The first few real sentences of a page, for a spoken answer when no model is available."""
    body = " ".join(x for x in text.split("\n") if not x.startswith(("## ", "- ")) and len(x) > 60)
    sentences = re.split(r"(?<=[.!?])\s+", body)
    return " ".join(sentences[:limit]).strip()
