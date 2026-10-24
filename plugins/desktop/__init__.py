"""Desktop: open apps and websites on this computer by voice. Only the ones you list in
config/airtac.toml under [plugins.desktop], and only after you press Confirm, because a web page
or a document could otherwise trick the model into opening things.

Websites open in Google Chrome with AIRTAC's own profile (see chrome.py), so you stay signed in.
The launch tiles on Home (YouTube, Instagram, Google, ...) open the same way when you tap them on
this PC; a tap is your OK, so they don't ask again. From your phone they open on the phone."""
import contextvars
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from airtac import config, memory, plugin_api, websearch
from airtac.plugin_api import Tool, ToolResult, after, local_only, plugin, schema
from plugins.desktop import chrome, embed

log = logging.getLogger("airtac.desktop")

p = plugin(__name__)


def _apps() -> dict:
    return {k.lower(): v for k, v in (p.setting("apps", {}) or {}).items()}


def _sites() -> dict:
    return {k.lower(): v for k, v in (p.setting("websites", {}) or {}).items()}


def _match(name: str, table: dict) -> str | None:
    name = (name or "").lower().strip(" .!?")
    if name in table:
        return name
    return next((k for k in table if k in name or name in k), None) if name else None


def launch(command: str) -> None:
    """Start an app the way the Start menu would (Windows), or with the desktop's opener elsewhere."""
    if sys.platform == "win32":
        os.startfile(command)  # noqa: S606 (only commands from your own allowlist get here)
    else:
        subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", command])


def open_app(name: str) -> ToolResult:
    key = _match(name, _apps())
    if key is None:
        return ToolResult(f"{name or 'That app'} isn't on your list of apps I may open. Add it under "
                          "[plugins.desktop] apps in config/airtac.toml.", ok=False)
    try:
        launch(_apps()[key])
    except OSError:
        return ToolResult(f"I couldn't open {key}. Check its command in config/airtac.toml.", ok=False)
    return ToolResult(f"Opened {key}.")


def open_website(name: str) -> ToolResult:
    key = _match(name, _sites())
    url = _sites()[key] if key else None
    if url is None:
        return ToolResult(f"{name or 'That site'} isn't on your list of websites. Add it under [plugins.desktop] "
                          "websites in config/airtac.toml, or use the Web page to search.", ok=False)
    try:
        how = open_url(url)
    except (OSError, ValueError):
        return ToolResult(f"I couldn't open {key}. Check its address in config/airtac.toml.", ok=False)
    return ToolResult(f"Opened {key} in {'Chrome, with your saved logins' if how == 'chrome' else 'your browser'}.")


def open_url(url: str, browser: str = "chrome") -> str:
    """Open an address. browser "chrome": AIRTAC's own Chrome profile (falls back to the default browser
    when Chrome isn't installed); "default": your usual browser."""
    use_chrome = browser == "chrome" and bool(p.setting("use_chrome_profile", True))
    return chrome.open_url(url, p.setting("chrome", ""), use_chrome)


# ---------------------------------------------------------------- the apps catalog: AI apps, Apps and Social

# One file lists every app: plugins/desktop/apps.json (name, address, section, group, about, logo, tabs).
# You can edit it by hand or from the app (Add, Edit and Remove on the AI apps, Apps and Social pages).
# Logos: a file in web/logos/ (scripts/make_logos.py), else the site's own favicon (fetched once, kept
# in data/favicons/), else a two-letter badge.
CATALOG_FILE = Path(__file__).with_name("apps.json")
LOGO_DIR = Path(__file__).resolve().parents[2] / "web" / "logos"
SECTIONS = ("ai", "apps", "social")
TAB_MODES = ("auto", "embed", "external")
_cat = {"mtime": None, "data": None, "moved": False}
_lock = threading.Lock()


def catalog_file() -> Path:
    return getattr(config, "APPS_FILE", None) or CATALOG_FILE    # the tests point APPS_FILE at a copy


