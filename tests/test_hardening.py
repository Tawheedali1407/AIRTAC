"""Part D hardening: the per-install API token, secrets kept out of logs, and the plugin network guard."""
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from airtac import auth, config, logsafe, plugin_api
from airtac.main import app


@pytest.fixture
def token_on(monkeypatch):
    monkeypatch.setenv("AIRTAC_NO_INSTALL_TOKEN", "0")


def test_api_needs_the_install_token(token_on):
    client = TestClient(app, base_url="http://localhost")
    r = client.get("/api/history")
    assert r.status_code == 403 and "AIRTAC's own page" in r.json()["detail"]
    assert client.get("/api/health").status_code == 200        # start.ps1 and doctor.py still work


def test_the_page_hands_out_the_cookie_and_then_api_works(token_on):
    client = TestClient(app, base_url="http://localhost")
    page = client.get("/")
    cookie = page.headers.get("set-cookie", "")
    assert auth.INSTALL_COOKIE in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert client.get("/api/history").status_code == 200       # the cookie came back with the call


def test_scripts_can_use_the_header(token_on):
    client = TestClient(app, base_url="http://localhost")
    assert client.get("/api/history", headers={"X-AIRTAC-Token": auth.install_token()}).status_code == 200
    assert client.get("/api/history", headers={"X-AIRTAC-Token": "wrong" * 10}).status_code == 403


def test_the_token_is_stored_once_and_reused():
    first = auth.install_token()
    assert len(first) >= 32 and auth.install_token() == first
    assert (config.DB_PATH.parent / ".install_token").read_text().strip() == first


def test_another_device_on_the_login_page_gets_no_install_cookie(token_on, monkeypatch):
    monkeypatch.setattr(config, "PASSCODE", "a-long-passcode-123")
    client = TestClient(app, client=("192.168.1.20", 5000))
    client.headers["host"] = "localhost"   # a trusted name, but the peer isn't this PC
    r = client.get("/login.html")
    assert auth.INSTALL_COOKIE not in r.headers.get("set-cookie", "")


# ---------------------------------------------------------------- no secrets in logs

def test_log_lines_hide_keys(monkeypatch, caplog):
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_live_abcdefghijklmnop1234"})
    logsafe.install()
    caplog.handler.addFilter(logsafe.RedactFilter())
    log = logging.getLogger("airtac.test")
    with caplog.at_level(logging.INFO):
        log.info("calling %s", "https://serpapi.com/account.json?api_key=abcdef1234567890")
        log.info("header Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwx")
        log.info("saved key was %s", "gsk_live_abcdefghijklmnop1234")
        log.warning("google said AIzaSyA1234567890abcdefghijklmnopqrstuv no")
    text = caplog.text
    for secret in ("abcdef1234567890", "sk-proj-abcdefghijklmnopqrstuvwx", "gsk_live_abcdefghijklmnop1234", "AIzaSyA1234567890"):
        assert secret not in text
    assert text.count("[hidden]") >= 4
    assert "calling https://serpapi.com/account.json?api_key=[hidden]" in text


def test_redact_leaves_normal_text_alone():
    line = "plugin research answered in 1.2 s for session default"
    assert logsafe.redact(line) == line


# ---------------------------------------------------------------- the plugin network guard

def test_plugin_code_may_only_reach_its_manifest_sites(monkeypatch):
    monkeypatch.setattr(plugin_api, "_calling_plugin", lambda: "github")
    plugin_api.check_request("https://api.github.com/user")                    # in github's manifest
    with pytest.raises(plugin_api.Blocked):
        plugin_api.check_request("https://evil.example.com/steal")
    plugin_api.check_request(config.LLM_BASE_URL.rstrip("/") + "/api/ps")       # Ollama on this PC is fine


def test_core_calls_are_not_blocked(monkeypatch):
    monkeypatch.setattr(plugin_api, "_calling_plugin", lambda: "")
    plugin_api.check_request("https://api.groq.com/openai/v1/chat/completions")


def test_guard_sits_in_httpx(monkeypatch):
    """With the guard on, a direct httpx call from a plugin with no network permission never leaves."""
    plugin_api.guard_http()
    monkeypatch.setattr(plugin_api, "_calling_plugin", lambda: "passwords")
    with pytest.raises(plugin_api.Blocked):
        httpx.get("https://example.com/", timeout=1)


def test_restart_is_pc_only_and_starts_the_fixed_script(monkeypatch):
    import subprocess
    import sys
    import threading
    from airtac import main
    phone = TestClient(main.app, base_url="http://localhost", client=("100.64.0.7", 50000))
    assert phone.post("/api/restart").status_code in (401, 403)
    started, timers = [], []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: started.append(args))
    monkeypatch.setattr(threading, "Timer", lambda s, fn: type("T", (), {"start": lambda self: timers.append(s)})())
    r = TestClient(main.app, base_url="http://localhost").post("/api/restart")
    assert r.status_code == 200 and started[0][-4:] == [str(main.config.ROOT / "scripts" / "restart.ps1"), "-Delay", "2", "-NoWindow"]
    assert started[0][0] == "powershell.exe" and timers == [1.0]
