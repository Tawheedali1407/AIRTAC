"""Can a site open as a tab inside AIRTAC? Only when the site itself allows being shown inside another page.

A site says no with one of two response headers:
    X-Frame-Options: DENY or SAMEORIGIN
    Content-Security-Policy: frame-ancestors ...   (wins over X-Frame-Options when both are sent)
AIRTAC reads them and obeys. It never strips, changes or proxies them to force a site into a tab:
those headers protect you from clickjacking. A site that says no opens in your browser instead.

The header check always runs. If Playwright is installed, a render check also loads the site in a
hidden browser inside a frame, because some sites block frames with script instead of headers.
Results are kept for 7 days. Opening a page checks only sites not checked yet (headers only); Re-probe tabs
and scripts/probe_embed.py check again, with the render check too.
"""
import json
import threading
import time
from urllib.parse import urlsplit

from airtac import memory, websearch

TTL = 7 * 24 * 3600
_one_browser = threading.Lock()   # a render check starts a browser: one at a time on an 8 GB laptop


def _directives(policy: str) -> dict[str, list[str]]:
    out = {}
    for part in policy.split(";"):
        words = part.strip().split()
        if words:
            out.setdefault(words[0].lower(), [w.lower() for w in words[1:]])
    return out


def verdict(headers) -> tuple[bool, str]:
    """(may it be framed by AIRTAC, why). headers: anything with .get_list() or a dict of lists/strings."""
    def values(name):
        if hasattr(headers, "get_list"):
            return headers.get_list(name)
        v = headers.get(name) or headers.get(name.lower()) or []
        return v if isinstance(v, list) else [v]

    for policy in values("content-security-policy"):        # Report-Only policies don't block, so they're skipped
        sources = _directives(policy).get("frame-ancestors")
        if sources is None:
            continue
        if "*" in sources:
            return True, "The site allows any page to show it."
        if not sources or "'none'" in sources:
            return False, "The site forbids being shown inside other pages (frame-ancestors 'none')."
        return False, "The site only lets its own pages show it (frame-ancestors)."
    for xfo in values("x-frame-options"):
        v = xfo.strip().lower()
        if v in ("deny", "sameorigin") or v.startswith("allow-from"):
            return False, f"The site forbids being shown inside other pages (X-Frame-Options: {xfo.strip().upper()})."
    return True, "No header stops it from opening in a tab."


def render_check(url: str, wait_ms: int = 6000) -> tuple[bool | None, str]:
    """Load the site inside a frame in a hidden browser. (None, why) when Playwright isn't installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "render check skipped (Playwright isn't installed)"
    try:
        with _one_browser, sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.set_content(f'<iframe src="{url}" style="width:1000px;height:700px" '
                             'sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>')
            page.wait_for_timeout(wait_ms)
            frame = page.frames[1] if len(page.frames) > 1 else None
            ok = bool(frame) and not frame.url.startswith("chrome-error") and frame.url != "about:blank"
            if ok:
                try:
                    ok = frame.evaluate("document.body ? document.body.children.length : 0") > 0
                except Exception:   # a cross-origin frame we can't read still rendered
                    ok = True
            browser.close()
        return ok, "rendered in a frame" if ok else "didn't render in a frame"
    except Exception as e:
        return None, f"render check failed ({type(e).__name__})"


def probe(url: str, render: bool = True) -> dict:
    """Check one address. {"ok", "why", "final" (origin after redirects), "at"}."""
    at = int(time.time())
    try:
        final, status, headers = websearch.head(url)
    except websearch.NotFound as e:      # no internet right now: don't remember a "no" for 7 days
        return {"ok": False, "why": f"{e} Check again when you're online.", "final": "", "at": at, "retry": True}
    except websearch.Blocked as e:
        return {"ok": False, "why": str(e), "final": "", "at": at}
    except Exception as e:
        return {"ok": False, "why": f"Couldn't reach the site ({type(e).__name__}).", "final": "", "at": at, "retry": True}
    f = urlsplit(final)
    origin = f"{f.scheme}://{f.netloc}"
    if status >= 400:
        return {"ok": False, "why": f"The site answered {status}.", "final": origin, "at": at}
    ok, why = verdict(headers)
    if ok and f.scheme != "https":
        ok, why = False, "It isn't served over https."
    if ok and render:
        seen, how = render_check(url)
        if seen is False:
            ok = False
        why = f"{why} ({how})"
    return {"ok": ok, "why": why, "final": origin, "at": at}


def cache() -> dict:
    try:
        return json.loads(memory.kv_get("desktop", "embed") or "{}")
    except ValueError:
        return {}


def save(results: dict) -> None:
    memory.kv_set("desktop", "embed", json.dumps(results))


def fresh(entry: dict | None, url: str, now: float | None = None) -> bool:
    return bool(entry) and entry.get("url") == url and (now or time.time()) - entry.get("at", 0) < TTL and not entry.get("retry")
