"""Which apps may open as tabs inside AIRTAC? Checks every app in plugins/desktop/apps.json.

    .venv\\Scripts\\python.exe scripts\\probe_embed.py              (apps not checked in the last 7 days)
    .venv\\Scripts\\python.exe scripts\\probe_embed.py --force      (check them all again)
    .venv\\Scripts\\python.exe scripts\\probe_embed.py gmail canvas (only these)
    add --no-render to skip the render check

For each site it reads the X-Frame-Options and Content-Security-Policy (frame-ancestors) headers the site
sends to a browser. If Playwright is installed it also loads the site in a frame in a hidden browser. The
answer is kept for 7 days, the same answer the Re-probe tabs button gives. AIRTAC never changes, strips
or proxies a site's headers: a site that says no opens in your browser.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AIRTAC_NO_SCHEDULER", "1")


def main(argv: list[str]) -> int:
    from airtac import plugins
    plugins.load_all()
    from plugins import desktop
    from plugins.desktop import embed

    force = "--force" in argv
    render = "--no-render" not in argv
    ids = [a for a in argv if not a.startswith("--")] or None
    print("Checking... (about 10 seconds)" if render else "Checking headers...")
    r = desktop.run_probe(ids, force=force, render=render)
    results = embed.cache()
    rows = [a for a in desktop.all_apps() if a.get("url") and (ids is None or a["id"] in ids)]
    width = max(len(a["name"]) for a in rows) if rows else 10
    for a in sorted(rows, key=lambda a: (a["open_as"] != "tab", a["name"].lower())):
        where = "TAB    " if a["open_as"] == "tab" else "browser"
        print(f"{a['name']:<{width}}  {where}  {a['why']}")
    print(f"\nChecked {r['checked']} now; {sum(1 for a in rows if a['open_as'] == 'tab')} of {len(rows)} can open as tabs. "
          f"Answers are kept for 7 days ({len(results)} saved).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
