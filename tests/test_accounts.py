"""Accounts: official sign-up/log-in links, usernames (never passwords), stats from public profiles."""
import json

from fastapi.testclient import TestClient

from airtac import agent, config, memory, tools
from airtac.main import app

client = TestClient(app, base_url="http://localhost")


def acc():
    import plugins.accounts as a
    return a


CODECHEF_HTML = """<html><body><div class="rating-header"><div class="rating-number">1532</div>
<span class="rating">3&#9733;</span><small>(Highest Rating 1601)</small></div>
<div class="rating-ranks"><ul><li><a href="/ratings/all"><strong>45,210</strong></a> Global Rank</li></ul></div>
<section><h3>Total Problems Solved: 143</h3></section><script>var x = "Total Problems Solved: 999";</script></body></html>"""

GFG_NEXT = {"props": {"pageProps": {"userInfo": {"name": "Ali", "score": 412, "total_problems_solved": 120,
                                                  "institute_rank": "15", "pod_solved_longest_streak": 21}}}}
GFG_HTML = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(GFG_NEXT)}</script></html>'
GFG_TEXT_ONLY = "<div>Coding Score</div><div>77</div><div>Problem Solved</div><div>31</div>"


def test_no_tool_ever_takes_a_password():
    accounts = [t for t in tools.REGISTRY.values() if t.plugin == "accounts"]
    assert len(accounts) == 3
    for t in accounts:
        assert "password" not in json.dumps(t.parameters).lower()


def test_parse_codechef_profile_page():
    got = acc().parse_codechef(CODECHEF_HTML)
    assert got == {"rating": 1532, "stars": 3, "highest": 1601, "solved": 143, "global_rank": 45210}
    assert acc().parse_codechef("<html>nothing here</html>") is None   # a changed page: no made-up numbers
    assert acc().parse_codechef("") is None


def test_parse_gfg_profile_page():
    assert acc().parse_gfg(GFG_HTML) == {"score": 412, "solved": 120, "institute_rank": 15, "streak": 21}
    text_only = acc().parse_gfg(GFG_TEXT_ONLY)
    assert text_only["score"] == 77 and text_only["solved"] == 31 and text_only["streak"] is None
    assert acc().parse_gfg("<p>Page not found</p>") is None


def test_stats_offline_are_labelled_sample():
    r = tools.run_tool("accounts_stats", {})
    assert r.ok and "[Sample data]" in r.screen
    for name in ("LeetCode", "CodeChef", "GeeksforGeeks", "AICTE EduSkills"):
        assert name in r.screen
    one = tools.run_tool("accounts_stats", {"site": "geeks for geeks"})
    assert one.spoken.startswith("GeeksforGeeks") and "CodeChef" not in one.screen


def test_links_are_the_official_sites():
    for site, host in (("leetcode", "https://leetcode.com/"), ("codechef", "https://www.codechef.com/"),
                       ("gfg", "https://auth.geeksforgeeks.org/"), ("eduskills", "https://eduskillsfoundation.org/")):
        for action in ("signup", "login"):
            r = tools.run_tool("accounts_link", {"site": site, "action": action})
            assert r.ok and r.data["links"][0]["url"].startswith(host)
            assert "http" not in r.spoken            # spoken text never reads out a web address
    assert not tools.run_tool("accounts_link", {"site": "myspace"}).ok


def test_username_saved_checked_and_used_by_leetcode():
    assert tools.run_tool("accounts_username", {"site": "codechef", "username": "@ali_rgm"}).ok
    assert memory.kv_get("accounts", "user:codechef") == "ali_rgm"
    assert acc().links("codechef")["profile"] == "https://www.codechef.com/users/ali_rgm"
    bad = tools.run_tool("accounts_username", {"site": "codechef", "username": "ali; DROP TABLE"})
    assert not bad.ok and memory.kv_get("accounts", "user:codechef") == "ali_rgm"
    tools.run_tool("accounts_username", {"site": "leetcode", "username": "someone_else"})
    import plugins.leetcode as lc
    assert lc.username() == "someone_else"


