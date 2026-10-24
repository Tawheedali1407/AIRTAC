"""Connectors (Ali, 18 Oct: "connectors like Claude has"): your own apps, added from the Connectors page.

- Gmail: search and read your mail (gmail.readonly); write a draft (gmail.compose), which waits for your Confirm
  and is only saved as a draft: AIRTAC never sends mail. You send it from Gmail.
- Google Calendar: your upcoming events (calendar.readonly); add an event (calendar.events, waits for Confirm).
- Google Drive: find files and read Google Docs and text files (drive.readonly).
- GitHub: the GitHub plugin (your token, saved on APIs and keys).
- Notion: search and read the pages you shared with your integration (its secret, saved on APIs and keys).

Google connectors use Settings > Account's sign-in (your own Desktop-app client, PKCE; the refresh token stays in the
vault) and ask Google for each permission only when you press Connect. Everything you read from mail, files or pages
is treated as data, never as instructions: the agent's rules still apply, and anything that changes something waits.
"""
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx

from airtac import config, dates, memory
from airtac.connections import Field
from airtac.plugin_api import Tool, ToolResult, plugin, schema

p = plugin(__name__)

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
DRIVE = "https://www.googleapis.com/drive/v3/files"
NOTION = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"      # still supported by Notion (developers.notion.com/reference/versioning)
READ_LIMIT = 6000

p.connector(id="notion", name="Notion", group="Services", order=62, logo="notion",
            get_key="https://www.notion.so/profile/integrations", docs="https://developers.notion.com/docs/authorization",
            about="An internal integration's secret. In Notion, share each page or database with the integration (••• > Connections).",
            powers=["search and read your Notion pages"],
            fields=[Field("NOTION_TOKEN", "Internal integration secret", placeholder="ntn_...")])

CONNECTORS = [
    {"id": "gmail", "name": "Gmail", "logo": "gmail", "kind": "google", "features": ["gmail", "gmail_drafts"],
     "about": "Search and read your mail, and write drafts for you to send.", "tools": ["gmail_search", "gmail_read", "gmail_draft"],
     "examples": ["Any emails from my college today?", "Draft a reply to Priya thanking her"]},
    {"id": "gcal", "name": "Google Calendar", "logo": "googlecalendar", "kind": "google", "features": ["calendar", "calendar_write"],
     "about": "See what's coming up and add events (with your OK).", "tools": ["calendar_upcoming", "calendar_add"],
     "examples": ["What's on my calendar this week?", "Add a study session tomorrow at 6 pm"]},
    {"id": "gdrive", "name": "Google Drive", "logo": "googledrive", "kind": "google", "features": ["drive_read"],
     "about": "Find files and read Google Docs and text files.", "tools": ["drive_search", "drive_read"],
     "examples": ["Find my DBMS notes in Drive", "Read my resume doc from Drive"]},
    {"id": "github", "name": "GitHub", "logo": "github", "kind": "key", "key": "GITHUB_TOKEN", "plugin": "github",
     "about": "Your repositories, issues and READMEs (the GitHub plugin).", "tools": ["github_summary", "list_issues", "repo_readme"],
     "examples": ["Summarise my GitHub", "Open issues in my AIRTAC repo"]},
    {"id": "notion", "name": "Notion", "logo": "notion", "kind": "key", "key": "NOTION_TOKEN",
     "about": "Search and read the Notion pages you shared with AIRTAC.", "tools": ["notion_search", "notion_read"],
     "examples": ["Find my project plan in Notion", "What does my Notion reading list say?"]},
]


class Unavailable(Exception):
    """A sentence for the chat: not connected, offline, or the service said no."""


def _account():
    from plugins import account
    return account


def _off() -> set[str]:
    try:
        return set(json.loads(memory.kv_get("connectors", "off") or "[]"))
    except ValueError:
        return set()


def set_on(cid: str, on: bool) -> None:
    off = _off() - {cid} if on else _off() | {cid}
    memory.kv_set("connectors", "off", json.dumps(sorted(off)))


