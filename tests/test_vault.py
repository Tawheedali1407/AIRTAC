"""The credential vault, signed-in site data, the Chrome profile launcher, and "this PC only" checks."""
import json

import keyring
import pytest
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend

from airtac import agent, config, memory, tools, vault
from airtac.main import app

here = TestClient(app, base_url="http://localhost")                       # this PC
phone = TestClient(app, base_url="http://localhost", client=("100.64.0.7", 50000))   # another device, faking the name


class MemoryKeyring(KeyringBackend):
    """Stands in for Windows Credential Manager in tests, and enforces its size limit."""
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}

    def set_password(self, service, username, password):
        assert len(password.encode("utf-16-le")) <= 2560, "Credential Manager holds at most 2,560 bytes"
        self.items[(service, username)] = password

    def get_password(self, service, username):
        return self.items.get((service, username))

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


@pytest.fixture
def store():
    old = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(old)


def test_save_read_split_and_delete(store):
    cookie = "LEETCODE_SESSION=" + "x" * 2400 + "; csrftoken=abc123"
    vault.save("accounts", "leetcode", "cookie", cookie)
    vault.save("accounts", "leetcode", "password", "hunter2-not-real")
    assert vault.get("accounts", "leetcode", "cookie") == cookie            # joined back from 3 parts
    assert vault.get("accounts", "leetcode", "password") == "hunter2-not-real"
    assert vault.summary("accounts", "leetcode")["cookie"]                  # a date, not the value
    index = memory.kv_get("vault", "index")
    assert "hunter2" not in index and "LEETCODE_SESSION" not in index      # AIRTAC's own database holds no secret
    vault.save("accounts", "leetcode", "cookie", "short=1")                 # shorter: old parts removed
    assert vault.get("accounts", "leetcode", "cookie") == "short=1"
    assert sum(1 for (_, name) in store.items if name.startswith("accounts/leetcode/cookie")) == 1
    vault.delete("accounts", "leetcode")
    assert vault.summary("accounts", "leetcode") == {"password": "", "cookie": ""} and store.items == {}


def test_no_safe_store_means_nothing_is_kept(monkeypatch):
    from keyring.backends import fail
    old = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        assert vault.available()[0] is False
        with pytest.raises(vault.VaultUnavailable):
            vault.save("accounts", "gfg", "password", "x")
        r = here.post("/api/plugins/accounts/vault", json={"site": "gfg", "password": "x"})
        assert r.status_code == 503 and "safe password store" in r.json()["detail"]
    finally:
        keyring.set_keyring(old)


def test_routes_work_only_on_this_pc(store):
    r = here.post("/api/plugins/accounts/vault", json={"site": "codechef", "password": "pw-1", "cookie": "c=1"})
    assert r.status_code == 200 and r.json()["vault"]["password"]
    assert here.post("/api/plugins/accounts/vault/reveal", json={"site": "codechef", "field": "password"}).json() == {"value": "pw-1"}
    # Another device, even one that claims to be "localhost", gets nothing.
    for path, body in (("/vault", {"site": "codechef", "password": "evil"}),
                       ("/vault/reveal", {"site": "codechef", "field": "password"}),
                       ("/vault/delete", {"site": "codechef"})):
        assert phone.post(f"/api/plugins/accounts{path}", json=body).status_code in (401, 403)
    assert vault.get("accounts", "codechef", "password") == "pw-1"
    page = here.get("/api/apps/accounts").json()
    card = next(c for c in page["accounts"]["cards"] if c["id"] == "codechef")
    assert card["vault"]["password"] and "pw-1" not in json.dumps(page)     # the page shows "saved", never the value
    assert here.post("/api/plugins/accounts/vault/delete", json={"site": "codechef", "field": "password"}).status_code == 200
    assert vault.get("accounts", "codechef", "password") == "" and vault.get("accounts", "codechef", "cookie") == "c=1"


def test_secrets_never_reach_the_model_or_the_chat(store):
    vault.save("accounts", "leetcode", "password", "pw-SECRET-42")
    vault.save("accounts", "leetcode", "cookie", "LEETCODE_SESSION=tok-SECRET-99; csrftoken=zz")
    for t in tools.REGISTRY.values():                                        # no tool can ask for one
        assert "password" not in json.dumps(t.parameters).lower() and "cookie" not in json.dumps(t.parameters).lower()
    r = agent.chat("Show my coding stats")
    assert "SECRET" not in r.spoken + r.screen
    rows = json.dumps(memory.session_messages("default"))
    assert "SECRET" not in rows


