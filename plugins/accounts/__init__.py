"""Accounts: LeetCode, CodeChef, GeeksforGeeks and AICTE-EduSkills in one place.

How "connecting" works:
- Sign up and Log in open the site's own page. On the PC they open in Google Chrome with AIRTAC's own
  profile (plugins/desktop/chrome.py), so once you log in there you stay logged in. You type the
  password on the real site: these sites use captchas and one-time codes, so AIRTAC doesn't type it.
- Login details (optional): you can keep each site's password and a session cookie in the vault
  (airtac/vault.py: Windows Credential Manager, not AIRTAC's folder). The password is there for you to
  look up on this PC. The session cookie lets AIRTAC read signed-in data (LeetCode: your streak) by
  sending it only to that site. Neither ever goes to the AI model, the chat, a log or a backup, and
  neither can be read from your phone. Using your own cookie for your own data is your call: a site's
  terms may not allow automated requests, so AIRTAC sends one request when you press Refresh.
- Your stats come from your public username alone:
    LeetCode       its public GraphQL endpoint (the same one leetcode.com uses), stable
    CodeChef       your public profile page, read best-effort (CodeChef closed its public API)
    GeeksforGeeks  your public profile page, read best-effort (no public API)
    EduSkills      no public API at all: your internships, modules and log kept in AIRTAC's EduSkills app
  "Best-effort" means: if the site changes its page, AIRTAC says it couldn't read it. It never guesses a number.
"""
import json
import re

from airtac import memory, vault
from airtac.plugin_api import CURRENT, SAMPLE_TAG, Tool, ToolResult, fetch, local_only, plugin, schema

