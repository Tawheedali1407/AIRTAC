"""QA crawl (Part H): opens every menu page, app page and tab of the running AIRTAC at desktop and phone size and
records what's broken (desktop in light mode, tablet and phone in dark mode). Read-only: it never presses buttons that change anything (only tabs) and sends no chat.

    .venv\\Scripts\\python scripts\\qa_crawl.py                 (AIRTAC must be running: start_airtac.bat)
    .venv\\Scripts\\python scripts\\qa_crawl.py --links         also HEAD-check external links (slower, goes online)
    .venv\\Scripts\\python scripts\\qa_crawl.py --base http://localhost:8772/

Per page and size: JavaScript errors, console errors, failed API calls (4xx/5xx; 423 "vault locked" is expected),
"failed to fetch"/"had a problem" text, images that didn't load, a blank page, sideways scrolling, loads over 3 s,
and new ERROR lines or tracebacks in data\\logs\\server.log. Writes data\\logs\\qa_report.md and prints failures only.
Exit code 0 = no failures.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "logs" / "server.log"
SIZES = {"desktop": (1280, 800, "light"), "tablet": (768, 1024, "dark"), "phone": (390, 844, "dark")}   # sizes and themes
SLOW_S = 3.0
EXPECTED_STATUS = {423}            # the vault is locked: the page shows the unlock box
ERROR_TEXT = ("failed to fetch", "had a problem", "something went wrong", "traceback", "undefined is not", "[object object]")


def log_size() -> int:
    try:
        return LOG.stat().st_size
    except OSError:
        return 0


def new_log_errors(since: int) -> list[str]:
    try:
        with LOG.open("rb") as f:
            f.seek(since)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [line[:200] for line in text.splitlines() if " ERROR " in line or line.startswith("Traceback")]


def launch(pw):
    """The browser Playwright installed, or the PC's Chrome when Playwright's own isn't downloaded."""
    try:
        return pw.chromium.launch()
    except Exception:
        return pw.chromium.launch(channel="chrome")