def test_signed_in_leetcode_sends_the_cookie_only_to_leetcode(store, monkeypatch):
    import plugins.accounts as acc
    monkeypatch.setattr(config, "OFFLINE", False)
    vault.save("accounts", "leetcode", "cookie", "LEETCODE_SESSION=tok; csrftoken=abc")
    seen = []

    def fake_fetch(url, **kw):
        seen.append((url, kw.get("headers", {})))
        q = (kw.get("json_body") or {}).get("query", "")
        if "streakCounter" in q:
            return {"data": {"userStatus": {"isSignedIn": True, "username": "MMDTAWHEEDALI"},
                             "streakCounter": {"streakCount": 9, "currentDayCompleted": False}}}
        return {"data": {"matchedUser": {"username": "MMDTAWHEEDALI", "profile": {"ranking": 5},
                "submitStatsGlobal": {"acSubmissionNum": [{"difficulty": "All", "count": 3}, {"difficulty": "Easy", "count": 3}]}}}}

    monkeypatch.setattr(acc, "fetch", fake_fetch)
    stats = acc.live_stats("leetcode")
    assert stats["signed_in"] and stats["streak"] == 9
    assert "Daily streak 9 days, today's not done yet." in acc.line("leetcode", stats)
    with_cookie = [(u, h) for u, h in seen if "Cookie" in h]
    assert with_cookie and all(u.startswith("https://leetcode.com/") for u, _ in with_cookie)
    assert with_cookie[0][1]["x-csrftoken"] == "abc"


def test_chrome_profile_launcher(monkeypatch, tmp_path):
    from plugins.desktop import chrome
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    cmd = chrome.command("https://leetcode.com/accounts/login/", "chrome.exe")
    assert f"--user-data-dir={tmp_path / 'data' / 'chrome_profile'}" in cmd and cmd[-1].startswith("https://")
    with pytest.raises(ValueError):
        chrome.open_url("file:///C:/Windows/system.ini", "chrome.exe")
    # Pretend Chrome is installed (on Windows the real one may be found first) and record what would start. (subprocess, shutil and webbrowser are
    # shared modules, so this holds whichever copy of the plugin the app's routes use.)
    started = []
    monkeypatch.setattr(chrome.shutil, "which", lambda name: "/fake/chrome" if name == "chrome" else None)
    monkeypatch.setattr(chrome.subprocess, "Popen", lambda cmd, **k: started.append(cmd))
    monkeypatch.setattr(chrome.webbrowser, "open", lambda *a, **k: pytest.fail("the default browser was used"))
    assert here.post("/api/plugins/desktop/launch", json={"url": "https://www.youtube.com"}).json() == {"opened": "chrome"}
    assert here.post("/api/plugins/desktop/launch", json={"url": "https://leetcode.com/accounts/login/"}).status_code == 200
    assert here.post("/api/plugins/desktop/launch", json={"url": "https://evil.example/"}).status_code == 400
    assert phone.post("/api/plugins/desktop/launch", json={"url": "https://www.youtube.com"}).status_code in (401, 403)
    assert [c[-1] for c in started] == ["https://www.youtube.com", "https://leetcode.com/accounts/login/"]
    assert all(c[0].lower().endswith(("chrome", "chrome.exe")) and any(a.startswith("--user-data-dir=") and a.endswith("chrome_profile")
                                             for a in c) for c in started)
    started.clear()
    tiles = here.get("/api/plugins/desktop/tiles").json()
    assert [t["name"] for t in tiles["tiles"]] == ["YouTube", "Instagram", "Google"] and tiles["here"] is True
    names = [t["name"] for t in tiles["ai"]]
    assert names[:3] == ["Claude", "Gemini", "Groq"] and {"Perplexity", "DeepSeek", "NotebookLM", "Manus", "Lovable"} <= set(names)
    assert all(t["url"].startswith("https://") for t in tiles["ai"])
    assert here.post("/api/plugins/desktop/launch", json={"url": "https://claude.ai"}).json() == {"opened": "chrome"}
    # The full catalog, and an app you add yourself (this PC only).
    assert {"Higgsfield", "Emergent", "Notewise", "ChatGPT", "Hugging Face"} <= {a["name"] for a in tiles["ai_all"]}
    assert "Higgsfield" in names and len(tiles["ai_all"]) >= 30 and set(tiles["ai_groups"]) >= {"chat", "create", "mine"}
    assert here.post("/api/plugins/desktop/ai_apps", json={"name": "Phind", "url": "javascript:alert(1)"}).status_code == 400
    assert phone.post("/api/plugins/desktop/ai_apps", json={"name": "Evil", "url": "https://evil.example"}).status_code in (401, 403)
    r = here.post("/api/plugins/desktop/ai_apps", json={"name": "Phind", "url": "https://www.phind.com"}).json()
    assert any(a["name"] == "Phind" and a["mine"] for a in r["ai_all"])
    assert here.post("/api/plugins/desktop/launch", json={"url": "https://www.phind.com"}).status_code == 200
    here.post("/api/plugins/desktop/ai_apps/remove", json={"name": "Phind", "url": "https://www.phind.com"})
    assert here.post("/api/plugins/desktop/launch", json={"url": "https://www.phind.com"}).status_code == 400


def test_a_faked_localhost_name_still_needs_the_passcode():
    assert here.get("/api/health").status_code == 200
    assert phone.get("/api/health").status_code == 401
