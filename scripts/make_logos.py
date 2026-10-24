"""Make the app logos in web/logos/ from Simple Icons (CC0, https://simpleicons.org).

AIRTAC never fetches a logo while it runs: the SVG files are saved in web/logos/ once, by this script.
Each logo becomes a small app-style tile: the brand colour as the background and the mark in white
(or black, when the brand colour is light), so it reads on every theme.

Run it only when you add an app whose logo isn't there yet:
    npm pack simple-icons            (or download the package from https://github.com/simple-icons/simple-icons)
    tar -xzf simple-icons-*.tgz
    .venv\\Scripts\\python scripts\\make_logos.py package

It reads the "logo" names in plugins/desktop/apps.json and the providers' logos, and writes only those.
Brand logos remain trademarks of their owners; see web/logos/CREDITS.md.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "logos"
EXTRA = ["anthropic", "nvidia", "openrouter", "googlegemini", "ollama", "deepseek", "mistralai", "huggingface",
         "perplexity", "github", "leetcode", "codechef", "geeksforgeeks",
         "brave", "sap", "cloudflare", "googlecloud", "googlecloudstorage", "dropbox", "supabase", "coderabbit",
         "gmail", "googlecalendar", "googledrive", "notion"]   # brains, accounts and API keys, not in apps.json


def wanted() -> set[str]:
    catalog = json.loads((ROOT / "plugins" / "desktop" / "apps.json").read_text(encoding="utf-8"))
    return {a["logo"] for a in catalog["apps"] if a.get("logo")} | set(EXTRA)


def light(hex_colour: str) -> bool:
    """True for light brand colours, which need a black mark (relative luminance, as in WCAG)."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] > 0.45


def tile(title: str, hex_colour: str, path: str) -> str:
    ink = "#000000" if light(hex_colour) else "#ffffff"
    title = re.sub(r"[<>&\"]", "", title)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-5 -5 34 34" role="img"><title>{title}</title>'
            f'<rect x="-5" y="-5" width="34" height="34" rx="8" fill="#{hex_colour}"/>'
            f'<path fill="{ink}" d="{path}"/></svg>\n')


def main(package: Path) -> None:
    data = json.loads((package / "data" / "simple-icons.json").read_text(encoding="utf-8"))
    icons = data if isinstance(data, list) else data.get("icons", [])
    by_slug = {}
    for icon in icons:
        slug = icon.get("slug") or re.sub(r"[^a-z0-9]", "", icon["title"].lower().replace("+", "plus").replace(".", "dot"))
        by_slug[slug] = icon
    OUT.mkdir(parents=True, exist_ok=True)
    made, missing = [], []
    for slug in sorted(wanted()):
        svg_file = package / "icons" / f"{slug}.svg"
        if slug not in by_slug or not svg_file.exists():
            missing.append(slug)
            continue
        path = re.search(r'<path d="([^"]+)"', svg_file.read_text(encoding="utf-8")).group(1)
        (OUT / f"{slug}.svg").write_text(tile(by_slug[slug]["title"], by_slug[slug]["hex"], path), encoding="utf-8")
        made.append((slug, by_slug[slug]["title"], by_slug[slug].get("license", {}).get("type", "CC0-1.0")))
    print(f"Wrote {len(made)} logos to {OUT}")
    if missing:
        print("Not in Simple Icons (they show a letter badge):", ", ".join(missing))
    for slug, title, lic in made:
        if lic != "CC0-1.0":
            print(f"Note: {title} ({slug}) is under {lic}, not CC0. Credit it in web/logos/CREDITS.md.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/make_logos.py <folder of the unpacked simple-icons package>")
    main(Path(sys.argv[1]))