def status() -> list[dict]:
    acc = _account()
    st = acc.status()
    out = []
    for c in CONNECTORS:
        if c["kind"] == "google":
            granted = [f for f in c["features"] if acc.SCOPES[f] in st["scopes"]]
            connected = st["signed_in"] and bool(granted)
            need = "" if st["ready"] else "Set up Google sign-in first (Settings > Account, then docs/GOOGLE_SETUP.md)."
        else:
            granted, connected, need = [], bool(config.secret(c["key"])), ""
        out.append({**{k: c[k] for k in ("id", "name", "logo", "kind", "about", "tools", "examples")}, "connected": connected,
                    "on": c["id"] not in _off(), "granted": granted, "needs": need, "email": st["email"] if c["kind"] == "google" else "",
                    "sign_in_again": st["sign_in_again"] if c["kind"] == "google" else False})
    return out


def _ready(cid: str) -> None:
    if config.OFFLINE:
        raise Unavailable("I'm offline, so I can't reach your apps right now.")
    c = next(x for x in CONNECTORS if x["id"] == cid)
    if cid in _off():
        raise Unavailable(f"{c['name']} is switched off on the Connectors page.")
    if c["kind"] == "google":
        acc = _account()
        if not acc.status()["signed_in"] or not any(acc.has_scope(f) for f in c["features"]):
            raise Unavailable(f"Connect {c['name']} first: Connectors page, then Connect.")
    elif not config.secret(c["key"]):
        raise Unavailable(f"Connect {c['name']} first: Connectors page, then Connect.")


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=20)
    return _client


def _google(method: str, url: str, **kw):
    acc = _account()
    try:
        token = acc.access_token()
    except acc.AccountError as e:
        raise Unavailable(str(e))
    try:
        r = _http().request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
    except httpx.HTTPError:
        raise Unavailable("Google didn't answer. Try again in a minute.")
    if r.status_code == 403:
        raise Unavailable("Google refused: connect it again on the Connectors page (it may need one more permission).")
    if r.status_code == 404:
        raise Unavailable("Google couldn't find that.")
    if r.status_code >= 400:
        raise Unavailable(f"Google answered with an error ({r.status_code}).")
    return r.json() if r.content else {}


def _notion(method: str, path: str, body: dict | None = None):
    try:
        r = _http().request(method, f"{NOTION}{path}", json=body, headers={
            "Authorization": f"Bearer {config.secret('NOTION_TOKEN')}", "Notion-Version": NOTION_VERSION})
    except httpx.HTTPError:
        raise Unavailable("Notion didn't answer. Try again in a minute.")
    if r.status_code == 401:
        raise Unavailable("Notion refused the secret. Check it on APIs and keys.")
    if r.status_code >= 400:
        raise Unavailable(f"Notion answered with an error ({r.status_code}).")
    return r.json()


def _run(cid: str, fn, *args) -> ToolResult:
    try:
        _ready(cid)
        return fn(*args)
    except Unavailable as e:
        return ToolResult(str(e), ok=False)


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- Gmail

def _header(msg: dict, name: str) -> str:
    return next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h.get("name", "").lower() == name.lower()), "")


def _sender(raw: str) -> str:
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', raw or "")
    return (m.group(1) if m else raw or "someone").strip()


