"""The web search module: providers in order, the reader's text, and the guard that keeps the
reader off private addresses. (Same module and tests as in AIRTAC.)"""
import socket

import httpx
import pytest

from airtac import websearch

PAGE = """<html><head><title>Two pointers &amp; you</title><script>var secret = 1;</script></head>
<body><nav><a href="/home">Home</a> Menu</nav>
<article><h1>Two pointers</h1><p>Two pointers walk an array from both ends.</p>
<ul><li>Sorted input</li><li>O(n) time</li></ul><p>See <a href="/more">more examples</a>.</p></article>
<footer>Copyright</footer></body></html>"""


def test_reader_keeps_the_text_and_drops_scripts_and_menus():
    page = websearch.extract(PAGE, "https://example.com/dsa/")
    assert page["title"] == "Two pointers & you"
    assert "## Two pointers" in page["text"]
    assert "- Sorted input" in page["text"] and "- O(n) time" in page["text"]
    assert "secret" not in page["text"] and "Menu" not in page["text"] and "Copyright" not in page["text"]
    assert {"text": "more examples", "url": "https://example.com/more"} in page["links"]


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/api/health", "http://localhost/", "http://10.0.0.5/",
                                 "http://192.168.1.1/admin", "http://169.254.169.254/latest/meta-data",
                                 "file:///C:/Windows/win.ini", "ftp://example.com/x", "https://user:pw@example.com/"])
def test_reader_refuses_private_or_odd_addresses(url):
    with pytest.raises(websearch.Blocked):
        websearch.check_public(url)


def _fake_dns(monkeypatch, table):
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]
        return real(host, port, *args, **kwargs)
    monkeypatch.setattr(websearch.socket, "getaddrinfo", fake)


def _fake_http(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(websearch.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))


def test_reader_reads_a_public_page(monkeypatch):
    _fake_dns(monkeypatch, {"example.com": "93.184.216.34"})
    _fake_http(monkeypatch, lambda request: httpx.Response(200, html=PAGE))
    page = websearch.read("https://example.com/dsa/")
    assert page["title"] == "Two pointers & you" and "O(n) time" in page["text"]


def test_a_public_page_cant_redirect_the_reader_inside_your_network(monkeypatch):
    # A name that points at 127.0.0.1 (DNS rebinding) and a redirect to 192.168.x are both refused.
    _fake_dns(monkeypatch, {"example.com": "93.184.216.34", "evil.example": "127.0.0.1"})
    _fake_http(monkeypatch, lambda request: httpx.Response(302, headers={"location": "http://192.168.1.1/admin"}))
    with pytest.raises(websearch.Blocked):
        websearch.read("https://example.com/go")
    with pytest.raises(websearch.Blocked):
        websearch.check_public("http://evil.example/")


def test_reader_refuses_downloads(monkeypatch):
    _fake_dns(monkeypatch, {"example.com": "93.184.216.34"})
    _fake_http(monkeypatch, lambda request: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}))
    with pytest.raises(websearch.Blocked):
        websearch.read("https://example.com/file.pdf")


def test_search_falls_back_through_the_providers(monkeypatch):
    calls = []

    def ddg(query, n, region):
        calls.append("ddg")
        raise ImportError("no ddgs")

    def wiki(query, n):
        calls.append("wikipedia")
        return [websearch._result("Two-pointer technique", "https://en.wikipedia.org/wiki/X", "<b>two</b> pointers", "Wikipedia")]
    monkeypatch.setattr(websearch, "search_ddg", ddg)
    monkeypatch.setattr(websearch, "search_wikipedia", wiki)
    rows, provider = websearch.search("two pointers", providers=["searxng", "tavily", "ddg", "wikipedia"])
    assert provider == "wikipedia" and calls == ["ddg", "wikipedia"]  # no SearXNG URL, no Tavily key: skipped
    assert rows[0]["snippet"] == "two pointers"  # HTML from the API is cleaned


def test_search_returns_nothing_when_every_provider_fails(monkeypatch):
    def broken(*args):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(websearch, "search_ddg", broken)
    monkeypatch.setattr(websearch, "search_wikipedia", broken)
    assert websearch.search("anything", providers=["ddg", "wikipedia"]) == ([], "")
