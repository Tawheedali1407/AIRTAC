"""Agent Reach: reading a page as text, the private-address guard, and the offline fixture."""
import pytest

from airtac import config, tools
from plugins import agentreach as ar


def test_only_public_https_addresses_are_read():
    assert ar._safe_url("example.com/post") == "https://example.com/post"
    assert ar._safe_url("http://news.site/a") == "http://news.site/a"
    for bad in ("localhost:8765", "http://127.0.0.1/x", "http://192.168.0.1", "file:///etc/passwd",
                "javascript:alert(1)", "https://a.com/ x", "ftp://a.com"):
        assert ar._safe_url(bad) == "", bad


def test_it_names_the_kind_of_link():
    assert ar._kind("https://youtu.be/abc") == "YouTube video"
    assert ar._kind("https://x.com/u/status/1") == "tweet"
    assert ar._kind("https://www.reddit.com/r/x/comments/1") == "Reddit thread"
    assert ar._kind("https://github.com/a/b") == "GitHub page"


def test_the_offline_fixture_reads_clean_text():
    t = tools.REGISTRY["agent_read"]
    r = tools.pick(t)("https://example.com/post")
    assert r.ok and "clean text" in r.screen and "https" not in r.spoken


def test_doctor_says_not_installed_when_the_cli_is_missing(monkeypatch):
    monkeypatch.setattr(ar, "cli", lambda: None)
    assert ar.doctor() == {"installed": False, "report": ""}


def test_the_live_read_refuses_a_private_address(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(ar, "cli", lambda: None)
    assert ar.read_live("http://localhost/x").ok is False
    assert ar.read_live("http://192.168.1.5/admin").ok is False


def test_all_private_ip_ranges_are_blocked():
    for bad in ("http://172.16.5.4/x", "http://172.31.255.1", "http://[fc00::1]/x", "http://[::1]/x",
                "http://169.254.1.1", "http://10.1.2.3", "http://192.168.1.1", "http://0.0.0.0",
                "http://router.local", "http://db.internal"):
        assert ar._safe_url(bad) == "", bad
    # A normal public IP is allowed.
    assert ar._safe_url("http://8.8.8.8/x") == "http://8.8.8.8/x"
