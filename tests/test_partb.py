"""Part B: the editable app catalog, the embed check and tabs, web search engines, and Search everything."""
import json
import shutil
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from airtac import config, memory, plugin_api, websearch
from airtac.main import app
from plugins import desktop, finder
from plugins import settings as st
from plugins.desktop import embed

ROOT = Path(__file__).resolve().parent.parent
here = lambda: TestClient(app, base_url="http://localhost")   # noqa: E731  (this PC)
away = lambda: TestClient(app)                                # noqa: E731  (another device)


@pytest.fixture
def cat_copy():
    """conftest gives every test its own copy of apps.json; this names it."""
    return config.APPS_FILE


# ---------------------------------------------------------------- catalog

def test_catalog_has_the_three_pages_and_a_tab_rule_for_every_app():
    cat = desktop.catalog()
    assert set(cat["sections"]) == {"ai", "apps", "social"}
    for a in cat["apps"]:
        assert a["tabs"] in desktop.TAB_MODES and a["section"] in desktop.SECTIONS and a["group"] in cat["groups"], a["id"]
        assert a.get("url") or a.get("app"), a["id"]
        if a.get("app"):     # Other apps are AIRTAC's own pages
            assert (ROOT / "plugins" / a["app"]).is_dir(), a["app"]
    ids = [a["id"] for a in cat["apps"]]
    assert len(ids) == len(set(ids))
    assert {a["group"] for a in desktop.social_apps()} <= {"messaging", "networks", "video", "work"}


def test_add_edit_remove_writes_apps_json_with_a_backup(cat_copy):
    c = here()
    r = c.post("/api/plugins/desktop/catalog", json={"name": "Wolfram Alpha", "url": "https://www.wolframalpha.com",
                                                     "section": "ai", "group": "research", "tabs": "auto"})
    assert r.status_code == 200, r.text
    new_id = r.json()["saved"]
    saved = json.loads(cat_copy.read_text(encoding="utf-8"))
    assert any(a["id"] == new_id and a["mine"] for a in saved["apps"])
    assert list((config.DB_PATH.parent / "backups" / "apps").glob("apps-*.json"))
    r = c.post("/api/plugins/desktop/catalog", json={"id": new_id, "name": "Wolfram", "url": "https://www.wolframalpha.com/",
                                                     "section": "ai", "group": "research", "tabs": "external"})
    assert r.status_code == 200 and desktop.find_app(new_id)["tabs"] == "external" and desktop.find_app(new_id)["name"] == "Wolfram"
    assert c.post("/api/plugins/desktop/catalog/remove", json={"id": new_id}).status_code == 200
    assert desktop.find_app(new_id) is None


@pytest.mark.parametrize("bad", [{"url": "http://x.com"}, {"url": "https://a:b@x.com"}, {"name": ""},
                                 {"section": "games"}, {"group": "nope"}, {"tabs": "always"}])
def test_bad_app_details_are_refused(cat_copy, bad):
    body = {"name": "Thing", "url": "https://thing.example", "section": "apps", "group": "web", "tabs": "auto", **bad}
    assert here().post("/api/plugins/desktop/catalog", json=body).status_code == 400


def test_catalog_edits_are_for_this_pc_only(cat_copy):
    assert away().post("/api/plugins/desktop/catalog", json={"name": "X", "url": "https://x.example"}).status_code in (401, 403)
    assert away().post("/api/plugins/desktop/catalog/remove", json={"id": "claude"}).status_code in (401, 403)
    assert away().post("/api/plugins/desktop/probe", json={}).status_code in (401, 403)


def test_old_apps_from_the_database_move_into_apps_json_once(cat_copy, monkeypatch):
    desktop._cat["moved"] = False
    backed = []
    import plugins.backup as backup
    monkeypatch.setattr(backup, "make_backup", lambda: backed.append(1))
    memory.kv_set("desktop", "ai_apps", json.dumps([{"name": "My Tool", "url": "https://tool.example"},
                                                     {"name": "Bad", "url": "http://insecure.example"}]))
    assert desktop.migrate_my_apps() == 1 and backed == [1]
    assert any(a["name"] == "My Tool" and a["group"] == "mine" for a in desktop.ai_apps())
    assert memory.kv_get("desktop", "ai_apps")          # the database copy is kept
    desktop._cat["moved"] = False
    assert desktop.migrate_my_apps() == 0               # only once


