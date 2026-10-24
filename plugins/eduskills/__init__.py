"""EduSkills: your AICTE-EduSkills virtual internships, from registration to certificate and report.

What it does:
  - keeps each internship you take (domain such as Cybersecurity or AI-ML, partner, cohort,
    dates, modules done, assessment score, certificate),
  - puts the start, the assessment and the end on your schedule, so you get reminders,
  - keeps a short learning log ("today: finished module 3 on IAM roles"),
  - writes the internship report your college asks for (a Word file), from your log,
  - gives the step-by-step checklist, and can read the official site for cohort news.

What it doesn't do: log in to the EduSkills portal for you (it has no public API, and your
password should stay with you). Dates come from you or the official dashboard, never guessed.
EduSkills internships are free: anyone asking for money is not EduSkills.
"""
import re
from datetime import date, timedelta

from airtac import dates, websearch
from airtac.llm import ask_text, brain_on
from airtac.plugin_api import Tool, ToolResult, after, capitalize, plugin, schema

from . import store

p = plugin(__name__)
p.sql(store.SCHEMA)

PORTAL = "https://eduskillsfoundation.org"
DASHBOARD = "https://internship.eduskillsfoundation.org"
DOMAINS = ("cybersecurity", "ai-ml", "ai ml", "artificial intelligence", "machine learning", "data science",
           "data analytics", "cloud", "aws", "google", "android", "flutter", "full stack", "web development", "iot",
           "embedded", "networking", "cisco", "juniper", "palo alto", "microchip", "salesforce", "electric vehicle",
           "ev", "blockchain", "devops", "zscaler", "fortinet", "alteryx", "ui ux")
STEPS = [
    ("Register", f"Make an account on the official site ({PORTAL}). Type the address yourself: "
                 "links shared in groups are sometimes fake."),
    ("Verify", "Confirm the codes sent to your phone and email. One account only: duplicates break certificate checks."),
    ("Enroll", "When a cohort opens, choose a domain on your dashboard. Registration can close fast, so check often."),
    ("Learn", "Finish every module and video; the assessment is based on them. Log what you learn each day here."),
    ("Assessment", "Take the final assessment before the deadline on your dashboard. Check the pass mark there."),
    ("Certificate", "Download the certificate (it has a QR code) and save it in your knowledge folder or files."),
    ("Report and credit", "Write the internship report for your college and ask your TPO how credits are given."),
]


def _when(text: str) -> str:
    if not text:
        return ""
    d, _t, _w = dates.find_when(text)
    if d is None:
        try:
            return date.fromisoformat(text.strip()).isoformat()
        except ValueError:
            return ""
    return d.isoformat()


def _schedule(i: dict) -> int:
    """Put the internship's dates on the schedule (category internship). Returns how many were added."""
    from plugins.schedule import store as sched
    existing = {(e["title"], e["date"]) for e in sched.list_events()}
    added = 0
    for key, label in (("start", "starts"), ("assessment", "final assessment"), ("end", "ends")):
        if i.get(key):
            title = f"EduSkills {i['domain']} {label}"
            if (title, i[key]) not in existing:
                sched.add_event(title, i[key], "", "internship", "Check the date on your EduSkills dashboard.", DASHBOARD)
                added += 1
    return added


def _line(i: dict) -> str:
    mods = f", modules {i['modules_done']}/{i['modules_total']}" if i["modules_total"] else ""
    score = f", score {i['score']:g}%" if i["score"] is not None else ""
    when = f", {i['start'] or '?'} to {i['end'] or '?'}" if i["start"] or i["end"] else ""
    return f"{i['domain']}{' (' + i['partner'] + ')' if i['partner'] else ''}: {i['status']}{mods}{score}{when}"


# ---------------------------------------------------------------- tools

def eduskills_add(domain: str, partner: str = "", cohort: str = "", start: str = "", end: str = "",
                  assessment: str = "", modules_total: int = 0) -> ToolResult:
    domain = capitalize((domain or "").strip())[:60]
    if not domain:
        return ToolResult("Which domain is it? For example Cybersecurity, AI-ML or Cloud.")
    i = {"domain": domain, "partner": partner.strip()[:40], "cohort": cohort.strip()[:20], "start": _when(start),
         "end": _when(end), "assessment": _when(assessment), "modules_total": int(modules_total or 0),
         "status": "learning" if _when(start) and _when(start) <= date.today().isoformat() else "registered"}
    i["id"] = store.add(**i)
    on_schedule = _schedule(i)
    spoken = f"Added your EduSkills {domain} internship."
    if on_schedule:
        spoken += f" {on_schedule} date{'s are' if on_schedule > 1 else ' is'} on your schedule with reminders."
    else:
        spoken += " Tell me the start and assessment dates from your dashboard and I'll remind you."
    return ToolResult(spoken, _line({**i, "modules_done": 0, "score": None}))