def catalog() -> dict:
    """apps.json, read again whenever the file changes."""
    path = catalog_file()
    m = (str(path), path.stat().st_mtime_ns)
    if _cat["mtime"] != m:
        _cat["data"], _cat["mtime"] = json.loads(path.read_text(encoding="utf-8")), m
    return _cat["data"]


def save_catalog(cat: dict) -> None:
    """Write apps.json safely: a copy of the old file goes to data/backups/ first (the newest 20 are kept),
    and the new file replaces it in one step, so a crash can't leave half a file."""
    with _lock:
        backups = config.DB_PATH.parent / "backups" / "apps"
        backups.mkdir(parents=True, exist_ok=True)
        path = catalog_file()
        shutil.copy2(path, backups / f"apps-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000:06d}.json")
        for old in sorted(backups.glob("apps-*.json"))[:-20]:
            old.unlink()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cat, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        _cat["mtime"] = None


def _slug(name: str, taken: set) -> str:
    base = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:30] or "app"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    return out


def migrate_my_apps() -> int:
    """Apps you added in older versions were kept in AIRTAC's database; move them into apps.json once.
    Your data folder is backed up first, and the database copy is left as it was."""
    if _cat["moved"]:
        return 0
    _cat["moved"] = True
    if memory.kv_get("desktop", "ai_apps_moved"):
        return 0
    try:
        mine = json.loads(memory.kv_get("desktop", "ai_apps") or "[]")
    except ValueError:
        mine = []
    cat = json.loads(json.dumps(catalog()))
    have = {a.get("url") for a in cat["apps"]}
    todo = [a for a in mine if safe_url(str(a.get("url", ""))) and a["url"] not in have]
    if todo:
        try:
            from plugins.backup import make_backup
            make_backup()
        except Exception as e:  # no backup, no move: try again next start
            log.warning("couldn't back up before moving your apps: %s", e)
            _cat["moved"] = False
            return 0
        taken = {a["id"] for a in cat["apps"]}
        for a in todo:
            name = str(a["name"])[:40]
            new_id = _slug(name, taken)
            taken.add(new_id)
            cat["apps"].append({"id": new_id, "name": name, "url": a["url"], "section": "ai", "group": "mine",
                                "about": "Added by you", "mark": name[:2], "logo": "", "tabs": "auto", "mine": True})
        save_catalog(cat)
    memory.kv_set("desktop", "ai_apps_moved", "1")
    return len(todo)


def embed_state(a: dict, results: dict | None = None) -> dict:
    """How an app opens: "tab" (inside AIRTAC), "browser" or "app" (an AIRTAC page), and why."""
    if a.get("app"):
        return {"open_as": "app", "why": "One of AIRTAC's own pages."}
    mode = a.get("tabs", "auto")
    entry = (results if results is not None else embed.cache()).get(a["id"])
    known = embed.fresh(entry, a.get("url", ""))
    if mode == "external":
        return {"open_as": "browser", "why": "Set to always open in your browser."}
    if not known:
        return {"open_as": "browser", "why": "Not checked yet.", "probe": "due"}
    if not entry["ok"]:
        return {"open_as": "browser", "why": entry["why"], "checked": entry["at"]}
    return {"open_as": "tab", "why": entry["why"], "checked": entry["at"]}


def _no_favicon(app_id: str) -> bool:
    """True when asking for the site's icon would only fail: offline, or a miss remembered in the last 7 days.
    The page then shows the letter badge straight away instead of a failed request."""
    if config.OFFLINE:
        return True
    key = "".join(c for c in app_id if c.isalnum() or c == "-")[:60]
    miss = config.DB_PATH.parent / "favicons" / f"{key}.none"
    try:
        return time.time() - miss.stat().st_mtime < embed.TTL
    except OSError:
        return False