# ---------------------------------------------------------------- embed check and tabs

@pytest.mark.parametrize("headers,ok", [
    ({"x-frame-options": "DENY"}, False),
    ({"x-frame-options": "sameorigin"}, False),
    ({"content-security-policy": "default-src 'self'; frame-ancestors 'none'"}, False),
    ({"content-security-policy": "frame-ancestors 'self' https://partner.example"}, False),
    ({"content-security-policy": "frame-ancestors *", "x-frame-options": "DENY"}, True),   # CSP wins over XFO
    ({"content-security-policy": "default-src 'self'"}, True),
    ({}, True),
])
def test_embed_verdict_follows_the_sites_own_headers(headers, ok):
    assert embed.verdict(headers)[0] is ok


def test_probe_reads_headers_after_redirects_and_keeps_it_7_days(monkeypatch, cat_copy):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(embed, "render_check", lambda url: (None, "render check skipped (Playwright isn't installed)"))

    def head(url):
        if "wikipedia" in url or "huggingface" in url:
            return url, 200, httpx.Headers({})
        return url, 200, httpx.Headers({"x-frame-options": "DENY"})
    monkeypatch.setattr(websearch, "head", head)
    cat = json.loads(cat_copy.read_text(encoding="utf-8"))
    cat["apps"].append({"id": "wiki", "name": "Wikipedia", "url": "https://en.wikipedia.org", "section": "apps", "group": "web",
                        "about": "", "mark": "W", "logo": "", "tabs": "auto"})
    cat_copy.write_text(json.dumps(cat), encoding="utf-8")
    r = here().post("/api/plugins/desktop/probe", json={}).json()
    assert r["checked"] > 10
    by_id = {a["id"]: a for a in r["apps"]}
    assert by_id["wiki"]["open_as"] == "tab" and by_id["huggingchat"]["open_as"] == "tab"
    assert by_id["gmail"]["open_as"] == "browser" and "X-Frame-Options" in by_id["gmail"]["why"]
    assert by_id["airtac-leetcode"]["open_as"] == "app"
    assert here().post("/api/plugins/desktop/probe", json={}).json()["checked"] == 0      # cached
    # The page may frame only the sites that passed.
    csp = here().get("/").headers["content-security-policy"]
    frame_src = csp.split("frame-src")[1].split(";")[0]
    assert "https://en.wikipedia.org" in frame_src and "https://huggingface.co" in frame_src and "mail.google.com" not in frame_src
    # After 7 days it's checked again.
    results = embed.cache()
    results["wiki"]["at"] -= embed.TTL + 1
    embed.save(results)
    assert desktop.embed_state(desktop.find_app("wiki"))["probe"] == "due"


def test_no_internet_is_retried_not_remembered_as_a_no(monkeypatch):
    def offline(url, *a, **k):
        raise websearch.NotFound("I couldn't find the website x.example.")
    monkeypatch.setattr(websearch, "head", offline)
    r = embed.probe("https://x.example", render=False)
    assert r["retry"] and not embed.fresh({**r, "url": "https://x.example"}, "https://x.example")


def test_external_apps_never_open_as_tabs_and_unchecked_ones_wait():
    a = {"id": "z", "url": "https://z.example", "tabs": "external"}
    embed.save({"z": {"ok": True, "why": "", "final": "https://z.example", "at": int(time.time()), "url": "https://z.example"}})
    assert desktop.embed_state(a)["open_as"] == "browser"
    assert desktop.embed_state({"id": "q", "url": "https://q.example", "tabs": "auto"})["open_as"] == "browser"