def eduskills_update(domain: str = "", modules_done: int | None = None, modules_total: int | None = None,
                     status: str = "", score: float | None = None, assessment: str = "", end: str = "") -> ToolResult:
    i = store.find(domain)
    if i is None:
        return ToolResult("You don't have an EduSkills internship saved yet. Say: add my EduSkills cybersecurity internship.")
    status = status.lower().strip()
    changes = {"modules_done": modules_done, "modules_total": modules_total, "score": score,
               "status": status if status in store.STATUSES else None,
               "assessment": _when(assessment) or None, "end": _when(end) or None}
    if score is not None and not changes["status"]:
        changes["status"] = "passed"
    if modules_done and not changes["status"] and i["status"] == "registered":
        changes["status"] = "learning"
    store.update(i["id"], **changes)
    i = {**i, **{k: v for k, v in changes.items() if v is not None}}
    _schedule(i)
    left = (i["modules_total"] or 0) - (i["modules_done"] or 0)
    spoken = f"Updated {i['domain']}: {i['status']}."
    if i["modules_total"] and left > 0:
        spoken += f" {left} module{'s' if left != 1 else ''} to go."
    return ToolResult(spoken, _line(i))


def eduskills_status() -> ToolResult:
    rows = store.all_internships()
    if not rows:
        return ToolResult("No EduSkills internships saved yet. When you enroll, say: add my EduSkills internship in "
                          "Cybersecurity, starting 1 Dec.", _guide_text())
    active = [r for r in rows if r["status"] not in ("certificate", "dropped")]
    today = date.today().isoformat()
    upcoming = sorted((r[k], f"{r['domain']} {k}") for r in active for k in ("assessment", "end") if r[k] and r[k] >= today)
    spoken = f"You have {len(active)} EduSkills internship{'s' if len(active) != 1 else ''} in progress."
    if upcoming:
        d, what = upcoming[0]
        spoken += f" Next: {what} {dates.spoken_day(date.fromisoformat(d))}."
    done = [r for r in rows if r["status"] == "certificate"]
    if done:
        spoken += f" {len(done)} finished with a certificate."
    return ToolResult(spoken, "Your EduSkills internships\n" + "\n".join(f"- {_line(r)}" for r in rows))


def eduskills_log(text: str, domain: str = "") -> ToolResult:
    i = store.find(domain)
    if i is None:
        return ToolResult("Add the internship first, then I can keep its learning log.")
    text = (text or "").strip()
    if len(text) < 3:
        return ToolResult("What did you learn or finish today?")
    store.add_log(i["id"], date.today().isoformat(), text)
    n = len(store.log(i["id"]))
    return ToolResult(f"Logged it for {i['domain']}. That's {n} entr{'ies' if n != 1 else 'y'} for your report.",
                      f"{date.today():%d %b}: {text}")


def _guide_text() -> str:
    return "EduSkills: from registration to certificate\n" + "\n".join(
        f"{n}. {title}: {text}" for n, (title, text) in enumerate(STEPS, 1)) + \
        f"\n\nOfficial site: {PORTAL}\nDashboard: {DASHBOARD}\nIt's free. Never pay anyone for it."


def eduskills_guide() -> ToolResult:
    return ToolResult("Seven steps: register on the official site, verify, enroll in a cohort, finish the modules, "
                      "pass the assessment, download the certificate, then write your report. Details are on screen.",
                      _guide_text())


REPORT_PROMPT = (
    "Write the body of a B.Tech virtual internship report. Use only the facts given; where something is missing, "
    "leave a short [fill in: ...] note instead of inventing it. Plain text, headings start with '## ': "
    "Abstract, About the internship, What I learned (one section per main topic from the log), "
    "Tools and technologies, Challenges and how I solved them, Conclusion.")