def _with_logo(a: dict, results: dict | None = None) -> dict:
    logo = a.get("logo") or ""
    has = bool(logo) and (LOGO_DIR / f"{logo}.svg").is_file()
    fav = f"api/plugins/desktop/favicon/{a['id']}" if not has and a.get("url") and not _no_favicon(a["id"]) else ""
    return {**a, "tabs": a.get("tabs", "auto"), "logo": f"logos/{logo}.svg" if has else "", "favicon": fav,
            "logo_missing": not has, **embed_state(a, results)}


def safe_url(url: str) -> bool:
    """An address AIRTAC may open: https, no user name or password inside it, no spaces or quotes."""
    from urllib.parse import urlsplit
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 300 or any(c in url for c in ' <>"\n\\'):
        return False
    parts = urlsplit(url)
    return bool(parts.hostname) and parts.username is None and parts.password is None and "@" not in parts.netloc


def my_ai_apps() -> list[dict]:
    """AI apps listed in config/airtac.toml under [plugins.desktop] ai_tiles (apps you add in the app go
    into apps.json)."""
    mine = [{"name": k, "url": v} for k, v in (p.setting("ai_tiles", {}) or {}).items()]
    return [{"id": "mine-" + "".join(c for c in str(a["name"]).lower() if c.isalnum())[:30], "name": str(a["name"])[:40],
             "url": a["url"], "mark": str(a["name"][:2]), "group": "mine", "section": "ai", "tabs": "external",
             "about": "From config/airtac.toml", "mine": True, "logo": "", "favicon": "", "logo_missing": True,
             "open_as": "browser", "why": "From config/airtac.toml.", "fixed": True}
            for a in mine if safe_url(str(a.get("url", "")))]


def section_apps(section: str) -> list[dict]:
    results = embed.cache()
    return [_with_logo(a, results) for a in catalog()["apps"] if a["section"] == section]


def ai_apps() -> list[dict]:
    return section_apps("ai") + my_ai_apps()


def social_apps() -> list[dict]:
    return section_apps("social")


def web_apps() -> list[dict]:
    return section_apps("apps")


def ai_tiles() -> list[dict]:
    """The pinned ones, for Home."""
    by_name = {a["name"]: a for a in ai_apps()}
    return [by_name[n] for n in catalog()["pinned"] if n in by_name] + [a for a in ai_apps() if a.get("mine")]


def all_apps() -> list[dict]:
    return ai_apps() + web_apps() + social_apps()


def find_app(app_id: str) -> dict | None:
    return next((a for a in all_apps() if a["id"] == app_id), None)


def frame_origins() -> set[str]:
    """Addresses the page may show in a tab: only apps whose check passed (and where they redirect to)."""
    results = embed.cache()
    out = set()
    for a in catalog()["apps"]:
        if a.get("url") and embed_state(a, results)["open_as"] == "tab":
            u = urlsplit(a["url"])
            out.add(f"https://{u.netloc}")
            if results[a["id"]].get("final", "").startswith("https://"):
                out.add(results[a["id"]]["final"])
    return out


plugin_api.frame_source(p.id, frame_origins)   # the page's CSP lets only these load in a tab


def run_probe(ids: list[str] | None = None, force: bool = False, render: bool = True) -> dict:
    """Check apps (all, or these ids) that may open as tabs; keep each answer for 7 days."""
    from concurrent.futures import ThreadPoolExecutor
    results = embed.cache()
    todo = [a for a in catalog()["apps"] if a.get("url") and a.get("tabs", "auto") != "external"
            and (ids is None or a["id"] in ids) and (force or not embed.fresh(results.get(a["id"]), a["url"]))]
    if config.OFFLINE or not todo:
        return {"checked": 0}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for a, r in zip(todo, pool.map(lambda a: contextvars.copy_context().run(embed.probe, a["url"], render), todo)):
            results[a["id"]] = {**r, "url": a["url"]}
    embed.save(results)
    plugin_api.frame_sources(refresh=True)
    return {"checked": len(todo), "tabs": sum(1 for a in todo if results[a["id"]]["ok"])}