def test_frame_sources_only_take_plain_https_origins():
    plugin_api.frame_source("test", lambda: {"https://ok.example", "http://plain.example", "https://x.example/path", "javascript:1",
                                             "https://a.example; script-src *"})
    try:
        assert plugin_api.frame_sources(refresh=True) >= {"https://ok.example"}
        assert not any(o in plugin_api.frame_sources() for o in ("http://plain.example", "https://x.example/path"))
        assert all(";" not in o and " " not in o for o in plugin_api.frame_sources())
    finally:
        plugin_api._FRAME_FNS.pop("test")
        plugin_api.frame_sources(refresh=True)


def test_tabs_are_sandboxed_limited_and_never_proxy_a_site():
    js = (ROOT / "web" / "tabs.js").read_text(encoding="utf-8")
    assert 'SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups"' in js
    assert 'referrerpolicy: "no-referrer"' in js and "MAX_LIVE = 5" in js and "innerHTML" not in js
    assert "allow-top-navigation" not in js
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for needed in ('id="view-apps"', 'id="view-tabs"', 'id="tab-search"', 'id="tab-college"', 'tabs.js?v=', 'id="app-dialog"',
                   'id="find-open"', 'id="web-engines"', 'id="set-engine"', "may not be kept"):
        assert needed in html, needed
    assert '"tabs.js"' in (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
    py = (ROOT / "plugins" / "desktop" / "__init__.py").read_text(encoding="utf-8").lower()
    assert "x-frame-options" not in py.replace("x-frame-options: ...", "")   # nothing here rewrites a site's headers


# ---------------------------------------------------------------- favicons

def test_favicon_is_fetched_once_and_svg_is_refused(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    calls = []
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    monkeypatch.setattr(websearch, "fetch_icon", lambda url: calls.append(url) or (png, "image/png"))
    c = here()
    r = c.get("/api/plugins/desktop/favicon/chatgpt")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == png
    assert c.get("/api/plugins/desktop/favicon/chatgpt").status_code == 200 and len(calls) == 1
    assert websearch.icon_type(b"<svg onload=alert(1)>") == "" and websearch.icon_type(b"\x00\x00\x01\x00rest") == "image/x-icon"
    monkeypatch.setattr(websearch, "fetch_icon", lambda url: calls.append(url) or (b"", ""))
    assert c.get("/api/plugins/desktop/favicon/grok").status_code == 404
    assert c.get("/api/plugins/desktop/favicon/grok").status_code == 404 and len(calls) == 2   # a miss is remembered
    assert c.get("/api/plugins/desktop/favicon/not-an-app").status_code == 404


def test_apps_without_a_logo_file_offer_the_favicon(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    chatgpt = desktop.find_app("chatgpt")
    claude = desktop.find_app("claude")
    assert chatgpt["favicon"] == "api/plugins/desktop/favicon/chatgpt" and claude["favicon"] == "" and claude["logo"]
    folder = config.DB_PATH.parent / "favicons"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "chatgpt.none").write_bytes(b"")                        # a miss remembered: no request, the badge at once
    assert desktop.find_app("chatgpt")["favicon"] == ""
    monkeypatch.setattr(config, "OFFLINE", True)
    (folder / "chatgpt.none").unlink()
    assert desktop.find_app("chatgpt")["favicon"] == ""                # offline: no request either


# ---------------------------------------------------------------- web search engines

def test_merge_puts_pages_found_by_several_engines_first():
    r = lambda t, u, src: websearch._result(t, u, "", src)   # noqa: E731
    merged = websearch.merge({
        "ddg": [r("A", "https://a.example/", "DuckDuckGo"), r("B", "https://b.example/x", "DuckDuckGo")],
        "bing": [r("B", "https://www.b.example/x", "Bing"), r("C", "https://c.example", "Bing")],
    })
    assert merged[0]["url"].endswith("b.example/x") and merged[0]["sources"] == ["DuckDuckGo", "Bing"]
    assert len(merged) == 3


def test_search_many_reports_slow_and_failed_engines(monkeypatch):
    def fake(query, n, engine, region):
        if engine == "bing":
            raise httpx.ConnectError("down")
        if engine == "brave":
            time.sleep(1.5)
        return [websearch._result(engine, f"https://{engine}.example/", "", engine)]
    monkeypatch.setattr(websearch, "search_ddgs", fake)
    monkeypatch.setattr(websearch, "search_ddg", lambda q, n, region: fake(q, n, "ddg", region))
    got, failed = websearch.search_many("x", ["ddg", "bing", "brave", "google"], budget=0.5)
    assert set(got) == {"ddg", "google"} and failed == {"bing": "failed", "brave": "too slow"}


def test_web_page_all_results_comes_from_two_or_more_engines(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)

    def many(query, engines, n=8, budget=12, **keys):
        return ({e: [websearch._result(f"{e} hit", f"https://{e}.example/", "", e.title())] for e in engines if e != "google"},
                {"google": "too slow"})
    monkeypatch.setattr(websearch, "search_many", many)
    r = here().get("/api/plugins/web/search", params={"q": "dbms normal forms", "provider": "all"}).json()
    assert len(r["answered"]) >= 2 and r["failed"] == {"Google": "too slow"} and len(r["results"]) == 4
    one = here().get("/api/plugins/web/search", params={"q": "x", "provider": "bing"}).json()
    assert one["answered"] == ["Bing"]
    assert here().get("/api/plugins/web/search", params={"q": "x", "provider": "gcse"}).status_code == 400   # no key set
    names = [e["id"] for e in here().get("/api/plugins/web/engines").json()["engines"]]
    assert names[:5] == ["ddg", "bing", "brave", "google", "wikipedia"] and "searxng" not in names   # SearXNG is off by default


def test_google_key_goes_in_a_header_not_the_address():
    import inspect
    src = inspect.getsource(websearch.search_google_cse)
    assert "X-goog-api-key" in src and '"key"' not in src


def test_default_search_engine_setting():
    assert st.load()["search_engine"] == "google"
    assert st.save({"search_engine": "perplexity"})["search_engine"] == "perplexity"
    with pytest.raises(ValueError):
        st.save({"search_engine": "yahoo"})


# ---------------------------------------------------------------- Search everything (Ctrl+K)

def test_search_everything_finds_a_chat_a_setting_and_an_app():
    memory.add_message("s1", "user", "When is my DBMS exam?")
    chats = finder.search("dbms exam")["results"]
    assert any(r["kind"] == "chats" and "DBMS" in r["title"] for r in chats)
    assert any(r["kind"] == "settings" and r["title"] == "Theme" for r in finder.search("theme")["results"])
    apps = [r for r in finder.search("gmail")["results"] if r["kind"] == "apps"]
    assert apps and apps[0]["go"]["app"] == "gmail"


def test_search_everything_covers_notes_schedule_and_files(tmp_path):
    from plugins.research import create_note
    from plugins.schedule import store
    create_note("Graph notes", "Dijkstra uses a priority queue")
    store.add_event("DBMS lab viva", "2026-11-02")
    (config.OUTPUTS_DIR / "study").mkdir(parents=True)
    (config.OUTPUTS_DIR / "study" / "dbms-unit-2.pdf").write_bytes(b"%PDF")
    kinds = {r["kind"] for r in finder.search("dbms")["results"]}
    assert {"schedule", "files"} <= kinds
    assert finder.search("dijkstra")["results"][0]["kind"] == "notes"


def test_search_everything_route_filters_and_checks_kinds():
    memory.add_message("s1", "user", "open gmail for me")
    r = here().get("/api/plugins/finder", params={"q": "gmail", "kinds": "apps"}).json()
    assert r["results"] and {x["kind"] for x in r["results"]} == {"apps"}
    assert here().get("/api/plugins/finder", params={"q": "x", "kinds": "passwords"}).status_code == 400
    assert here().get("/api/plugins/finder", params={"q": ""}).json()["results"] == []
    assert here().get("/api/plugins/finder", params={"q": "100%_"}).status_code == 200   # wildcards are plain text


def test_palette_has_filters_recents_and_a_phone_button():
    js = (ROOT / "web" / "dock.js").read_text(encoding="utf-8")
    assert "airtac-recent-finds" in js and "palette-filters" in js and 'find-open' in js and "host.find" in js
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "/api/plugins/finder" in app_js and "function goToResult" in app_js