def test_live_stats_read_each_site_and_never_guess(monkeypatch):
    a = acc()
    monkeypatch.setattr(config, "OFFLINE", False)
    memory.kv_set("accounts", "user:codechef", "ali_rgm")
    memory.kv_set("accounts", "user:gfg", "aligfg")
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        if "leetcode.com/graphql" in url:
            return {"data": {"matchedUser": {"username": "MMDTAWHEEDALI", "profile": {"ranking": 5000},
                    "submitStatsGlobal": {"acSubmissionNum": [{"difficulty": "All", "count": 10},
                     {"difficulty": "Easy", "count": 6}, {"difficulty": "Medium", "count": 3}, {"difficulty": "Hard", "count": 1}]}}}}
        if "codechef.com/users/ali_rgm" in url:
            return CODECHEF_HTML
        return None   # GeeksforGeeks "down"

    monkeypatch.setattr(a, "fetch", fake_fetch)
    r = a.accounts_stats_live()
    assert "LeetCode (MMDTAWHEEDALI): 10 solved" in r.screen
    assert "CodeChef (ali_rgm): rating 1532, 3 star" in r.screen
    assert "GeeksforGeeks: couldn't read aligfg's profile right now." in r.screen
    assert len(calls) == 3
    # Remembered for the page, which opens without waiting on the internet
    cards = {c["id"]: c for c in a.cards(True)}
    assert cards["codechef"]["line"].startswith("rating 1532") and cards["codechef"]["checked"]
    assert cards["gfg"]["line"] == "Press Refresh to read your public profile."
    # A new username: the old numbers are not shown as yours
    a.set_username("codechef", "someone")
    assert a.last_stats("codechef") is None


def test_eduskills_card_uses_the_eduskills_app():
    tools.run_tool("eduskills_add", {"domain": "cybersecurity", "modules_total": 8})
    tools.run_tool("eduskills_update", {"domain": "cyber", "modules_done": 3})
    r = tools.run_tool("accounts_stats", {"site": "eduskills"})
    assert "1 internship, 3 of 8 modules done" in r.spoken


def test_accounts_page_and_routes():
    page = client.get("/api/apps/accounts").json()
    assert page["action"] == "accounts"
    ids = [c["id"] for c in page["accounts"]["cards"]]
    assert ids == ["leetcode", "codechef", "gfg", "eduskills"]
    assert page["accounts"]["live"] is False
    r = client.post("/api/plugins/accounts/username", json={"site": "gfg", "username": "ali_gfg"})
    assert r.status_code == 200 and r.json()["card"]["links"]["profile"].endswith("/user/ali_gfg/")
    assert client.post("/api/plugins/accounts/username", json={"site": "gfg", "username": "a b"}).status_code == 400
    assert client.post("/api/plugins/accounts/username", json={"site": "nope", "username": "x"}).status_code == 400
    again = client.post("/api/plugins/accounts/refresh", json={}).json()
    assert again["live"] is False and len(again["cards"]) == 4


def test_accounts_by_voice():
    assert agent.chat("Show my coding stats").steps[0].tool == "accounts_stats"
    link = agent.chat("Open the CodeChef login")
    assert link.steps[0].tool == "accounts_link" and "CodeChef log-in" in link.screen
    assert agent.chat("Sign up for GeeksforGeeks").steps[0].tool == "accounts_link"
    assert agent.chat("Set my CodeChef username to ali_rgm").steps[0].tool == "accounts_username"
    assert memory.kv_get("accounts", "user:codechef") == "ali_rgm"
    assert agent.chat("What's my LeetCode profile?").steps[0].tool == "leetcode_profile"


def test_ai_illustration_with_a_nebius_key(monkeypatch):
    """Parity with the hackathon version: FLUX.1 schnell on Nebius, labelled AI, never for exact diagrams."""
    import base64
    import io

    from PIL import Image

    import plugins.diagrams as dg
    no_key = tools.run_tool("make_image", {"prompt": "a robot studying", "style": "illustration"})
    assert "Nebius key" in no_key.spoken and "Image:" not in no_key.screen
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 40, 60)).save(buf, "PNG")
    sent = {}

    def fake_fetch(url, **kw):
        sent.update(url=url, **kw)
        return {"data": [{"b64_json": base64.b64encode(buf.getvalue()).decode()}]}

    monkeypatch.setattr(dg, "fetch", fake_fetch)
    r = dg.make_image_live("a robot studying", "illustration")
    assert r.ok and "AI-generated" in r.screen and "/files/diagrams/ai-" in r.screen
    assert sent["url"].startswith("https://api.tokenfactory.nebius.com/") and sent["write"] is True
    exact = dg.make_image_live("binary tree 8 4 12", "diagram")
    assert "not AI" in exact.screen
