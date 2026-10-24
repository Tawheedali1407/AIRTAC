"""The apps catalog (AI apps and social sites), their logos, opening them, and the Settings page."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airtac import config
from plugins import desktop
from plugins import settings as st

ROOT = Path(__file__).resolve().parent.parent


def test_every_app_has_a_logo_file_or_the_logo_missing_flag():
    apps = desktop.all_apps()
    assert {a["name"] for a in desktop.social_apps()} == {"YouTube", "Facebook", "Instagram", "X", "LinkedIn",
                                                          "WhatsApp Web", "Telegram Web", "Reddit"}
    assert {"Gmail", "YouTube", "Google", "Google Drive", "LinkedIn", "WPS Office", "DigiLocker", "Canvas",
            "LeetCode", "GitHub", "EduSkills", "Music", "News", "Backup", "Desktop"} <= {a["name"] for a in desktop.web_apps()}
    for name in ("Claude", "Gemini", "Groq", "Perplexity", "DeepSeek", "Emergent", "NotebookLM", "Notewise", "Manus", "Higgsfield",
                 "Lovable", "ChatGPT", "Grok", "Google AI Studio", "Copilot", "Le Chat", "HuggingChat"):
        assert any(a["name"] == name for a in apps), name
    for a in apps:
        if a["logo_missing"]:
            assert a["logo"] == "" and a["mark"]
        else:
            assert (ROOT / "web" / a["logo"]).is_file() and a["logo"].startswith("logos/")


def test_missing_logos_are_listed_in_the_credits():
    credits = (ROOT / "web" / "logos" / "CREDITS.md").read_text(encoding="utf-8")
    assert "trademarks of their owners" in credits and "CC0" in credits
    for a in desktop.all_apps():
        if a["logo_missing"] and not a.get("app"):
            assert a["name"] in credits, a["name"]


def test_logos_are_plain_local_svgs():
    for f in (ROOT / "web" / "logos").glob("*.svg"):
        text = f.read_text(encoding="utf-8")
        # Content Credentials (C2PA) metadata names its own XML namespace; that's an identifier, not a download.
        text = text.replace("http://c2pa.org/manifest", "")
        assert "<script" not in text and "http://" not in text.replace("http://www.w3.org/2000/svg", "") and "https://" not in text


def test_opening_returns_the_catalog_address_and_never_credentials():
    for a in desktop.all_apps():
        if a.get("app"):
            continue
        for page in ("home", "login"):
            url = desktop.open_target(a["id"], page)
            assert url.startswith("https://") and "@" not in url.split("/")[2]
            assert not any(word in url.lower() for word in ("password=", "passwd", "token=", "pass="))
    assert desktop.open_target("instagram", "login") == "https://www.instagram.com/accounts/login/"
    with pytest.raises(ValueError):
        desktop.open_target("not-an-app")


def test_addresses_with_a_user_or_password_are_refused():
    assert desktop.safe_url("https://chat.example.com/")
    for bad in ("https://ali:secret@example.com/", "http://example.com", "javascript:alert(1)", "https://a.com/\"x", "https://"):
        assert not desktop.safe_url(bad), bad


def test_open_by_id_only_on_this_pc(monkeypatch):
    from airtac.main import app
    opened = []
    monkeypatch.setattr(desktop, "open_url", lambda url, browser="chrome": opened.append((url, browser)) or "chrome")
    here = TestClient(app, base_url="http://localhost")
    assert here.post("/api/plugins/desktop/open", json={"id": "claude"}).json()["url"] == "https://claude.ai"
    assert here.post("/api/plugins/desktop/open", json={"id": "x", "page": "login"}).status_code == 200
    assert opened == [("https://claude.ai", "chrome"), ("https://x.com/i/flow/login", "default")]   # social: your browser
    assert here.post("/api/plugins/desktop/open", json={"id": "claude", "page": "../etc"}).status_code == 422
    assert TestClient(app).post("/api/plugins/desktop/open", json={"id": "claude"}).status_code in (401, 403)


def test_settings_theme_and_choices_save_and_read_back():
    assert st.load()["theme"] == "crimson"
    st.save({"theme": "contrast", "social_browser": "chrome", "session_token_limit": 5000})
    assert json.loads(st.path().read_text())["theme"] == "contrast"
    assert st.get("social_browser") == "chrome" and st.get("session_token_limit") == 5000
    for bad in ({"theme": "neon"}, {"social_browser": "edge"}, {"session_token_limit": -1}, {"brain": "nope"}):
        with pytest.raises(ValueError):
            st.save(bad)
    from airtac.main import app
    c = TestClient(app, base_url="http://localhost")
    assert c.post("/api/plugins/settings", json={"theme": "day"}).json()["theme"] == "day"
    assert "contrast" in c.get("/api/plugins/settings").json()["themes"]


def test_api_keys_on_the_settings_page_are_masked(monkeypatch):
    from airtac import connections
    from airtac.main import app
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_live_abcdefghijklmnop1234"})
    data = TestClient(app, base_url="http://localhost").get("/api/connections").text
    assert "abcdefghijklmnop" not in data and "••••1234" in data
    groq = next(c for c in connections.all_connectors() if c["id"] == "groq")
    assert groq["fields"][0]["shown"] == "••••1234"


def test_chat_header_shows_provider_model_and_tokens(monkeypatch):
    """From a recorded answer: which brain answered, and this session's tokens."""
    from airtac import agent, providers
    from airtac.llm import _report
    providers.LAST.update(tier="groq", model="openai/gpt-oss-120b")
    _report("groq", "openai/gpt-oss-120b", 1200, 300)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    info, usage = agent._brain_info("general"), agent._session_usage("hdr")
    assert info["tier"] == "groq" and info["model"] == "openai/gpt-oss-120b"
    assert usage["session"] == {"input": 1200, "output": 300, "total": 1500}


def test_chat_side_panel_limits_the_tools():
    from airtac import agent, tools
    assert tools.select_tools("anything", only=[]) == []
    only_news = tools.select_tools("open notepad", only=["news"])
    assert only_news and all(tools.REGISTRY[n].plugin == "news" for n in only_news)
    r = agent.chat("top stories on hacker news", "side-1", plugins=[])     # tools off: no tool runs
    assert r.steps == []
    r = agent.chat("top stories on hacker news", "side-2", plugins=["news"])
    assert [s.tool for s in r.steps] == ["hn_top"]


def test_a_tool_that_was_not_offered_never_runs():
    from airtac import agent
    from airtac.llm import LLMReply, ToolCall

    class Sneaky:   # a model that names a tool it wasn't given (say, after reading a tricky web page)
        def chat(self, messages, tools, max_tokens=600):
            return LLMReply(tool_call=ToolCall("open_app", {"name": "cmd"}))
    r = agent._loop("sneaky", [{"role": "user", "content": "hi"}], Sneaky(), ["hn_top"])
    assert r.steps == [] and r.pending is None and "can't use that tool" in r.spoken


def test_chat_request_rejects_odd_plugin_names():
    from airtac.main import app
    c = TestClient(app, base_url="http://localhost")
    assert c.post("/api/chat", json={"message": "hi", "plugins": ["../x"]}).status_code == 422
    assert c.post("/api/chat", json={"message": "hi", "plugins": ["news"] * 41}).status_code == 422
