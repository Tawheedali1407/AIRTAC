"""Connectors (Ali, 18 Oct: "connectors like Claude has"): Gmail, Google Calendar, Drive, GitHub and Notion. Google's
and Notion's answers are faked here (no network in tests); the shapes follow their API references."""
import base64
import json

import httpx
from fastapi.testclient import TestClient

from airtac import config
from airtac.main import app
from plugins import account, connectors
from airtac.tools import REGISTRY

client = TestClient(app, base_url="http://localhost")


def _fake(monkeypatch, handler, scopes=("gmail", "gmail_drafts", "calendar", "calendar_write", "drive_read")):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(account, "access_token", lambda http=None: "tok")
    monkeypatch.setattr(account, "status", lambda: {"ready": True, "signed_in": True, "sign_in_again": False, "email": "ali@example.com",
                                                     "scopes": [account.SCOPES[s] for s in scopes]})
    monkeypatch.setattr(account, "has_scope", lambda f: f in scopes)
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)
    monkeypatch.setattr(connectors, "_client", httpx.Client(transport=httpx.MockTransport(wrapped)))
    return seen


def test_page_lists_the_five_and_says_what_is_connected(monkeypatch):
    rows = {c["id"]: c for c in client.get("/api/plugins/connectors").json()["connectors"]}
    assert list(rows) == ["gmail", "gcal", "gdrive", "github", "notion"]
    assert not rows["gmail"]["connected"] and not rows["notion"]["connected"]
    monkeypatch.setitem(config.SAVED, "NOTION_TOKEN", "ntn_test")
    _fake(monkeypatch, lambda r: httpx.Response(200, json={}), scopes=("gmail",))
    rows = {c["id"]: c for c in client.get("/api/plugins/connectors").json()["connectors"]}
    assert rows["gmail"]["connected"] and rows["gmail"]["granted"] == ["gmail"] and not rows["gcal"]["connected"]
    assert rows["notion"]["connected"]
    client.post("/api/plugins/connectors/gmail/switch", json={"on": False})
    assert "switched off" in connectors.gmail_search("x").spoken
    for name in ("gmail_search", "gmail_draft", "calendar_add", "drive_read", "notion_read"):
        assert name in REGISTRY
    assert REGISTRY["gmail_draft"].risk == "write" and REGISTRY["calendar_add"].risk == "write"


def test_not_connected_or_offline_says_so():
    assert "Connect Gmail first" in connectors.gmail_search("x").spoken or "offline" in connectors.gmail_search("x").spoken
    assert "offline" in connectors.notion_search("x").spoken


def test_gmail_search_read_and_draft(monkeypatch):
    body = base64.urlsafe_b64encode(b"Hi Ali, your interview is on Monday.").decode()

    def handler(r):
        if r.url.path.endswith("/messages") and r.method == "GET":
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if r.url.path.endswith("/messages/m1"):
            return httpx.Response(200, json={"id": "m1", "snippet": "your interview is on Monday", "payload": {
                "headers": [{"name": "From", "value": "Priya <priya@acme.example>"}, {"name": "Subject", "value": "Interview"},
                            {"name": "Date", "value": "Sun, 18 Oct 2026 09:00:00 +0530"}],
                "mimeType": "multipart/alternative", "parts": [{"mimeType": "text/plain", "body": {"data": body}}]}})
        if r.url.path.endswith("/drafts"):
            return httpx.Response(200, json={"id": "d1"})
        return httpx.Response(404)
    seen = _fake(monkeypatch, handler)
    r = connectors.gmail_search("from:acme")
    assert r.spoken == "1 email. Newest from Priya: Interview." and "your interview is on Monday" in r.screen
    assert seen[0].headers["authorization"] == "Bearer tok" and seen[0].url.params["q"] == "from:acme"
    assert "Hi Ali, your interview is on Monday." in connectors.gmail_read("interview").screen
    d = connectors.gmail_draft("priya@acme.example", "Thanks", "Thank you!")
    assert "draft" in d.spoken and "never" not in d.spoken
    raw = json.loads(seen[-1].content)["message"]["raw"]
    assert b"To: priya@acme.example" in base64.urlsafe_b64decode(raw + "==")
    assert "email address" in connectors.gmail_draft("priya", "x", "y").spoken


def test_calendar_and_drive(monkeypatch):
    def handler(r):
        if "calendar" in r.url.path and r.method == "GET":
            return httpx.Response(200, json={"items": [{"summary": "Workshop", "start": {"dateTime": "2030-01-02T11:00:00+05:30"}}]})
        if "calendar" in r.url.path and r.method == "POST":
            return httpx.Response(200, json={"htmlLink": "https://calendar.google.com/x"})
        if r.url.path.endswith("/files"):
            return httpx.Response(200, json={"files": [{"id": "f1", "name": "DBMS notes", "mimeType": "application/vnd.google-apps.document",
                                                        "modifiedTime": "2026-10-01T00:00:00Z", "webViewLink": "https://docs.google.com/x"}]})
        if r.url.path.endswith("/f1/export"):
            return httpx.Response(200, text="Normal forms: 1NF, 2NF, 3NF.")
        return httpx.Response(404)
    seen = _fake(monkeypatch, handler)
    assert connectors.calendar_upcoming(7).spoken.startswith("1 event. Next: Workshop")
    assert "Added Study session" in connectors.calendar_add("Study session", "tomorrow at 6 pm").spoken
    sent = json.loads(seen[-1].content)
    assert sent["summary"] == "Study session" and "T18:00" in sent["start"]["dateTime"]
    assert connectors.drive_search("dbms notes").spoken.startswith("1 file in Drive")
    assert "fullText contains 'dbms'" in seen[-1].url.params["q"]
    assert "1NF, 2NF, 3NF" in connectors.drive_read("dbms").screen


def test_notion(monkeypatch):
    monkeypatch.setitem(config.SAVED, "NOTION_TOKEN", "ntn_test")
    page = {"object": "page", "id": "p1", "url": "https://notion.so/p1", "last_edited_time": "2026-10-10T00:00:00Z",
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Project plan"}]}}}

    def handler(r):
        if r.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [page]})
        if "/blocks/p1/children" in r.url.path:
            return httpx.Response(200, json={"results": [
                {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Goals"}]}},
                {"type": "to_do", "to_do": {"checked": False, "rich_text": [{"plain_text": "Ship AIRTAC chat"}]}}]})
        return httpx.Response(404)
    seen = _fake(monkeypatch, handler)
    assert connectors.notion_search("plan").spoken == "1 result in Notion. First: Project plan."
    assert seen[0].headers["notion-version"] == "2022-06-28" and seen[0].headers["authorization"] == "Bearer ntn_test"
    assert "## Goals\n[ ] Ship AIRTAC chat" in connectors.notion_read("plan").screen