def crawl(base: str, check_links: bool) -> dict:
    from playwright.sync_api import sync_playwright
    findings: list[dict] = []
    pages_seen, links, timings = 0, set(), []
    start_log = log_size()
    with sync_playwright() as pw:
        browser = launch(pw)
        for size_name, (w, h, scheme) in SIZES.items():
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
            ctx.add_init_script("try { localStorage.setItem('airtac-boot', 'off') } catch (e) {}")
            page = ctx.new_page()
            where = {"route": ""}

            def add(kind, detail):
                findings.append({"size": size_name, "route": where["route"], "kind": kind, "detail": str(detail)[:220]})

            page.on("pageerror", lambda e: add("JavaScript error", f"{e} @ {(getattr(e, 'stack', '') or '').splitlines()[1:2]}"))
            page.on("console", lambda m: add("console error", m.text) if m.type == "error"
                    and "status of 423" not in m.text else None)

            def on_response(r):
                if r.status >= 400 and r.status not in EXPECTED_STATUS and base in r.url and "/favicon/" not in r.url:
                    add(f"HTTP {r.status}", r.url.replace(base, "/"))
            page.on("response", on_response)
            page.on("requestfailed", lambda r: add("request failed", f"{r.url.replace(base, '/')} {r.failure}")
                    if base in r.url else None)

            page.goto(base + "#home", wait_until="networkidle")
            if "login" in page.url:
                add("setup", "AIRTAC asked for the passcode: run this on the PC itself (localhost).")
                break
            views = page.evaluate("VIEWS.filter(v => v !== 'app')")
            apps = page.evaluate("APPS.map(a => 'app-' + a.id)")
            for route in views + apps:
                where["route"] = route
                pages_seen += 1
                t0 = time.monotonic()
                page.goto(f"{base}#{route}", wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    add("slow", "still loading after 15 s")
                page.wait_for_timeout(400)
                took = time.monotonic() - t0
                timings.append((took, size_name, route))
                if took > SLOW_S:
                    add("slow", f"{took:.1f} s to load")
                state = page.evaluate("""() => {
                    const v = [...document.querySelectorAll('section.view')].find(s => !s.hidden) || document.body;
                    const text = (v.innerText || '').trim();
                    const imgs = [...v.querySelectorAll('img')].filter(i => i.complete && i.naturalWidth === 0 && i.offsetParent)
                        .map(i => i.getAttribute('src') || '').slice(0, 5);
                    const links = [...v.querySelectorAll('a[href^="http"]')].map(a => a.href).slice(0, 80);
                    const tabs = [...v.querySelectorAll('[role="tab"]')].filter(t => t.offsetParent).map(t => t.id).filter(Boolean);
                    return {text: text.slice(0, 4000), len: text.length, imgs, links, tabs,
                            wide: document.documentElement.scrollWidth - document.documentElement.clientWidth};
                }""")
                low = state["text"].lower()
                for phrase in ERROR_TEXT:
                    if phrase in low:
                        i = low.index(phrase)
                        add("error text", state["text"][max(0, i - 60): i + 100].replace("\n", " "))
                if state["len"] < 15:
                    add("blank", "the page shows almost nothing")
                if state["wide"] > 1:
                    add("sideways scroll", f"{state['wide']} px wider than the screen")
                for src in state["imgs"]:
                    add("missing image", src)
                links.update(state["links"])
                for tab in state["tabs"]:
                    if not page.is_visible(f"#{tab}"):
                        continue            # an earlier tab switched to another mode that hides this one
                    try:
                        page.click(f"#{tab}", timeout=3000)
                        page.wait_for_timeout(500)
                    except Exception as e:
                        add("tab", f"{tab}: {type(e).__name__}")
            ctx.close()
        browser.close()
    dead = check_external(links) if check_links else []
    for url, why in dead:
        findings.append({"size": "-", "route": "-", "kind": "dead link", "detail": f"{url} ({why})"})
    for line in new_log_errors(start_log):
        findings.append({"size": "-", "route": "server.log", "kind": "backend error", "detail": line})
    timings.sort(reverse=True)
    return {"findings": findings, "pages": pages_seen, "links": len(links), "slowest": timings[:5]}


def check_external(urls: set[str]) -> list[tuple[str, str]]:
    import httpx
    dead = []
    with httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": "AIRTAC link check"}) as c:
        for url in sorted(urls):
            try:
                r = c.head(url)
                if r.status_code in (403, 405, 429):        # many sites refuse HEAD or bots; try a GET
                    r = c.get(url)
                if r.status_code >= 400 and r.status_code not in (401, 403, 429):
                    dead.append((url, r.status_code))
            except httpx.HTTPError as e:
                dead.append((url, type(e).__name__))
    return dead


def report(result: dict, base: str) -> Path:
    out = ROOT / "data" / "logs" / "qa_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    f = result["findings"]
    lines = [f"# QA crawl: {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"{base} · {result['pages']} pages x sizes · {result['links']} external links seen · **{len(f)} findings**", "",
             "Slowest: " + ", ".join(f"{r} ({s}) {t:.1f} s" for t, s, r in result["slowest"]), ""]
    if f:
        lines += ["| Size | Page | Kind | Detail |", "|---|---|---|---|"]
        lines += [f"| {x['size']} | {x['route']} | {x['kind']} | {x['detail'].replace('|', '/')} |" for x in f]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out.with_suffix(".json")).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://localhost:8765/")
    ap.add_argument("--links", action="store_true")
    args = ap.parse_args(argv)
    base = args.base if args.base.endswith("/") else args.base + "/"
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright isn't installed: .venv\\Scripts\\pip install playwright, then .venv\\Scripts\\playwright install chromium")
        return 2
    result = crawl(base, args.links)
    out = report(result, base)
    for x in result["findings"]:
        print(f"FAIL [{x['size']}] {x['route']}: {x['kind']}: {x['detail']}")
    print(f"\n{result['pages']} pages checked, {len(result['findings'])} findings. Report: {out}")
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