def open_target(app_id: str, page: str = "home") -> str:
    """The address to open for an app: its home page, or (page="login") its sign-in page.
    Only the catalog's own address is used, and it never carries a user name, password or cookie:
    a saved login is copied to your clipboard on this PC, never sent in the address."""
    app = find_app(app_id)
    if app is None:
        raise ValueError("That app isn't in AIRTAC's list.")
    if app.get("app"):
        raise ValueError("That's one of AIRTAC's own pages.")
    url = app.get("login") if page == "login" and app.get("login") else app["url"]
    if not safe_url(url):
        raise ValueError("That app's address isn't a plain https address.")
    return url


def browser_for(app: dict) -> str:
    """AI apps open in Chrome; Apps and Social in the browser chosen on the Settings page (default: your usual one)."""
    if app.get("section") in ("social", "apps"):
        try:
            from plugins.settings import get as setting
            return setting("social_browser")
        except Exception:
            return "default"
    return "chrome"


def check_app(body: dict, cat: dict, editing: dict | None) -> dict:
    """Check an app from the Add or Edit form. Raises ValueError with a sentence for the page."""
    name = str(body.get("name") or "").strip()[:40]
    if not name:
        raise ValueError("Give the app a name.")
    section = body.get("section") or (editing or {}).get("section") or "ai"
    if section not in SECTIONS:
        raise ValueError("Pick AI apps, Apps or Social.")
    group = body.get("group") or "mine"
    if group != "mine" and group not in cat["groups"]:
        raise ValueError("Pick one of the groups on the page.")
    tabs = body.get("tabs") or "auto"
    if tabs not in TAB_MODES:
        raise ValueError("Opens as: auto, tab or browser.")
    out = {**(editing or {}), "name": name, "section": section, "group": group, "tabs": tabs,
           "about": str(body.get("about") or (editing or {}).get("about") or "Added by you").strip()[:140]}
    if not (editing or {}).get("app"):                     # AIRTAC's own pages have no address
        url = str(body.get("url") or "").strip()
        if not safe_url(url):
            raise ValueError("Give a plain address that starts with https:// (no user name or password in it).")
        if editing and url != editing.get("url"):
            out.pop("login", None)                         # a sign-in page of the old address no longer fits
        out["url"] = url
    out.setdefault("mark", name[:2])
    out.setdefault("logo", "")
    return out


# Tiles on Home that open a site straight away (your tap is the OK): the social sites, unless you list
# your own under [plugins.desktop] tiles in config/airtac.toml.
def tiles() -> list[dict]:
    chosen = p.setting("tiles", None)
    if not chosen:
        return social_apps()
    by_url = {a["url"]: a for a in all_apps() if a.get("url")}      # reuse a catalog logo when the URL matches
    out = []
    for k, v in chosen.items():
        if not safe_url(str(v)):
            continue
        known = by_url.get(v)
        out.append({"name": k, "url": v, "logo": known["logo"] if known else "", "logo_missing": not (known and known["logo"])})
    return out


p.add(Tool("open_app", "Open an app on this computer from the user's allowlist (asks the user to confirm first).",
           schema({"name": {"type": "string", "description": "The app, e.g. notepad, vs code, calculator"}},
                  required=("name",)),
           "device", open_app, summarize=lambda a: f"open {a.get('name', 'that app')}"))
p.add(Tool("open_website", "Open one of the user's saved websites in the browser (asks the user to confirm first).",
           schema({"name": {"type": "string", "description": "The site's name, e.g. github, youtube"}}, required=("name",)),
           "device", open_website, summarize=lambda a: f"open {a.get('name', 'that site')} in your browser"))