def eduskills_report(domain: str = "") -> ToolResult:
    i = store.find(domain)
    if i is None:
        return ToolResult("Add the internship first, then I can write its report.")
    entries = store.log(i["id"])
    facts = (f"Student: {p.setting('student', '') or '[fill in: your name]'}\n"
             f"College: {p.setting('college', '') or '[fill in: college]'}\nDomain: {i['domain']}\n"
             f"Partner: {i['partner'] or '[fill in]'}\nCohort: {i['cohort'] or '[fill in]'}\n"
             f"Dates: {i['start'] or '?'} to {i['end'] or '?'}\nScore: {i['score'] if i['score'] is not None else '[fill in]'}\n"
             "Learning log:\n" + "\n".join(f"- {e['day']}: {e['text']}" for e in entries))
    if brain_on():
        try:
            body = ask_text(REPORT_PROMPT, facts, max_tokens=1800)
        except Exception:
            body = ""
    else:
        body = ""
    if len(body) < 200:  # no model, or it failed: a clean template with your log in it
        body = "\n".join([
            "## Abstract", f"[fill in: three or four lines on the {i['domain']} internship and what you gained]",
            "## About the internship",
            f"An AICTE-EduSkills virtual internship in {i['domain']}" + (f" with {i['partner']}" if i["partner"] else "")
            + (f", cohort {i['cohort']}" if i["cohort"] else "") + f", from {i['start'] or '[start]'} to {i['end'] or '[end]'}.",
            "## Weekly learning log", *([f"- {e['day']}: {e['text']}" for e in entries] or ["- [fill in: add log entries]"]),
            "## Tools and technologies", "- [fill in]",
            "## Challenges and how I solved them", "[fill in]",
            "## Conclusion", "[fill in]"])
    from plugins.documents import make_document
    result = make_document(f"{i['domain']} internship report", body)
    result.spoken = (f"Your {i['domain']} internship report is ready as a Word file in your files. "
                     + ("Check the parts marked fill in." if "[fill in" in body else "Read it through before you submit."))
    return result


def eduskills_news() -> ToolResult:
    """Best effort: read the official site and pick out lines about cohorts and registration."""
    from airtac import config
    if config.OFFLINE:
        return ToolResult("I'm in offline mode, so I can't check the EduSkills site. Open it yourself when you're online.",
                          _guide_text())
    try:
        page = websearch.read(PORTAL)
    except Exception:
        return ToolResult("I couldn't reach the EduSkills site just now. Try again later.", ok=False)
    lines = [x for x in page["text"].splitlines() if re.search(r"cohort|registration|register|internship", x, re.I)]
    lines = [x.lstrip("#- ").strip() for x in lines if 15 < len(x) < 220][:8]
    if not lines:
        return ToolResult("The EduSkills site didn't mention a cohort on its front page. Check your dashboard.",
                          f"{PORTAL}\n{DASHBOARD}")
    return ToolResult(f"The EduSkills site says: {lines[0]}", "From the official site:\n" + "\n".join(f"- {x}" for x in lines)
                      + f"\n\n{PORTAL}")


p.add(Tool("eduskills_add", "Save an AICTE-EduSkills virtual internship the user enrolled in (domain, partner, cohort, "
           "dates) and put its dates on the schedule.",
           schema({"domain": {"type": "string", "description": "e.g. Cybersecurity, AI-ML, Cloud"},
                   "partner": {"type": "string", "description": "e.g. Palo Alto, Google, AWS"},
                   "cohort": {"type": "string"}, "start": {"type": "string", "description": "Start date in the user's words"},
                   "end": {"type": "string"}, "assessment": {"type": "string", "description": "Final assessment date"},
                   "modules_total": {"type": "integer"}}, required=("domain",)),
           "local", eduskills_add))
p.add(Tool("eduskills_update", "Update an EduSkills internship: modules done, assessment score, status or dates.",
           schema({"domain": {"type": "string"}, "modules_done": {"type": "integer"}, "modules_total": {"type": "integer"},
                   "status": {"type": "string", "enum": list(store.STATUSES)}, "score": {"type": "number"},
                   "assessment": {"type": "string"}, "end": {"type": "string"}}),
           "local", eduskills_update))
p.add(Tool("eduskills_status", "Say how the user's EduSkills internships are going and what's due next.",
           schema(), "local", eduskills_status))
p.add(Tool("eduskills_log", "Add today's learning to an EduSkills internship's log (used for the internship report).",
           schema({"text": {"type": "string"}, "domain": {"type": "string"}}, required=("text",)),
           "local", eduskills_log))
p.add(Tool("eduskills_guide", "Explain the EduSkills internship steps: register, verify, enroll, modules, assessment, "
           "certificate, report.", schema(), "local", eduskills_guide))
p.add(Tool("eduskills_report", "Write the college internship report (Word file) for an EduSkills internship from its log.",
           schema({"domain": {"type": "string"}}), "local", eduskills_report))
p.add(Tool("eduskills_news", "Check the official EduSkills website for cohort and registration news.",
           schema(), "read", eduskills_news))


# ---------------------------------------------------------------- app page, Home numbers, briefing

