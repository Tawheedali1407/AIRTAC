"""Home's News: sources side by side, the last good copy when one fails, arXiv RSS as the fallback."""
import time

import pytest

from airtac import config
from plugins import news

RSS = """<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"><channel><title>cs.AI updates on arXiv.org</title>
<item><title>A study of protein folding</title><link>https://arxiv.org/abs/2610.00001</link>
<description>Abstract: proteins.</description><dc:creator>A. One, B. Two</dc:creator></item>
<item><title>Planning with LLM agents under a live clock</title><link>https://arxiv.org/abs/2610.20665</link>
<description>Abstract: agents act while the world moves.</description><dc:creator>C. Three, D. Four, E. Five</dc:creator></item>
</channel></rss>"""

HN = {"hits": [{"title": "Story", "url": "https://example.com/s", "points": 10, "num_comments": 2, "objectID": "1"}]}


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)


def _fake(answers, calls=None):
    def fetch(url, **kw):
        if calls is not None:
            calls.append((url, kw.get("retry", True), kw.get("timeout")))
        for key, value in answers.items():
            if key in url:
                return value() if callable(value) else value
        return None
    return fetch


def test_rss_fallback_picks_papers_matching_the_topic():
    papers = news.parse_arxiv_rss(RSS, "LLM agents")
    assert papers[0]["title"].startswith("Planning with LLM agents") and papers[0]["authors"] == ["C. Three", "D. Four"]
    assert news.parse_arxiv_rss('<!DOCTYPE x [<!ENTITY a "b">]><rss/>', "x") == []      # XML bombs refused


def test_arxiv_search_down_falls_back_to_rss(online, monkeypatch):
    calls = []
    monkeypatch.setattr(news, "fetch", _fake({"rss.arxiv.org": RSS}, calls))
    r = news.research()
    assert r["items"][0]["url"] == "https://arxiv.org/abs/2610.20665" and "instead" in r["note"]
    assert all(retry is False and timeout == news.FEED_TIMEOUT for _, retry, timeout in calls)   # fast, no retry


def test_a_failed_source_shows_its_last_good_copy(online, monkeypatch):
    monkeypatch.setattr(news, "fetch", _fake({"hn.algolia.com": HN}))
    assert news.tech()["items"][0]["title"] == "Story"                    # a good answer is remembered
    monkeypatch.setattr(news, "fetch", _fake({}))
    later = news.tech()
    assert later["items"][0]["title"] == "Story" and later.get("stale") and "didn't answer" in later["note"]


def test_nothing_saved_yet_says_what_failed(online, monkeypatch):
    monkeypatch.setattr(news, "fetch", _fake({}))
    c = news.contests()
    assert c["items"] == [] and c["note"] == "Codeforces didn't answer. Try again soon."


def test_sources_are_fetched_side_by_side(online, monkeypatch):
    def slow():
        time.sleep(0.6)
        return HN
    monkeypatch.setattr(news, "fetch", _fake({"hn.algolia.com": slow, "export.arxiv.org": lambda: (time.sleep(0.6), None)[1],
                                              "rss.arxiv.org": RSS, "codeforces.com": lambda: (time.sleep(0.6), None)[1]}))
    t = time.monotonic()
    sections = news.all_sections()
    assert time.monotonic() - t < 1.5                                         # not 0.6 x 3 in a row
    assert [s["id"] for s in sections] == ["tech", "research", "contests"]


def test_threads_keep_the_news_plugins_permissions(online, monkeypatch):
    """fetch() checks which plugin is asking; the worker threads must still count as "news"."""
    from airtac import plugin_api
    seen = []

    def check(url, **kw):
        seen.append(plugin_api.allowed(url))
        return None
    monkeypatch.setattr(news, "fetch", check)
    token = plugin_api.CURRENT.set("news")
    try:
        news.all_sections()
    finally:
        plugin_api.CURRENT.reset(token)
    assert seen and all(seen)                       # hn, arXiv API, arXiv RSS, Codeforces all allowed