def gmail_search(query: str = "newer_than:2d", limit: int = 6) -> ToolResult:
    def go():
        q = (query or "newer_than:2d").strip()[:200]
        found = _google("GET", f"{GMAIL}/messages", params={"q": q, "maxResults": max(1, min(int(limit or 6), 10))}).get("messages", [])
        if not found:
            return ToolResult(f"No emails match {q}.")
        mails = [_google("GET", f"{GMAIL}/messages/{m['id']}", params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
                 for m in found]
        lines = [f"- {_sender(_header(m, 'From'))}: {_header(m, 'Subject') or '(no subject)'} ({_header(m, 'Date')[:16]})\n  {_clip(m.get('snippet', ''), 160)}"
                 for m in mails]
        first = mails[0]
        said = (f"{len(mails)} email{'s' if len(mails) != 1 else ''}. Newest from {_sender(_header(first, 'From'))}: "
                f"{_header(first, 'Subject') or 'no subject'}.")
        return ToolResult(said, screen="Gmail:\n" + "\n".join(lines),
                          data={"ids": [m["id"] for m in mails]})
    return _run("gmail", go)


def _body_text(part: dict) -> str:
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
    for sub in part.get("parts", []) or []:
        text = _body_text(sub)
        if text:
            return text
    return ""


def gmail_read(query: str) -> ToolResult:
    def go():
        found = _google("GET", f"{GMAIL}/messages", params={"q": (query or "").strip()[:200], "maxResults": 1}).get("messages", [])
        if not found:
            return ToolResult(f"No email matches {query}.", ok=False)
        m = _google("GET", f"{GMAIL}/messages/{found[0]['id']}", params={"format": "full"})
        text = _body_text(m.get("payload", {})) or m.get("snippet", "")
        subject, sender = _header(m, "Subject") or "(no subject)", _sender(_header(m, "From"))
        return ToolResult(f"The email from {sender}, {subject}, is on screen.",
                          screen=f"From: {_header(m, 'From')}\nSubject: {subject}\nDate: {_header(m, 'Date')}\n\n{text.strip()[:READ_LIMIT]}")
    return _run("gmail", go)


def gmail_draft(to: str, subject: str, body: str) -> ToolResult:
    def go():
        if not re.fullmatch(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+", (to or "").strip()):
            return ToolResult("Who is it to? I need an email address.", ok=False)
        msg = EmailMessage()
        msg["To"], msg["Subject"] = to.strip(), (subject or "").strip()[:200]
        msg.set_content((body or "").strip()[:20000])
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        _google("POST", f"{GMAIL}/drafts", json={"message": {"raw": raw}})
        return ToolResult(f"I saved a draft to {to.strip()} in Gmail. Open Gmail's Drafts to check it and send it.",
                          screen=f"Draft saved in Gmail (not sent)\nTo: {to}\nSubject: {subject}\n\n{body}")
    return _run("gmail", go)


# ---------------------------------------------------------------- Google Calendar

def _when(e: dict) -> tuple[str, str]:
    start = e.get("start", {})
    if start.get("dateTime"):
        dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone()
        return dt.date().isoformat(), dt.strftime("%H:%M")
    return (start.get("date") or "")[:10], ""


def calendar_upcoming(days: int = 7) -> ToolResult:
    def go():
        now = datetime.now(timezone.utc)
        items = _google("GET", CAL, params={"timeMin": now.isoformat(), "timeMax": (now + timedelta(days=max(1, min(int(days or 7), 60)))).isoformat(),
                                            "singleEvents": "true", "orderBy": "startTime", "maxResults": 25}).get("items", [])
        rows = [(d, t, e.get("summary") or "(no title)") for e in items for d, t in [_when(e)] if d]
        if not rows:
            return ToolResult(f"Nothing on your Google Calendar in the next {days} days.")
        d0, t0, s0 = rows[0]
        said = f"{len(rows)} event{'s' if len(rows) != 1 else ''}. Next: {s0}, {dates.spoken_day(datetime.fromisoformat(d0).date())}" + \
               (f" at {dates.spoken_time(t0)}" if t0 else "") + "."
        return ToolResult(said, screen="Google Calendar:\n" + "\n".join(f"- {dates.nice(datetime.fromisoformat(d).date(), t)}: {s}" for d, t, s in rows))
    return _run("gcal", go)


def calendar_add(title: str, when: str, minutes: int = 60) -> ToolResult:
    def go():
        if not _account().has_scope("calendar_write"):
            return ToolResult("Adding events needs one more permission: Connectors page, Google Calendar, Connect again.", ok=False)
        d, t, _ = dates.find_when(when or "")
        if not d:
            return ToolResult("I couldn't tell the date. Say it like: tomorrow at 6 pm.", ok=False)
        if t:
            start = datetime.fromisoformat(f"{d.isoformat()}T{t}").astimezone()
            body = {"summary": title.strip()[:200], "start": {"dateTime": start.isoformat()},
                    "end": {"dateTime": (start + timedelta(minutes=max(5, min(int(minutes or 60), 1440)))).isoformat()}}
        else:
            body = {"summary": title.strip()[:200], "start": {"date": d.isoformat()}, "end": {"date": (d + timedelta(days=1)).isoformat()}}
        made = _google("POST", CAL, json=body)
        return ToolResult(f"Added {title} to your Google Calendar on {dates.nice(d, t)}.",
                          screen=f"Google Calendar: {title}, {dates.nice(d, t)}\n{made.get('htmlLink', '')}")
    return _run("gcal", go)


# ---------------------------------------------------------------- Google Drive

def _drive_find(query: str, limit: int = 8) -> list[dict]:
    words = [w.replace("\\", "").replace("'", "\\'") for w in (query or "").split()[:6]]
    q = " and ".join(f"fullText contains '{w}'" for w in words) or "trashed = false"
    return _google("GET", DRIVE, params={"q": f"({q}) and trashed = false", "pageSize": limit, "orderBy": "modifiedTime desc",
                                          "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"}).get("files", [])


def drive_search(query: str) -> ToolResult:
    def go():
        files = _drive_find(query)
        if not files:
            return ToolResult(f"No Drive files match {query}.")
        return ToolResult(f"{len(files)} file{'s' if len(files) != 1 else ''} in Drive. First: {files[0]['name']}.",
                          screen="Google Drive:\n" + "\n".join(f"- {f['name']} ({f['modifiedTime'][:10]})\n  {f.get('webViewLink', '')}" for f in files))
    return _run("gdrive", go)


def drive_read(query: str) -> ToolResult:
    def go():
        files = _drive_find(query, 1)
        if not files:
            return ToolResult(f"No Drive file matches {query}.", ok=False)
        f = files[0]
        mime = f.get("mimeType", "")
        if mime == "application/vnd.google-apps.document":
            url, params = f"{DRIVE}/{f['id']}/export", {"mimeType": "text/plain"}
        elif mime.startswith("text/"):
            url, params = f"{DRIVE}/{f['id']}", {"alt": "media"}
        else:
            return ToolResult(f"{f['name']} isn't a Google Doc or text file, so I can't read it here. Its link is on screen.",
                              screen=f"{f['name']}\n{f.get('webViewLink', '')}")
        acc = _account()
        try:
            r = _http().get(url, params=params, headers={"Authorization": f"Bearer {acc.access_token()}"})
        except (httpx.HTTPError, acc.AccountError):
            raise Unavailable("Google didn't answer. Try again in a minute.")
        if r.status_code != 200:
            raise Unavailable(f"Google answered with an error ({r.status_code}).")
        return ToolResult(f"{f['name']} is on screen.", screen=f"{f['name']}\n{f.get('webViewLink', '')}\n\n{r.text.strip()[:READ_LIMIT]}")
    return _run("gdrive", go)


# ---------------------------------------------------------------- Notion

def _title(obj: dict) -> str:
    if obj.get("object") == "database":
        return "".join(t.get("plain_text", "") for t in obj.get("title", [])) or "Untitled database"
    for prop in (obj.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", [])) or "Untitled"
    return "Untitled"


def notion_search(query: str) -> ToolResult:
    def go():
        found = _notion("POST", "/search", {"query": (query or "").strip()[:200], "page_size": 8}).get("results", [])
        if not found:
            return ToolResult(f"Nothing in Notion matches {query}. Is the page shared with your AIRTAC integration?")
        return ToolResult(f"{len(found)} result{'s' if len(found) != 1 else ''} in Notion. First: {_title(found[0])}.",
                          screen="Notion:\n" + "\n".join(f"- {_title(o)} ({o.get('object')}, edited {o.get('last_edited_time', '')[:10]})\n  {o.get('url', '')}" for o in found))
    return _run("notion", go)


def notion_read(query: str) -> ToolResult:
    def go():
        found = [o for o in _notion("POST", "/search", {"query": (query or "").strip()[:200], "page_size": 5,
                                                         "filter": {"property": "object", "value": "page"}}).get("results", [])]
        if not found:
            return ToolResult(f"No Notion page matches {query}.", ok=False)
        page = found[0]
        blocks = _notion("GET", f"/blocks/{page['id']}/children?page_size=100").get("results", [])
        lines = []
        for b in blocks:
            content = b.get(b.get("type", ""), {}) or {}
            text = "".join(t.get("plain_text", "") for t in content.get("rich_text", []) or [])
            if text:
                prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list_item": "- ",
                          "numbered_list_item": "1. ", "to_do": "[x] " if content.get("checked") else "[ ] "}.get(b["type"], "")
                lines.append(prefix + text)
        return ToolResult(f"The Notion page {_title(page)} is on screen.",
                          screen=f"{_title(page)}\n{page.get('url', '')}\n\n" + "\n".join(lines)[:READ_LIMIT])
    return _run("notion", go)


p.add(Tool("gmail_search", "Search the user's Gmail (Gmail search words, e.g. from:college newer_than:7d is:unread) and list the emails.",
           schema({"query": {"type": "string"}, "limit": {"type": "integer"}}), "read", gmail_search))
p.add(Tool("gmail_read", "Open one email from the user's Gmail that matches the search words and show its text.",
           schema({"query": {"type": "string"}}, required=("query",)), "read", gmail_read))
p.add(Tool("gmail_draft", "Write an email as a Gmail draft for the user to check and send (never sends).",
           schema({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, required=("to", "body")),
           "write", gmail_draft, summarize=lambda a: f"save a Gmail draft to {a.get('to', '?')}: {a.get('subject', '')}"))
p.add(Tool("calendar_upcoming", "List the user's upcoming Google Calendar events for the next few days.",
           schema({"days": {"type": "integer"}}), "read", calendar_upcoming))
p.add(Tool("calendar_add", "Add an event to the user's Google Calendar.",
           schema({"title": {"type": "string"}, "when": {"type": "string", "description": "e.g. tomorrow at 6 pm"},
                   "minutes": {"type": "integer"}}, required=("title", "when")),
           "write", calendar_add, summarize=lambda a: f"add \"{a.get('title', '')}\" to Google Calendar ({a.get('when', '')})"))
p.add(Tool("drive_search", "Find files in the user's Google Drive by words in them.",
           schema({"query": {"type": "string"}}, required=("query",)), "read", drive_search))
p.add(Tool("drive_read", "Read a Google Doc or text file from the user's Google Drive.",
           schema({"query": {"type": "string"}}, required=("query",)), "read", drive_read))
p.add(Tool("notion_search", "Search the user's Notion pages and databases.",
           schema({"query": {"type": "string"}}, required=("query",)), "read", notion_search))
p.add(Tool("notion_read", "Read one of the user's Notion pages.",
           schema({"query": {"type": "string"}}, required=("query",)), "read", notion_read))


def _after(words: tuple):
    def args(t: str, m) -> dict:
        low = t.lower()
        for w in words:
            if w in low:
                return {"query": t[low.index(w) + len(w):].strip(" ?.")}
        return {"query": t}
    return args


p.mock_rule(2.5, ("my emails", "my email", "my inbox", "gmail", "any emails"), "gmail_search", lambda t, m: {"query": "newer_than:2d"})
p.mock_rule(2.6, ("google calendar",), "calendar_upcoming", lambda t, m: {"days": 7})
p.mock_rule(2.7, ("in drive", "google drive", "from drive"), "drive_search", _after(("find", "search")))
p.mock_rule(2.8, ("in notion", "notion"), "notion_search", _after(("find", "search")))


# ---------------------------------------------------------------- the Connectors page

@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Request
    from pydantic import BaseModel

    from airtac.plugin_api import local_only

    class Switch(BaseModel):
        on: bool

    @router.get("")
    def listing():
        return {"connectors": status()}

    @router.post("/{cid}/switch")
    def switch(cid: str, body: Switch, request: Request):
        local_only(request)
        if cid not in {c["id"] for c in CONNECTORS}:
            raise HTTPException(404, "No such connector.")
        set_on(cid, body.on)
        return {"connectors": status()}

    @router.post("/{cid}/connect")
    def connect(cid: str, request: Request):
        """Google ones: opens Google's sign-in in your browser, asking only for this connector's permissions."""
        local_only(request)
        c = next((x for x in CONNECTORS if x["id"] == cid), None)
        if not c:
            raise HTTPException(404, "No such connector.")
        if c["kind"] != "google":
            return {"open": "#connections", "say": f"Paste the {c['name']} key on the APIs and keys page."}
        acc = _account()
        try:
            url = acc.start(c["features"])
        except acc.AccountError as e:
            raise HTTPException(409, str(e))
        set_on(cid, True)
        return {"auth_url": url, "say": "Finish in the browser window that opened; this page updates by itself."}