@p.routes
def api(router) -> None:
    from fastapi import Request
    from pydantic import BaseModel, Field

    class Launch(BaseModel):
        url: str = Field(max_length=500)

    @router.get("/tiles")
    def list_tiles(request: Request):
        from airtac import auth
        migrate_my_apps()
        cat = catalog()
        groups = {**cat["groups"], "mine": "Added by you"}
        return {"tiles": tiles(), "ai": ai_tiles(), "ai_all": ai_apps(), "ai_groups": groups, "groups": groups,
                "sections": cat["sections"], "apps": web_apps(), "social": social_apps(), "pinned": cat["pinned"],
                "here": auth.is_local_request(request), "offline": config.OFFLINE,
                "chrome": bool(chrome.find_chrome(p.setting("chrome", ""))), "profile": str(chrome.profile_dir())}

    class AppIn(BaseModel):
        id: str = Field(default="", max_length=60)
        name: str = Field(max_length=80)
        url: str = Field(default="", max_length=500)
        section: str = Field(default="", max_length=10)
        group: str = Field(default="", max_length=20)
        about: str = Field(default="", max_length=300)
        tabs: str = Field(default="auto", max_length=10)

    @router.post("/catalog")
    def save_app(body: AppIn, request: Request):
        """Add an app (no id) or change one (its id). This PC only; written to apps.json."""
        from fastapi import HTTPException
        local_only(request)
        cat = json.loads(json.dumps(catalog()))
        at = next((i for i, a in enumerate(cat["apps"]) if a["id"] == body.id), None) if body.id else None
        if body.id and at is None:
            raise HTTPException(404, "That app isn't in the list any more.")
        try:
            app = check_app(body.model_dump(), cat, cat["apps"][at] if at is not None else None)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if at is None:
            if any(a.get("url") == app["url"] and a["section"] == app["section"] for a in cat["apps"]):
                raise HTTPException(400, "That address is already in this list.")
            app = {"id": _slug(app["name"], {a["id"] for a in cat["apps"]}), **app, "mine": True}
            cat["apps"].append(app)
        else:
            cat["apps"][at] = app
        save_catalog(cat)
        return {"saved": app["id"], "ai_all": ai_apps()}

    class AppId(BaseModel):
        id: str = Field(max_length=60)

    @router.post("/catalog/remove")
    def remove_app(body: AppId, request: Request):
        """Take an app off the list (a copy of the old list stays in data/backups/apps/)."""
        from fastapi import HTTPException
        local_only(request)
        cat = json.loads(json.dumps(catalog()))
        left = [a for a in cat["apps"] if a["id"] != body.id]
        if len(left) == len(cat["apps"]):
            raise HTTPException(404, "That app isn't in the list any more.")
        cat["apps"] = left
        save_catalog(cat)
        return {"removed": body.id, "ai_all": ai_apps()}

    class AiApp(BaseModel):          # the side panel's quick "Add an app" (name and address only)
        name: str = Field(max_length=80)
        url: str = Field(default="", max_length=500)

    @router.post("/ai_apps")
    def add_ai_app(body: AiApp, request: Request):
        return save_app(AppIn(name=body.name, url=body.url.strip(), section="ai", group="mine"), request)

    @router.post("/ai_apps/remove")
    def remove_ai_app(body: AiApp, request: Request):
        local_only(request)
        found = next((a for a in catalog()["apps"] if a["name"] == body.name and a.get("mine")), None)
        return remove_app(AppId(id=found["id"]), request) if found else {"ai_all": ai_apps()}

    class Probe(BaseModel):
        ids: list[str] | None = Field(default=None, max_length=200)
        force: bool = False

    @router.post("/probe")
    def probe(body: Probe, request: Request):
        """Check which apps may open as tabs (Re-probe: force). This PC only: it calls the sites."""
        local_only(request)
        # The automatic check (a page opened) reads headers only; Re-probe also does the slower render check.
        return {**run_probe(body.ids, body.force, render=body.force), "apps": all_apps()}

    @router.get("/favicon/{app_id}")
    def favicon(app_id: str):
        """An app's own favicon, fetched once from its site and kept in data/favicons/ (a miss is
        remembered for 7 days). Only ico, png, gif, jpeg or webp; SVG is refused (it can carry script)."""
        from fastapi import HTTPException, Response
        app = find_app(app_id)
        if app is None or not app.get("url") or config.OFFLINE:
            raise HTTPException(404, "No icon.")
        folder = config.DB_PATH.parent / "favicons"
        folder.mkdir(parents=True, exist_ok=True)
        key = "".join(c for c in app_id if c.isalnum() or c == "-")[:60]
        hit = next(iter(folder.glob(f"{key}.*")), None)
        if hit and hit.suffix == ".none" and time.time() - hit.stat().st_mtime < embed.TTL:
            raise HTTPException(404, "No icon.")
        if not hit or hit.suffix == ".none":
            try:
                data, kind = websearch.fetch_icon(app["url"])
            except Exception:
                data, kind = b"", ""
            if hit:
                hit.unlink()
            hit = folder / (f"{key}.{kind.split('/')[1].replace('x-icon', 'ico')}" if kind else f"{key}.none")
            hit.write_bytes(data)
            if not kind:
                raise HTTPException(404, "No icon.")
        kind = {"ico": "image/x-icon", "png": "image/png", "gif": "image/gif", "jpeg": "image/jpeg", "webp": "image/webp"}[hit.suffix[1:]]
        return Response(hit.read_bytes(), media_type=kind, headers={"Cache-Control": "max-age=86400",
                                                                     "Content-Security-Policy": "default-src 'none'"})

    @router.post("/launch")
    def launch_url(body: Launch, request: Request):
        """Open a site in AIRTAC's Chrome profile. Only from this PC, and only addresses AIRTAC itself
        offers (a tile, a saved website, or an account's sign-in page), never any address a page sends."""
        from fastapi import HTTPException
        local_only(request)
        if body.url not in allowed_urls():
            raise HTTPException(400, "That address isn't one of AIRTAC's tiles or saved sites.")
        return {"opened": open_url(body.url)}

    class OpenApp(BaseModel):
        id: str = Field(max_length=60)
        page: str = Field(default="home", pattern="^(home|login)$")

    @router.post("/open")
    def open_app_by_id(body: OpenApp, request: Request):
        """Open an app from the catalog by its id, on this PC: AI apps in Chrome, social sites in the
        browser chosen on the Settings page. Only the catalog's own address is opened."""
        from fastapi import HTTPException
        local_only(request)
        try:
            url = open_target(body.id, body.page)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"opened": open_url(url, browser_for(find_app(body.id))), "url": url}


