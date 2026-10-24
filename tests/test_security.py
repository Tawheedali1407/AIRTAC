"""Security checks for the Advance version: headers, page policy, other websites, proxies, limits."""
import keyring
import pytest
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend

from airtac import config, connections, main
from airtac.main import app

here = TestClient(app, base_url="http://localhost")


def test_headers_and_page_policy():
    page = here.get("/")
    for h in ("x-content-type-options", "referrer-policy", "x-frame-options", "permissions-policy"):
        assert h in page.headers, h
    csp = page.headers["content-security-policy"]
    assert "script-src 'self' 'sha256-" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert here.get(path).status_code == 404


def test_other_websites_cant_make_changes():
    evil = {"Origin": "https://evil.example"}
    assert here.post("/api/plugins/schedule", json={"title": "x", "date": "2026-11-14"}, headers=evil).status_code == 403
    assert here.post("/api/connections/groq", json={"values": {"GROQ_API_KEY": "x"}}, headers=evil).status_code == 403
    assert here.post("/api/chat", json={"message": "hi"}, headers=evil).status_code == 403
    r = here.post("/api/connections/groq", content='{"values": {}}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 422                                           # a form can't pass as JSON


def test_a_proxy_in_front_is_never_this_pc():
    assert here.get("/api/health", headers={"X-Forwarded-For": "100.64.0.9"}).status_code == 401


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def get_password(self, service, username):
        return self.items.get((service, username))

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


def test_a_new_passcode_signs_other_devices_out(monkeypatch):
    old = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    monkeypatch.setattr(config, "TRUSTED_HOSTS", config.TRUSTED_HOSTS + ["ali-pc.tail1234.ts.net"])
    phone = TestClient(app, base_url="https://ali-pc.tail1234.ts.net", client=("100.64.0.7", 50000))
    try:
        connections.save("phone", {"AIRTAC_PASSCODE": "first-passcode-1"})
        assert phone.post("/api/login", json={"passcode": "first-passcode-1"}).status_code == 200
        assert phone.get("/api/health").status_code == 200
        connections.save("phone", {"AIRTAC_PASSCODE": "second-passcode-2"})
        assert phone.get("/api/health").status_code == 401
    finally:
        keyring.set_keyring(old)


def test_other_devices_have_a_limit(monkeypatch):
    monkeypatch.setattr(config, "PASSCODE", "long-passcode-42")
    monkeypatch.setattr(config, "TRUSTED_HOSTS", config.TRUSTED_HOSTS + ["ali-pc.tail1234.ts.net"])
    monkeypatch.setattr(main, "limiter", main.RateLimiter(per_hour=3, per_day=100))
    phone = TestClient(app, base_url="https://ali-pc.tail1234.ts.net", client=("100.64.0.7", 50000))
    phone.post("/api/login", json={"passcode": "long-passcode-42"})   # the login counts as one request too
    codes = [phone.post("/api/chat", json={"message": "Top Hacker News stories"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert all(here.post("/api/chat", json={"message": "Top Hacker News stories"}).status_code == 200 for _ in range(3))


def test_xml_bombs_from_a_feed_are_refused():
    from plugins.news import parse_arxiv
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
            '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&lol2;</title></entry></feed>')
    assert parse_arxiv(bomb) == []


def test_request_fields_are_bounded():
    assert here.post("/api/plugins/accounts/username", json={"site": "x" * 100, "username": "a"}).status_code == 422
    assert here.post("/api/plugins/desktop/launch", json={"url": "https://" + "a" * 600}).status_code == 422


def test_ssrf_guard_blocks_loopback_in_disguise(monkeypatch):
    """A review found numeric/short/mapped IP forms that resolve to loopback or the cloud metadata
    address must not pass the reader's guard (the Agent Reach CLI would read them on the PC itself)."""
    import socket
    from plugins import agentreach
    pretend = {"2130706433": "127.0.0.1", "127.1": "127.0.0.1", "0x7f.0.0.1": "127.0.0.1",
               "metadata.google.internal": "169.254.169.254"}

    def fake_getaddrinfo(host, *a, **k):
        if host in pretend:
            return [(socket.AF_INET, None, None, "", (pretend[host], 0))]
        if host in ("example.com", "r.jina.ai"):
            return [(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]
        raise OSError("no such host")

    monkeypatch.setattr(agentreach.socket, "getaddrinfo", fake_getaddrinfo)
    for bad in ("http://2130706433/", "http://127.1/", "http://0x7f.0.0.1/", "http://[::ffff:127.0.0.1]/",
                "http://[::ffff:169.254.169.254]/latest/meta-data/", "http://metadata.google.internal/",
                "http://169.254.169.254/", "file:///etc/passwd", "http://10.0.0.5/"):
        assert agentreach._safe_url(bad) == "", bad
    assert agentreach._safe_url("https://example.com/post") == "https://example.com/post"


def test_login_is_rate_limited_for_remote_devices(monkeypatch):
    monkeypatch.setattr(config, "PASSCODE", "a-good-long-passcode")
    monkeypatch.setattr(config, "TRUSTED_HOSTS", config.TRUSTED_HOSTS + ["ali-pc.tail1234.ts.net"])
    monkeypatch.setattr(main, "limiter", main.RateLimiter(per_hour=3, per_day=100))
    phone = TestClient(app, base_url="https://ali-pc.tail1234.ts.net", client=("100.64.0.7", 50000))
    # Wrong-passcode floods from a device are capped by the remote limiter (on top of the 10-minute lockout).
    codes = [phone.post("/api/login", json={"passcode": "wrong"}).status_code for _ in range(4)]
    assert codes.count(429) >= 1