@p.dashboard
def page(pg: dict) -> None:
    rows = store.all_internships()
    pg["stats"] = [{"label": "In progress", "value": sum(r["status"] not in ("certificate", "dropped") for r in rows)},
                   {"label": "Certificates", "value": sum(r["status"] == "certificate" for r in rows)},
                   {"label": "Log entries", "value": sum(len(store.log(r["id"])) for r in rows)}]
    with_modules = [r for r in rows if r["modules_total"]]
    if with_modules:
        pg["bars"] = {"title": "Modules done", "kind": "progress",
                      "items": [{"label": r["domain"], "value": r["modules_done"], "total": r["modules_total"]} for r in with_modules]}
    pg["lists"] = [{"title": "Your internships", "empty": "None yet. Say: add my EduSkills Cybersecurity internship starting 1 Dec.",
                    "items": [{"title": r["domain"] + (f" ({r['partner']})" if r["partner"] else ""), "meta": _line(r).split(": ", 1)[1]}
                              for r in rows]},
                   {"title": "Steps", "items": [{"title": t, "meta": x} for t, x in STEPS]},
                   {"title": "Official links (never pay anyone)", "items": [{"title": "EduSkills Foundation", "url": PORTAL},
                                                                           {"title": "Internship dashboard", "url": DASHBOARD}]}]


@p.progress
def numbers() -> dict:
    rows = store.all_internships()
    return {"order": 45, "counts": [{"label": "EduSkills certificates", "value": sum(r["status"] == "certificate" for r in rows)}]} \
        if rows else {}


@p.briefing
def your_internships(now) -> dict | None:
    """One line in your day when an assessment or end date is within a week."""
    today = now.date()
    soon = []
    for r in store.all_internships():
        if r["status"] in ("certificate", "dropped", "passed"):
            continue
        for key, word in (("assessment", "assessment"), ("end", "internship ends")):
            if r[key]:
                d = date.fromisoformat(r[key])
                if today <= d <= today + timedelta(days=7):
                    soon.append((d, f"EduSkills {r['domain']} {word}"))
    if not soon:
        return None
    d, what = min(soon)
    return {"order": 20, "screen": f"{what} {dates.nice(d, today=today)}.", "spoken": f"{what} {dates.spoken_day(d, today)}."}


# ---------------------------------------------------------------- rules for the no-model mode

def _domain_in(text: str) -> str:
    low = text.lower()
    return next((d for d in DOMAINS if re.search(rf"\b{re.escape(d)}\b", low)), "")


def _add_args(text, _m):
    words = re.findall(r"\b(?:starting|from|starts)\s+(.+?)(?:\s+(?:to|till|until|ending)\s+(.+))?$", text, re.I)
    start, end = (words[0] if words else ("", ""))
    return {"domain": capitalize(_domain_in(text)) or "Virtual internship", "start": start, "end": end}


def _update_args(text, _m):
    low = text.lower()
    m = re.search(r"\b(\d+)\s*(?:of|/|out of)\s*(\d+)\s*modules?", low) or re.search(r"\bmodules?\s*(\d+)\s*(?:of|/)\s*(\d+)", low)
    score = re.search(r"\b(?:scored|score|got)\s*(\d+(?:\.\d+)?)", low)
    status = "certificate" if "certificate" in low else ""
    return {"domain": _domain_in(text), "modules_done": int(m.group(1)) if m else None,
            "modules_total": int(m.group(2)) if m else None, "score": float(score.group(1)) if score else None,
            "status": status}


p.mock_rule(2.6, ("internship report", "eduskills report"), "eduskills_report", lambda t, m: {"domain": _domain_in(t)})
p.mock_rule(2.61, ("eduskills log", "log for eduskills", "today i learned", "today i finished"), "eduskills_log",
            lambda t, m: {"text": after(t, r"\b(eduskills log|log for eduskills|today i learned|today i finished)\b") or t,
                          "domain": _domain_in(t)})
p.mock_rule(2.62, ("add my eduskills", "enrolled in eduskills", "joined eduskills", "new eduskills"), "eduskills_add", _add_args)
p.mock_rule(2.63, ("eduskills modules", "eduskills score", "eduskills certificate", "modules of"),
            "eduskills_update", _update_args)
p.mock_rule(2.64, ("eduskills news", "eduskills cohort", "eduskills registration"), "eduskills_news")
p.mock_rule(2.65, ("how does eduskills", "eduskills steps", "eduskills guide", "how to get eduskills"), "eduskills_guide")
p.mock_rule(2.66, ("eduskills",), "eduskills_status")
