"""The app never shows sample data: without a key it says what to connect, without an answer it says so."""
import json

import pytest
from fastapi.testclient import TestClient

from airtac import config, plugin_api
from airtac.main import app
from airtac.tools import run_tool

here = TestClient(app, base_url="http://localhost")


@pytest.fixture
def online(monkeypatch):
    """The app as you use it: online, but every website "doesn't answer" (no real calls in tests)."""
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(plugin_api, "fetch", lambda *a, **k: None)
    import plugins.leetcode as lc
    import plugins.news as news
    monkeypatch.setattr(lc, "fetch", lambda *a, **k: None)
    monkeypatch.setattr(news, "fetch", lambda *a, **k: None)


def test_a_missing_key_names_the_connection(online):
    r = run_tool("github_summary", {})
    assert not r.ok and "GitHub" in r.spoken and "Connections" in r.spoken and "Sample" not in r.screen
    r = run_tool("make_questions", {"subject": "DSA", "count": 2})
    assert "needs a brain" in r.spoken and "Sample" not in r.screen
    assert "Solve or paste a problem first" in run_tool("explain_solution", {}).spoken


def test_no_answer_means_saying_so_never_samples(online):
    assert "couldn't reach LeetCode" in run_tool("leetcode_daily", {}).spoken
    page = here.get("/api/apps/leetcode").json()
    assert "didn't answer" in page["note"] and page["lists"][0]["items"] == []
    gh = here.get("/api/apps/github").json()
    assert "Connect GitHub" in gh["note"] and not gh["stats"]
    progress = here.get("/api/progress").json()
    assert progress["hero"] is None or "LeetCode" not in progress["hero"]["label"]
    for app_id in ("leetcode", "github", "news", "accounts", "practice", "study", "schedule"):
        text = json.dumps(here.get(f"/api/apps/{app_id}").json())
        assert '"sample": true' not in text and "Sample data" not in text, app_id