def allowed_urls() -> set[str]:
    apps = all_apps()
    urls = {t["url"] for t in tiles()} | {a["url"] for a in apps if a.get("url")} | {a["login"] for a in apps if a.get("login")}
    urls |= set(_sites().values())
    try:  # the Accounts page's sign-up, log-in and profile links
        from plugins.accounts import SITES, links
        for sid in SITES:
            urls |= {u for u in links(sid).values() if u}
    except Exception:
        pass
    return urls


@p.dashboard
def page(pg: dict) -> None:
    found = chrome.find_chrome(p.setting("chrome", ""))
    pg["stats"] = [{"label": "Apps allowed", "value": len(_apps())}, {"label": "Websites", "value": len(_sites())},
                   {"label": "Chrome profile", "value": "ready" if found else "Chrome not found"}]
    pg["lists"] = [{"title": "Apps", "items": [{"title": k, "meta": v} for k, v in _apps().items()],
                    "empty": "None yet. Add them in config/airtac.toml."},
                   {"title": "Websites", "items": [{"title": k, "meta": v, "url": v} for k, v in _sites().items()],
                    "empty": "None yet."}]


def _what(text: str) -> str:
    return after(text, r"\b(open|launch|start)\b(\s+(the|my))?") or text


def _open_args(text, _messages):
    return {"name": _what(text)}


p.mock_rule(26.5, ("open website", "open site", "go to", "open github", "open youtube", "open leetcode"),
            "open_website", _open_args)
p.mock_rule(26.6, ("open", "launch", "start the app"), "open_app", _open_args)