p = plugin(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (AIRTAC personal assistant; reads one public profile)"}
USERNAME_OK = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")

SITES = {
    "leetcode": {"name": "LeetCode", "home": "https://leetcode.com",
                 "signup": "https://leetcode.com/accounts/signup/", "login": "https://leetcode.com/accounts/login/",
                 "profile": "https://leetcode.com/u/{u}/", "how": "Public LeetCode data (GraphQL)."},
    "codechef": {"name": "CodeChef", "home": "https://www.codechef.com",
                 "signup": "https://www.codechef.com/signup", "login": "https://www.codechef.com/login",
                 "profile": "https://www.codechef.com/users/{u}",
                 "how": "Read from your public profile page, best effort. CodeChef has no public API."},
    "gfg": {"name": "GeeksforGeeks", "home": "https://www.geeksforgeeks.org",
            "signup": "https://auth.geeksforgeeks.org/", "login": "https://auth.geeksforgeeks.org/",
            "profile": "https://www.geeksforgeeks.org/user/{u}/",
            "how": "Read from your public profile page, best effort. GeeksforGeeks has no public API."},
    "eduskills": {"name": "AICTE EduSkills", "home": "https://eduskillsfoundation.org",
                  "signup": "https://eduskillsfoundation.org/internshipreg/", "login": "https://eduskillsfoundation.org/login/",
                  "profile": "https://internship.eduskillsfoundation.org",
                  "how": "EduSkills has no public API. Stats are what you keep in the EduSkills app here."},
}
ALIASES = {"geeksforgeeks": "gfg", "geeks for geeks": "gfg", "gfg": "gfg", "code chef": "codechef",
           "codechef": "codechef", "leetcode": "leetcode", "leet code": "leetcode", "eduskills": "eduskills",
           "edu skills": "eduskills", "aicte": "eduskills"}


def site_id(text: str) -> str:
    low = (text or "").lower().strip()
    if low in SITES:
        return low
    return next((v for k, v in ALIASES.items() if k in low), "")


# ---------------------------------------------------------------- usernames (never passwords)

def username(site: str) -> str:
    """Saved on the Accounts page, else config [plugins.accounts] usernames, else (LeetCode) the old setting."""
    saved = memory.kv_get("accounts", f"user:{site}")
    if saved:
        return saved
    default = (p.setting("usernames", {}) or {}).get(site, "")
    if not default and site == "leetcode":
        from airtac import config
        default = config.plugin_settings("leetcode").get("user", "")
    return default


def set_username(site: str, name: str) -> str:
    """Saves a username. Returns "" when it worked, else what was wrong."""
    if site not in SITES:
        return "I don't know that site."
    name = (name or "").strip().lstrip("@")
    if name and not USERNAME_OK.match(name):
        return "A username has only letters, numbers, dots, dashes and underscores."
    memory.kv_set("accounts", f"user:{site}", name)
    return ""


def links(site: str) -> dict:
    s, u = SITES[site], username(site)
    return {"signup": s["signup"], "login": s["login"], "home": s["home"],
            "profile": s["profile"].format(u=u) if u and "{u}" in s["profile"] else (s["profile"] if "{u}" not in s["profile"] else "")}


# ---------------------------------------------------------------- reading public pages

def _text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _int(pattern: str, text: str):
    m = re.search(pattern, text, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None


def parse_codechef(html: str) -> dict | None:
    """Rating, stars, highest rating, problems solved, global rank from a CodeChef profile page."""
    if not html:
        return None
    text = _text(html)
    out = {"rating": _int(r'class="rating-number"[^>]*>\s*(\d+)', html),
           "stars": _int(r"(\d)\s*(?:★|&#9733;|&starf;)", html) or _int(r"(\d)\s*★", text),
           "highest": _int(r"Highest Rating\s*\(?\s*(\d+)", text),
           "solved": _int(r"Total Problems Solved\s*:?\s*(\d+)", text),
           "global_rank": _int(r"(\d[\d,]*)\s*Global Rank", text)}
    return out if any(v is not None for v in out.values()) else None


GFG_KEYS = {"score": ("score", "coding_score", "codingScore"),
            "solved": ("total_problems_solved", "totalProblemsSolved"),
            "institute_rank": ("institute_rank", "instituteRank"),
            "streak": ("pod_solved_longest_streak", "maxStreak", "longest_streak")}


def _find(obj, names):
    """First number under any of these key names, anywhere in a JSON tree."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names and isinstance(v, (int, float, str)) and str(v).replace(",", "").isdigit():
                return int(str(v).replace(",", ""))
        for v in obj.values():
            found = _find(v, names)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find(v, names)
            if found is not None:
                return found
    return None


def parse_gfg(html: str) -> dict | None:
    """Coding score, problems solved, institute rank, longest streak from a GeeksforGeeks profile page."""
    if not html:
        return None
    out = dict.fromkeys(GFG_KEYS)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            out = {k: _find(data, set(names)) for k, names in GFG_KEYS.items()}
        except ValueError:
            pass
    text = _text(html)
    fallbacks = {"score": r"Coding Score\s*:?\s*(\d+)", "solved": r"Problems? Solved\s*:?\s*(\d+)",
                 "institute_rank": r"Institute Rank\s*:?\s*(\d+)", "streak": r"(\d+)\s*Days?\s*Longest Streak"}
    for k, pat in fallbacks.items():
        if out[k] is None:
            out[k] = _int(pat, text)
    return out if any(v is not None for v in out.values()) else None


def _signed_in_headers(site: str) -> dict:
    """Your saved session cookie for this site, sent only to this site's own address."""
    cookie = vault.get("accounts", site, "cookie")
    if not cookie:
        return {}
    headers = {"Cookie": cookie}
    m = re.search(r"csrftoken=([^;\s]+)", cookie)
    if m:  # LeetCode wants its CSRF token repeated in a header
        headers["x-csrftoken"] = m.group(1)
    return headers


def leetcode_signed_in(headers: dict) -> dict:
    """Signed-in extras from LeetCode (your daily streak). {} when the cookie doesn't work."""
    query = "query { userStatus { isSignedIn username } streakCounter { streakCount currentDayCompleted } }"
    data = fetch("https://leetcode.com/graphql", method="POST", write=True,   # write=True: never cached
                 headers={"Content-Type": "application/json", "Referer": "https://leetcode.com", **HEADERS, **headers},
                 json_body={"query": query})
    d = (data or {}).get("data") or {}
    if not (d.get("userStatus") or {}).get("isSignedIn"):
        return {"signed_in": False}
    streak = d.get("streakCounter") or {}
    return {"signed_in": True, "streak": streak.get("streakCount"), "today_done": streak.get("currentDayCompleted")}


def live_stats(site: str) -> dict | None:
    """Live stats for one site, or None if the site couldn't be read. Never made up."""
    u = username(site)
    if not u and site != "eduskills":
        return None
    extra = _signed_in_headers(site) if site in ("leetcode", "codechef", "gfg") else {}
    if site == "leetcode":
        query = ("query($u: String!) { matchedUser(username: $u) { username profile { ranking } "
                 "submitStatsGlobal { acSubmissionNum { difficulty count } } } }")
        data = fetch("https://leetcode.com/graphql", method="POST",
                     headers={"Content-Type": "application/json", "Referer": "https://leetcode.com", **HEADERS},
                     json_body={"query": query, "variables": {"u": u}})
        user = ((data or {}).get("data") or {}).get("matchedUser")
        if not user:
            return None
        by = {s["difficulty"]: s["count"] for s in user["submitStatsGlobal"]["acSubmissionNum"]}
        out = {"username": user["username"], "solved": by.get("All", 0), "easy": by.get("Easy", 0),
               "medium": by.get("Medium", 0), "hard": by.get("Hard", 0), "ranking": user["profile"]["ranking"]}
        if extra:
            out.update(leetcode_signed_in(extra))
        return out
    if site == "codechef":
        got = parse_codechef(fetch(SITES["codechef"]["profile"].format(u=u), headers={**HEADERS, **extra},
                                   as_text=True) or "")
        return {"username": u, **got, **({"signed_in": True} if extra else {})} if got else None
    if site == "gfg":
        got = parse_gfg(fetch(SITES["gfg"]["profile"].format(u=u), headers={**HEADERS, **extra}, as_text=True) or "")
        return {"username": u, **got, **({"signed_in": True} if extra else {})} if got else None
    return eduskills_stats()


def eduskills_stats() -> dict | None:
    """From the EduSkills app's own records (EduSkills has no API)."""
    try:
        from plugins.eduskills import store
    except Exception:  # the EduSkills plugin is off
        return None
    rows = store.all_internships()
    if not rows:
        return {"internships": 0, "modules_done": 0, "modules_total": 0, "certificates": 0}
    return {"internships": len(rows),
            "modules_done": sum(r.get("modules_done") or 0 for r in rows),
            "modules_total": sum(r.get("modules_total") or 0 for r in rows),
            "certificates": sum(1 for r in rows if r.get("status") == "certificate" or r.get("certificate"))}


def refresh(ids=None) -> dict:
    """Read every site at once (one slow site doesn't hold up the others) and remember the results,
    so the Accounts page opens instantly next time with "last checked" numbers."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    ids = list(ids or SITES)

    def one(site):
        # fetch() checks permissions against the plugin that is running. Worker threads (and the
        # Refresh route) don't inherit that, so say it here: this is the accounts plugin.
        token = CURRENT.set(p.id)
        try:
            return live_stats(site)
        finally:
            CURRENT.reset(token)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {s: pool.submit(one, s) for s in ids}
        got = {s: f.result() for s, f in futures.items()}
    stamp = datetime.now().strftime("%d %b %H:%M")
    for s, v in got.items():
        if v is not None and s != "eduskills":
            memory.kv_set("accounts", f"stats:{s}", json.dumps({**v, "_checked": stamp}))
    return got


def last_stats(site: str) -> dict | None:
    raw = memory.kv_get("accounts", f"stats:{site}")
    try:
        v = json.loads(raw) if raw else None
    except ValueError:
        return None
    # Saved for a different username: not yours any more.
    return v if v and v.get("username", "").lower() == username(site).lower() else None


def line(site: str, s: dict | None) -> str:
    name = SITES[site]["name"]
    if s is None:
        u = username(site)
        return f"{name}: " + (f"couldn't read {u}'s profile right now." if u else "no username saved yet.")
    if site == "leetcode":
        extra = ""
        if s.get("signed_in") and s.get("streak") is not None:
            extra = f" Daily streak {s['streak']} day{'s' * (s['streak'] != 1)}" + \
                    (", today's done." if s.get("today_done") else ", today's not done yet.")
        elif s.get("signed_in") is False:
            extra = " Your saved session cookie has expired: log in again and save a new one."
        return (f"LeetCode ({s['username']}): {s['solved']} solved ({s['easy']} easy, {s['medium']} medium, "
                f"{s['hard']} hard), ranking {s['ranking']:,}.{extra}")
    if site == "codechef":
        bits = [f"rating {s['rating']}" if s.get("rating") else "",
                f"{s['stars']} star" if s.get("stars") else "",
                f"highest {s['highest']}" if s.get("highest") else "",
                f"{s['solved']} solved" if s.get("solved") is not None else "",
                f"global rank {s['global_rank']:,}" if s.get("global_rank") else ""]
        return f"CodeChef ({s['username']}): " + ", ".join(b for b in bits if b) + "."
    if site == "gfg":
        bits = [f"coding score {s['score']}" if s.get("score") is not None else "",
                f"{s['solved']} solved" if s.get("solved") is not None else "",
                f"institute rank {s['institute_rank']}" if s.get("institute_rank") else "",
                f"longest streak {s['streak']} days" if s.get("streak") else ""]
        return f"GeeksforGeeks ({s['username']}): " + ", ".join(b for b in bits if b) + "."
    if not s["internships"]:
        return "AICTE EduSkills: no internships added yet. Add one in the EduSkills app."
    return (f"AICTE EduSkills: {s['internships']} internship{'s' * (s['internships'] != 1)}, "
            f"{s['modules_done']} of {s['modules_total'] or '?'} modules done, {s['certificates']} certificate"
            f"{'s' * (s['certificates'] != 1)}.")


def _sample(site: str) -> dict | None:
    if site == "eduskills":
        return eduskills_stats()
    return dict(p.sample("accounts")[site])


def _stats_result(site: str, got: dict, tag: str = "") -> ToolResult:
    ids = list(got)
    lines = [line(s, got[s]) for s in ids]
    found = [s for s in ids if got[s] is not None and s != "eduskills"]
    if site:
        spoken = lines[0]
    elif found:
        spoken = "Here are your stats on " + ", ".join(SITES[s]["name"] for s in found) + ". Details are on screen."
    else:
        spoken = "I couldn't read any of your profiles. Check your usernames on the Accounts page."
    return ToolResult(spoken[:300], f"Your accounts {tag}".strip() + "\n" + "\n".join(lines),
                      data={"stats": got})


# ---------------------------------------------------------------- tools

def accounts_stats_fixture(site: str = "") -> ToolResult:
    sid = site_id(site)
    return _stats_result(sid, {s: _sample(s) for s in ([sid] if sid else SITES)}, SAMPLE_TAG)


def accounts_stats_live(site: str = "") -> ToolResult:
    sid = site_id(site)
    return _stats_result(sid, refresh([sid] if sid else None))


def accounts_link(site: str, action: str = "login") -> ToolResult:
    sid = site_id(site)
    if not sid:
        return ToolResult("Which site? I know LeetCode, CodeChef, GeeksforGeeks and AICTE EduSkills.", ok=False)
    action = action if action in ("signup", "login", "profile") else "login"
    url = links(sid)[action] or links(sid)["login"]
    word = {"signup": "sign-up", "login": "log-in", "profile": "profile"}[action]
    name = SITES[sid]["name"]
    return ToolResult(f"Here's the {name} {word} page. Tap the link on screen. You type your password on {name}'s own site, never here.",
                      f"{name} {word}: {url}", data={"links": [{"title": f"{name} {word}", "url": url}]})


def accounts_username(site: str, username: str) -> ToolResult:
    sid = site_id(site)
    if not sid:
        return ToolResult("Which site? I know LeetCode, CodeChef, GeeksforGeeks and AICTE EduSkills.", ok=False)
    problem = set_username(sid, username)
    if problem:
        return ToolResult(problem, ok=False)
    name = username.strip().lstrip("@")
    return ToolResult(f"Saved your {SITES[sid]['name']} username.", f"{SITES[sid]['name']} username: {name}\n"
                      f"Profile: {links(sid)['profile']}")


SITE_ARG = {"type": "string", "description": "leetcode, codechef, gfg (GeeksforGeeks) or eduskills; empty for all"}
p.add(Tool("accounts_stats",
           "Get the user's stats on LeetCode, CodeChef, GeeksforGeeks and AICTE EduSkills together (or one of them).",
           schema({"site": SITE_ARG}), "read", accounts_stats_fixture, accounts_stats_live))
p.add(Tool("accounts_link", "Give the official sign-up, log-in or profile link for LeetCode, CodeChef, GeeksforGeeks or EduSkills.",
           schema({"site": SITE_ARG, "action": {"type": "string", "enum": ["signup", "login", "profile"]}}, ("site",)),
           "read", accounts_link))
p.add(Tool("accounts_username", "Save the user's username (never a password) for one of those sites.",
           schema({"site": SITE_ARG, "username": {"type": "string"}}, ("site", "username")),
           "local", accounts_username))


# ---------------------------------------------------------------- app page and routes

def card(sid: str, stats: dict | None, sample: bool) -> dict:
    s = SITES[sid]
    text = line(sid, stats).split(": ", 1)[-1] if stats is not None or sid == "eduskills" else (
        "Press Refresh to read your public profile." if username(sid) else "Add your username to see your stats.")
    return {"id": sid, "name": s["name"], "username": username(sid), "how": s["how"], "links": links(sid),
            "line": text, "checked": (stats or {}).get("_checked", ""), "sample": sample and sid != "eduskills",
            "needs_username": sid != "eduskills", "vault": vault.summary("accounts", sid)}


def cards(live: bool) -> list[dict]:
    """Opening the page never waits on the internet: live mode shows the last numbers read (Refresh reads again)."""
    from airtac import config
    if config.OFFLINE:            # the offline tests: recorded profiles
        return [card(sid, _sample(sid), True) for sid in SITES]
    return [card(sid, eduskills_stats() if sid == "eduskills" else last_stats(sid), False) for sid in SITES]


@p.dashboard
def page(pg: dict) -> None:
    from airtac.tools import is_live
    ok, why = vault.available()
    pg["accounts"] = {"live": is_live(p.tools[0]), "cards": cards(is_live(p.tools[0])),
                      "vault": {"ok": ok, "why": "" if ok else why}}
    pg["lists"] = [{"title": "How connecting works", "sample": False, "empty": "", "items": [
        {"title": "Sign up and Log in open the real site", "meta": "On this PC they open in AIRTAC's Chrome profile, so you stay logged in."},
        {"title": "Login details stay in Windows' password store", "meta": "Never sent to the AI, the chat or a backup, and only shown on this PC."},
        {"title": "Stats need only your public username", "meta": "LeetCode by its public data; CodeChef and GeeksforGeeks from your public profile page."},
        {"title": "EduSkills has no public API", "meta": "Keep your internships in the EduSkills app; they show up here."}]}]


@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Request
    from pydantic import BaseModel, Field

    class Name(BaseModel):
        site: str = Field(max_length=40)
        username: str = Field(default="", max_length=80)

    @router.post("/username")
    def save(body: Name):
        problem = set_username(body.site, body.username)
        if problem:
            raise HTTPException(400, problem)
        return {"ok": True, "card": card(body.site, last_stats(body.site), False)}

    class Secret(BaseModel):
        site: str = Field(max_length=40)
        password: str | None = Field(default=None, max_length=2000)
        cookie: str | None = Field(default=None, max_length=16000)

    class Which(BaseModel):
        site: str = Field(max_length=40)
        field: str = Field(default="", max_length=20)

    def _site_or_400(site: str) -> str:
        if site not in SITES:
            raise HTTPException(400, "I don't know that site.")
        return site

    @router.post("/vault")
    def vault_save(body: Secret, request: Request):
        """Save or replace login details. Only on this PC. An empty field leaves what's saved alone."""
        local_only(request)
        site = _site_or_400(body.site)
        try:
            for field in ("password", "cookie"):
                value = getattr(body, field)
                if value:
                    vault.save("accounts", site, field, value)
        except vault.VaultUnavailable as e:
            raise HTTPException(503, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "vault": vault.summary("accounts", site)}

    @router.post("/vault/reveal")
    def vault_reveal(body: Which, request: Request):
        local_only(request)
        site = _site_or_400(body.site)
        if body.field not in ("password", "cookie"):
            raise HTTPException(400, "Which detail?")
        return {"value": vault.get("accounts", site, body.field)}

    @router.post("/vault/delete")
    def vault_delete(body: Which, request: Request):
        local_only(request)
        site = _site_or_400(body.site)
        vault.delete("accounts", site, body.field or None)
        return {"ok": True, "vault": vault.summary("accounts", site)}

    @router.post("/refresh")
    def again():
        from airtac.tools import is_live
        if not is_live(p.tools[0]):
            return {"live": False, "cards": cards(False)}
        got = refresh()
        return {"live": True, "cards": [card(sid, got[sid] if got[sid] is not None else last_stats(sid), False)
                                        for sid in SITES]}


# ---------------------------------------------------------------- rules for the no-model mode

def _site(text, _m):
    return {"site": site_id(text)}


def _link_args(text, _m):
    low = text.lower()
    action = "signup" if re.search(r"sign ?up|register|create .*account", low) else \
             "profile" if "profile page" in low else "login"
    return {"site": site_id(text), "action": action}


def _user_args(text, _m):
    m = re.search(r"username (?:to|is|=)?\s*@?([A-Za-z0-9_.\-]+)", text, re.IGNORECASE)
    return {"site": site_id(text), "username": m.group(1) if m else ""}


p.mock_rule(18.5, ("username to", "username is", "my username"), "accounts_username", _user_args)
p.mock_rule(18.6, ("sign up", "signup", "log in", "login", "sign in", "register on", "profile page"), "accounts_link", _link_args)
p.mock_rule(18.7, ("coding stats", "codechef", "geeksforgeeks", "gfg", "my accounts", "all my stats"), "accounts_stats", _site)
