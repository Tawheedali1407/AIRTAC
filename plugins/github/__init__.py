"""GitHub: today's activity, open issues, README, and creating an issue (asks you first).

Needs GITHUB_TOKEN in .env for your real data (a fine-grained token with read access to your
repos, plus Issues: write if you want AIRTAC to open issues). Without it: labelled sample data.
"""
import itertools
import re
from datetime import datetime, timezone

from airtac import connections
from airtac.plugin_api import SAMPLE_TAG, Tool, ToolResult, after, capitalize, fetch, plugin, schema, unreachable

p = plugin(__name__)

API = "https://api.github.com"
REPO_ARG = {"repo": {"type": "string", "description": "Repo name, e.g. airtac. Defaults to the user's main repo."}}


def _full_name(repo: str) -> str:
    repo = (repo or p.setting('default_repo', 'airtac')).strip()
    return repo if "/" in repo else f"{p.setting('user', 'Tawheedali1407')}/{repo}"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {p.secret('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AIRTAC",
    }


def _test_token(http) -> tuple[bool, str]:
    r = http.get(f"{API}/user", headers=_headers())
    if r.status_code == 401:
        return False, "GitHub refused the token. Make a new fine-grained token and paste it again."
    r.raise_for_status()
    login = r.json().get("login", "")
    want = p.setting("user", "")
    if want and login.lower() != want.lower():
        return True, f"Connected as {login}. [plugins.github] user in config/airtac.toml says {want}; change it if that's wrong."
    return True, f"Connected as {login}."


p.connector(name="GitHub", order=30, logo="github", get_key="https://github.com/settings/personal-access-tokens/new",
            about="Your repos, issues and today's activity. Opening an issue always waits for your Confirm.",
            powers=["GitHub app", "issues"], test=_test_token,
            note="Make a fine-grained token for your repos only: read access, plus Issues: write if you want issues opened.",
            fields=[connections.Field("GITHUB_TOKEN", "Token", placeholder="github_pat_...")], group="Search and dev tools")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# --- Shared formatting: fixture and live both build the same simple shapes,
# so the wording is written once.

def _summary_result(events: list[dict], notifications: int, tag: str = "") -> ToolResult:
    if not events:
        return ToolResult(f"No GitHub activity yet today. You have {_plural(notifications, 'unread notification')}.")
    repos = {e["repo"] for e in events}
    spoken = (f"Today on GitHub you have {_plural(len(events), 'event')} across {_plural(len(repos), 'repo')} "
              f"and {_plural(notifications, 'unread notification')}. Details are on screen.")
    lines = [f"GitHub today {tag}".strip()] + [f"- {e['repo']}: {e['text']}" for e in events]
    lines.append(f"Unread notifications: {notifications}")
    return ToolResult(spoken, "\n".join(lines))


def _issues_result(repo: str, issues: list[dict], tag: str = "") -> ToolResult:
    if not issues:
        return ToolResult(f"There are no open issues in {repo}.")
    newest = issues[0]
    spoken = (f"{repo} has {_plural(len(issues), 'open issue')}. The newest is number {newest['number']}: "
              f"{newest['title']}.")
    lines = [f"Open issues in {repo} {tag}".strip()]
    for i in issues:
        labels = f" [{', '.join(i['labels'])}]" if i.get("labels") else ""
        lines.append(f"#{i['number']} {i['title']}{labels} (opened {i['created']})")
    return ToolResult(spoken, "\n".join(lines))


# --- github_summary

def github_summary_fixture() -> ToolResult:
    data = p.sample("github")
    return _summary_result(data["events_today"], data["notifications"], SAMPLE_TAG)


def today_events_live() -> tuple[list[dict] | None, int]:
    """Today's events as simple {repo, text} dicts (None if GitHub didn't answer), and unread notifications."""
    events = fetch(f"{API}/users/{p.setting('user', 'Tawheedali1407')}/events", headers=_headers(), params={"per_page": 30})
    notes = fetch(f"{API}/notifications", headers=_headers())
    if events is None:
        return None, 0
    return _simplify(events), len(notes or [])


def github_summary_live() -> ToolResult:
    simple, notifications = today_events_live()
    if simple is None:
        return unreachable("GitHub")
    return _summary_result(simple, notifications)


def _simplify(events: list[dict]) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    simple = []
    for e in events:
        if not e.get("created_at", "").startswith(today):
            continue
        kind = e["type"].replace("Event", "")
        if kind == "Push":
            text = f"pushed {len(e['payload'].get('commits', []))} commit(s)"
        elif kind == "Issues":
            text = f"{e['payload'].get('action')} issue #{e['payload']['issue']['number']}: {e['payload']['issue']['title']}"
        else:
            text = kind.lower()
        simple.append({"repo": e["repo"]["name"].split("/")[-1], "text": text})
    return simple


# --- list_issues

def list_issues_fixture(repo: str = "") -> ToolResult:
    name = (repo or p.setting('default_repo', 'airtac')).split("/")[-1]
    repo_data = p.sample("github")["repos"].get(name)
    if repo_data is None:
        return ToolResult(f"I don't have a repo called {name} in the sample data.", f"{SAMPLE_TAG} Repos: airtac, dsa-practice")
    return _issues_result(name, repo_data["open_issues"], SAMPLE_TAG)


def list_issues_live(repo: str = "") -> ToolResult:
    full = _full_name(repo)
    data = fetch(f"{API}/repos/{full}/issues", headers=_headers(), params={"state": "open", "per_page": 10})
    if data is None:
        return unreachable("GitHub")
    issues = [
        {"number": i["number"], "title": i["title"], "labels": [lb["name"] for lb in i["labels"]],
         "created": i["created_at"][:10]}
        for i in data if "pull_request" not in i  # the issues API also returns PRs
    ]
    return _issues_result(full.split("/")[-1], issues)


# --- repo_readme

def repo_readme_fixture(repo: str = "") -> ToolResult:
    name = (repo or p.setting('default_repo', 'airtac')).split("/")[-1]
    repo_data = p.sample("github")["repos"].get(name)
    if repo_data is None:
        return ToolResult(f"I don't have a repo called {name} in the sample data.")
    return ToolResult(f"{name}: {repo_data['description']}. The README is on screen.",
                      f"{SAMPLE_TAG}\n{repo_data['readme']}")


def repo_readme_live(repo: str = "") -> ToolResult:
    full = _full_name(repo)
    text = fetch(f"{API}/repos/{full}/readme", headers={**_headers(), "Accept": "application/vnd.github.raw+json"},
                 as_text=True)
    if text is None:
        return unreachable("GitHub")
    return ToolResult(f"Here is the README for {full.split('/')[-1]}. It's on screen.", text)


# --- create_issue (write: the agent asks for confirmation before this runs)

_fake_numbers = itertools.count(101)


def create_issue_fixture(title: str, repo: str = "", body: str = "") -> ToolResult:
    number = next(_fake_numbers)
    name = (repo or p.setting('default_repo', 'airtac')).split("/")[-1]
    return ToolResult(
        f"Simulated: I would have created issue number {number}, {title}, in {name}. No real issue was made.",
        f"SIMULATED (no GitHub token, nothing was sent)\nRepo: {name}\nIssue #{number}: {title}\n\n{body}",
    )


def create_issue_live(title: str, repo: str = "", body: str = "") -> ToolResult:
    full = _full_name(repo)
    data = fetch(f"{API}/repos/{full}/issues", method="POST", headers=_headers(),
                 json_body={"title": title, "body": body}, write=True)
    if data is None:
        # Not "failed": the write may have gone through before the timeout.
        return ToolResult("I couldn't confirm the issue was created. Please check GitHub before trying again.", ok=False)
    return ToolResult(f"Created issue number {data['number']} in {full.split('/')[-1]}.",
                      f"Issue #{data['number']}: {title}\n{data['html_url']}")


def _describe_issue(args: dict) -> str:
    return f"create an issue titled \"{args.get('title', '')}\" in {(args.get('repo') or p.setting('default_repo', 'airtac'))}"


p.add(Tool("github_summary", "Summarize the user's GitHub activity today: pushes, issues and unread notifications.",
              schema(), "read", github_summary_fixture, github_summary_live, "GITHUB_TOKEN"))
p.add(Tool("list_issues", "List the open issues in one of the user's GitHub repos.",
              schema(REPO_ARG), "read", list_issues_fixture, list_issues_live, "GITHUB_TOKEN"))
p.add(Tool("repo_readme", "Read the README file of one of the user's GitHub repos.",
              schema(REPO_ARG), "read", repo_readme_fixture, repo_readme_live, "GITHUB_TOKEN"))
p.add(Tool("create_issue", "Create a new issue in one of the user's GitHub repos (asks the user to confirm first).",
              schema({**REPO_ARG,
                      "title": {"type": "string", "description": "Short issue title"},
                      "body": {"type": "string", "description": "Issue details"}}, required=("title",)),
              "write", create_issue_fixture, create_issue_live, "GITHUB_TOKEN", summarize=_describe_issue))


# ---------------------------------------------------------------- app page

@p.dashboard
def page(pg: dict) -> None:
    from airtac import config
    from airtac.tools import is_live
    if config.OFFLINE:            # the offline tests: the recorded day
        data = p.sample("github")
        events, notes = data["events_today"], data["notifications"]
    elif not is_live(p.tools[0]):
        pg["note"] = "Connect GitHub on the Connections page to see your repos, issues and today's activity."
        return
    else:
        events, notes = today_events_live()
        if events is None:
            pg["note"] = "GitHub didn't answer. Try again in a minute."
            return
    pg["stats"] = [{"label": "Events today", "value": len(events)},
                   {"label": "Active repos", "value": len({e["repo"] for e in events})},
                   {"label": "Unread notifications", "value": notes}]
    pg["lists"] = [{"title": "Today", "items": [{"title": e["text"], "meta": e["repo"]} for e in events],
                    "empty": "No activity yet today."}]


# ---------------------------------------------------------------- rules for the no-model mode

def _repo(text: str) -> str:
    m = re.search(r"\b(?:in|on|for|of)\s+(?:my\s+|the\s+)?([\w.-]+)\s+repo", text, re.IGNORECASE)
    return m.group(1) if m else p.setting("default_repo", "airtac")


def _issue_args(text, _messages):
    title = text.split(":", 1)[1] if ":" in text else after(text, r"\bissue\b(\s+(called|titled|to|for|about))?")
    return {"repo": _repo(text), "title": capitalize(title.strip(" .?!")) or "New issue",
            "body": "Created by AIRTAC from a voice request."}


p.mock_rule(15, ("create an issue", "create issue", "new issue", "file an issue", "open an issue", "raise an issue",
                 "add an issue"), "create_issue", _issue_args)
p.mock_rule(16, ("issues",), "list_issues", lambda t, m: {"repo": _repo(t)})
p.mock_rule(17, ("readme",), "repo_readme", lambda t, m: {"repo": _repo(t)})
p.mock_rule(18, ("github",), "github_summary")
