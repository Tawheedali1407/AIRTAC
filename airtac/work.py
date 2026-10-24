"""Work mode: hand AIRTAC a job with several steps and get a file at the end.

    job = start("Research free GPU options for students")

1. Plan. The model writes a short plan (at most 6 steps) from the tools that fit the goal.
   Without a model (keyword mode), a fixed plan is picked from the words in the goal.
   A saved skill skips planning and reuses the steps that worked last time.
2. Work. One step at a time in a background thread, so you can close the page. Each step is a
   tool call and its result becomes a note. A step can open a link an earlier search found:
   {"url": "$result1"} means the first search result.
3. Write. The notes become a report, shown in Work mode. A PDF, Word or PowerPoint file is made only when
   you ask ("convert"): convert(job_id, "pdf"|"docx"|"pptx").

A step that changes something outside AIRTAC (risk "write" or "device") waits for your Confirm.
Stop ends a job before its next step. One job runs at a time: an i3 laptop has no spare cores.
When a job ends you get a notification, even if the page is closed.
"""
import json
import logging
import queue
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlparse

from airtac import brains, config, memory, pdfgen
from airtac.llm import brain_on
from airtac.tools import REGISTRY, run_and_log, select_tools

log = logging.getLogger("airtac.work")

MAX_STEPS = 6
JOB_SECONDS = 900           # a whole job (a laptop model is slow); past this the job ends
NOTES_FOR_MODEL = 5000      # characters of notes the model reads when writing: a 4B model on a laptop needs a small prompt
WRITE_TOKENS = 900          # the report's length; longer took minutes on an i3 and ran past the job's time
FORMATS = {"pdf": "PDF", "docx": "Word document", "pptx": "PowerPoint"}
CONFIRM_RISKS = ("write", "device")
NOT_IN_PLANS = ("start_work", "daily_briefing")
EXAMPLES = ["Research how AGI could change jobs for students like me",
            "Research free GPU options for students",
            "Find new papers on small language models and summarise them",
            "Look up how DNS works and make me revision notes",
            "Plan my week from my schedule and write it up"]

JOBS: dict[str, dict] = {}
_queue: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


class StepFailed(Exception):
    """A step couldn't finish. The message is a sentence for the user."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _domain(url: str) -> str:
    host = urlparse(url).hostname or url
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------- storage

def _save(job: dict) -> None:
    job["updated_at"] = _now()
    memory.save_job(job)


def _stale(job: dict) -> dict:
    """A job saved as running whose thread is gone (AIRTAC was closed) is shown as stopped."""
    if job["id"] not in JOBS and job["status"] in ("planning", "running"):
        job = {**job, "status": "stopped", "spoken": "AIRTAC was closed before this job finished."}
    return job


def get(job_id: str) -> dict | None:
    if job_id in JOBS:
        return JOBS[job_id]
    job = memory.get_job(job_id)
    if job:
        job = _stale(job)
        JOBS[job_id] = job
    return job


def public(job: dict) -> dict:
    """A job without its notes (they can be long), for the page."""
    return {k: v for k, v in job.items() if k != "notes"}


def recent(limit: int = 20) -> list[dict]:
    found = {j["id"]: _stale(j) for j in memory.list_jobs(limit)}
    found.update(JOBS)
    return [public(j) for j in sorted(found.values(), key=lambda j: j["created_at"], reverse=True)[:limit]]


# ---------------------------------------------------------------- planning

def _goal_topic(goal: str) -> str:
    """'Research free GPU options for students and write me a PDF' -> 'free GPU options for students'."""
    text = re.split(r"\b(and|then)\s+(write|make|create|give|put|turn|summari[sz]e)\b", goal, maxsplit=1, flags=re.I)[0]
    text = re.sub(r"^\s*(please\s+)?(research|find( out)?|look up|search( the web)?( for)?|learn about|study|"
                  r"read about|tell me about|compare|explain)\s+", "", text, flags=re.I)
    return text.strip(" .,:;!?") or goal.strip()[:80]


def template_plan(goal: str) -> tuple[str, list[dict]]:
    """A fixed plan from the words in the goal, for when there's no model."""
    low = goal.lower()
    topic = _goal_topic(goal)
    if re.search(r"\b(papers?|arxiv)\b", low) and "arxiv_search" in REGISTRY:
        steps = [("Find recent papers", "arxiv_search", {"query": topic})]
    elif re.search(r"\b(my week|schedule|deadlines|plan my)\b", low) and "schedule_list" in REGISTRY:
        steps = [("Read your schedule for the week", "schedule_list", {"period": "week"})]
    elif re.search(r"\b(my (books|notes|library))\b", low) and "knowledge_search" in REGISTRY:
        steps = [("Search your library", "knowledge_search", {"query": topic})]
    else:
        steps = [(f"Search the web for {topic}", "web_search", {"query": topic}),
                 ("Read the top result", "read_page", {"url": "$result1"}),
                 ("Read the second result", "read_page", {"url": "$result2"})]
    title = topic[:1].upper() + topic[1:80]
    return title, [{"title": t, "tool": tool, "args": args} for t, tool, args in steps if tool in REGISTRY]


def plan_tools(goal: str) -> list[str]:
    """The tools a plan may use: web search and reading, plus whatever fits the goal."""
    names = [n for n in ("web_search", "read_page") if n in REGISTRY]
    names += [n for n in select_tools(goal, None, 10) if n not in names and n not in NOT_IN_PLANS]
    return names[:12]


PLAN_PROMPT = """You plan small research and writing jobs for {name}'s assistant.
Goal: {goal}

Reply with JSON only, no other text, in this shape:
{{"title": "short title", "steps": [{{"title": "what this step does", "tool": "tool_name", "args": {{}}}}]}}
Rules: at most {max_steps} steps. Use only these tools:
{tools}
To open a link found by an earlier web_search step, use read_page with {{"url": "$result1"}} (or $result2, $result3).
Do not add a writing step: the report is written automatically at the end."""


def _parse_plan(text: str, allowed: list[str]) -> tuple[str, list[dict]] | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    steps = []
    for s in data.get("steps", [])[:MAX_STEPS] if isinstance(data, dict) else []:
        if isinstance(s, dict) and s.get("tool") in allowed:
            args = s.get("args") if isinstance(s.get("args"), dict) else {}
            steps.append({"title": str(s.get("title") or s["tool"])[:120], "tool": s["tool"], "args": args})
    if not steps:
        return None
    return str(data.get("title") or "")[:100], steps


def plan(goal: str, llm=None) -> tuple[str, list[dict]]:
    if llm is None and not brain_on():
        return template_plan(goal)
    allowed = plan_tools(goal)
    tools = "\n".join(f"- {n}: {REGISTRY[n].description} Arguments: {json.dumps(REGISTRY[n].parameters['properties'])}"
                      for n in allowed)
    prompt = PLAN_PROMPT.format(name=config.USER_NAME or "the user", goal=goal, max_steps=MAX_STEPS, tools=tools)
    try:
        reply = (llm or brains.get("general")).chat([{"role": "user", "content": prompt}], [], max_tokens=700)
        parsed = _parse_plan(reply.text, allowed)
    except Exception:
        log.exception("planning failed; using the fixed plan")
        parsed = None
    if not parsed:
        return template_plan(goal)
    title, steps = parsed
    return title or template_plan(goal)[0], steps


# ---------------------------------------------------------------- running a step

def _resolve(args: dict, links: list[str]) -> dict:
    out = {}
    for k, v in args.items():
        m = re.fullmatch(r"\$result(\d)", str(v).strip())
        if m:
            i = int(m.group(1)) - 1
            if i >= len(links):
                raise StepFailed("The search didn't find that many links.")
            v = links[i]
        out[k] = v
    return out


def _run_step(job: dict, step: dict) -> None:
    try:
        step["args"] = _resolve(step.get("args", {}), job["links"])
    except StepFailed:
        if step["tool"] != "read_page" or not job["notes"]:
            raise
        step["status"] = "skipped"         # fewer links than planned: the report uses what was found
        raise StepFailed("Skipped: the search found fewer links.")
    result = run_and_log(step["tool"], step["args"])
    data = result.data or {}
    if data.get("links") is not None:
        job["links"] = list(data["links"])
    page = data.get("page")
    if page:
        name = (page.get("title") or _domain(page["url"]))[:80]
        step["note"] = name
        job["notes"].append({"step": step["title"], "source": page["url"], "title": page.get("title", ""),
                             "text": page.get("text", "")[:4000]})
    else:
        step["note"] = result.spoken[:160]
        job["notes"].append({"step": step["title"], "source": "", "text": result.screen or result.spoken})
    if not result.ok:
        raise StepFailed(result.spoken)


# ---------------------------------------------------------------- writing the result

WRITE_PROMPT = """Write a short report for {name} on this goal: {goal}

Use only the notes below. Plain text: headings start with "## ", no other markdown.
Start with "## Summary" (3 to 5 sentences), then 2 to 4 sections, then "## Sources" listing
each source as [n] title, address. Cite sources in the text as [n]. If the notes don't answer
part of the goal, say so instead of guessing.

Notes:
{notes}"""


def _sources(job: dict) -> list[dict]:
    seen, out = set(), []
    for n in job["notes"]:
        if n.get("source") and n["source"] not in seen:
            seen.add(n["source"])
            out.append({"title": n.get("title") or _domain(n["source"]), "url": n["source"]})
    return out


def compose(job: dict, llm=None) -> tuple[str, bool]:
    """(report text, written by a model?)."""
    sources = _sources(job)
    if llm is not None or brain_on():
        notes = "\n\n".join(f"[{n['step']}]" + (f" source: {n['source']}" if n.get("source") else "") + f"\n{n['text']}"
                            for n in job["notes"])[:NOTES_FOR_MODEL]
        try:
            reply = (llm or brains.get("general")).chat([{"role": "user", "content": WRITE_PROMPT.format(
                name=config.USER_NAME or "the user", goal=job["goal"], notes=notes)}], [], max_tokens=WRITE_TOKENS)
            if reply.text and len(reply.text) > 80:
                return reply.text.strip(), True
        except Exception:
            log.exception("writing failed; putting the notes together instead")
    parts = ["## Summary", f"Goal: {job['goal']}",
             "AIRTAC made this without a language model, so it is the notes each step collected, not a "
             "written summary. Start Ollama (or set a model in config/airtac.toml) to get a written report."]
    for n in job["notes"]:
        parts += [f"## {n['step']}", n["text"][:2500]]
    if sources:
        parts.append("## Sources")
        parts += [f"[{i}] {s['title']}, {s['url']}" for i, s in enumerate(sources, 1)]
    return "\n".join(parts), False


def convert(job_id: str, fmt: str) -> str:
    """Turn a finished job's report into a file, only when you ask ("convert"). Returns its address."""
    job = get(job_id)
    if job is None or not job.get("report"):
        raise ValueError("That job has no report to convert yet.")
    if fmt not in FORMATS:
        raise ValueError("Convert to pdf, docx (Word) or pptx (PowerPoint).")
    if fmt == "pdf":
        url = _files(job, job["report"], job.get("by_model", True))
    else:
        from plugins.documents import make_document, make_slides
        maker = make_document if fmt == "docx" else make_slides
        result = maker(job["title"], job["report"].replace("## ", "# "))
        url = re.search(r"/files/\S+", result.screen).group(0)
    job.setdefault("files", {})[fmt] = url
    job["file"] = url
    _save(job)
    return url


def latest_report() -> dict | None:
    return next((j for j in recent(20) if j.get("status") == "done" and j.get("report")), None)


def _files(job: dict, text: str, by_model: bool) -> str:
    """Write the PDF and a Markdown copy to outputs/work/. Returns the PDF's address."""
    sections = [("title", job["title"]),
                ("meta", f"Made by AIRTAC Advance in Work mode on {datetime.now():%d %b %Y}"
                         + (" (written by AI: check the sources)" if by_model else " (notes, no model)")),
                ("meta", f"Goal: {job['goal']}"), ("rule",)]
    para: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if para:
                sections.append(("text", "\n".join(para)))
                para = []
            sections.append(("heading", line[3:].strip()))
        elif line.strip():
            para.append(line.rstrip())
    if para:
        sections.append(("text", "\n".join(para)))
    slug = re.sub(r"[^a-z0-9]+", "-", job["title"].lower()).strip("-")[:40] or "job"
    folder = config.OUTPUTS_DIR / "work"
    stem = f"{slug}-{datetime.now():%Y%m%d-%H%M%S}"
    pdfgen.render(sections, folder / f"{stem}.pdf")
    (folder / f"{stem}.md").write_text(f"# {job['title']}\n\nGoal: {job['goal']}\n\n{text}\n", encoding="utf-8")
    return f"/files/work/{stem}.pdf"


# ---------------------------------------------------------------- the job loop

def _finish(job: dict, status: str, spoken: str) -> None:
    job["status"], job["spoken"] = status, spoken
    _save(job)
    if status in ("done", "failed", "waiting"):
        memory.notify(f"Work mode: {job['title'][:80]}", spoken, spoken, "work")


def _work(job: dict, llm=None) -> None:
    deadline = time.monotonic() + JOB_SECONDS
    if job["status"] == "planning":
        job["title"], steps = plan(job["goal"], llm)
        job["steps"] = [{**s, "status": "todo", "note": ""} for s in steps]
        job["steps"].append({"title": "Write the report", "tool": "write", "args": {}, "status": "todo", "note": ""})
        job["status"] = "running"
        _save(job)
    for step in job["steps"]:
        if step["status"] in ("done", "failed", "skipped"):
            continue
        if job.get("stop"):
            return _finish(job, "stopped", "Stopped. The steps done so far are kept.")
        if time.monotonic() > deadline:
            return _finish(job, "failed", "That job took too long, so I stopped it.")
        if step["tool"] == "write":
            step["status"] = "running"
            _save(job)
            if not job["notes"]:
                step["status"] = "failed"
                return _finish(job, "failed", "None of the steps found anything to write about.")
            text, by_model = compose(job, llm)
            job["report"], job["by_model"] = text[:20000], by_model      # no file until you say "convert"
            step["status"], step["note"] = "done", "Report ready"
            return _finish(job, "done", f"Done: {job['title']}. The report is in Work mode. "
                                        "Say convert to PDF, Word or PowerPoint if you want a file.")
        tool = REGISTRY.get(step["tool"])
        if tool is None:
            step["status"], step["note"] = "skipped", "That tool isn't installed any more."
            continue
        if tool.risk in CONFIRM_RISKS and not step.get("approved"):
            step["status"] = "waiting"
            return _finish(job, "waiting", f"Step {job['steps'].index(step) + 1} wants to {step['title'].lower()}. "
                                           "Please confirm or skip it.")
        step["status"] = "running"
        _save(job)
        try:
            _run_step(job, step)
            step["status"] = "done"
        except StepFailed as e:
            step["status"], step["note"] = "failed", str(e)[:160]
        except Exception:
            log.exception("work step failed")
            step["status"], step["note"] = "failed", "Something went wrong in this step."
        _save(job)


def _worker_loop() -> None:
    while True:
        job_id = _queue.get()
        job = JOBS.get(job_id)
        if job and job["status"] in ("planning", "running"):
            try:
                _work(job)
            except Exception:
                log.exception("job %s failed", job_id)
                _finish(job, "failed", "Something went wrong, so I stopped this job.")
        _queue.task_done()


def _enqueue(job_id: str) -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="airtac-work", daemon=True)
            _worker.start()
    _queue.put(job_id)


def new_job(goal: str, steps: list[dict] | None = None, title: str = "") -> dict:
    goal = re.sub(r"\s+", " ", goal).strip()[:1500]
    job = {"id": uuid.uuid4().hex[:10], "goal": goal, "title": title or goal[:80], "status": "planning", "steps": [],
           "notes": [], "links": [], "file": "", "report": "", "spoken": "Planning...",
           "created_at": _now(), "updated_at": _now()}
    if steps:  # a saved skill: reuse its plan
        job["steps"] = [{"title": s["title"], "tool": s["tool"], "args": s.get("args", {}), "status": "todo", "note": ""}
                        for s in steps if s.get("tool") != "write"]
        job["steps"].append({"title": "Write the report", "tool": "write", "args": {}, "status": "todo", "note": ""})
        job["status"], job["spoken"] = "running", "Running a saved skill..."
    JOBS[job["id"]] = job
    _save(job)
    return job


def start(goal: str, steps: list[dict] | None = None, title: str = "") -> dict:
    job = new_job(goal, steps, title)
    _enqueue(job["id"])
    return job


def run_now(goal: str, llm=None) -> dict:
    """Plan and run a job in this thread (tests)."""
    job = new_job(goal)
    _work(job, llm)
    return job


def stop(job_id: str) -> bool:
    job = get(job_id)
    if not job or job["status"] not in ("planning", "running", "waiting"):
        return False
    job["stop"] = True
    if job["status"] == "waiting":
        _finish(job, "stopped", "Stopped. The steps done so far are kept.")
    return True


def confirm(job_id: str, approve: bool) -> dict | None:
    job = get(job_id)
    if not job or job["status"] != "waiting":
        return None
    step = next(s for s in job["steps"] if s["status"] == "waiting")
    if approve:
        step["approved"], step["status"] = True, "todo"
    else:
        step["status"], step["note"] = "skipped", "You skipped this step."
    job["status"] = "running"
    _save(job)
    _enqueue(job_id)
    return job


def save_skill(job_id: str, name: str = "") -> int | None:
    """Keep a finished job's plan as a skill you can run again in one click."""
    job = get(job_id)
    if not job or job["status"] != "done":
        return None
    steps = [{"title": s["title"], "tool": s["tool"], "args": s["args"]} for s in job["steps"]
             if s["tool"] != "write" and s["status"] == "done"]
    # Links found by a search step change every run, so read_page steps go back to "$resultN".
    n = 0
    for s in steps:
        if s["tool"] == "read_page":
            n += 1
            s["args"] = {"url": f"$result{n}"}
    return memory.save_skill(name or job["title"], job["goal"], steps)


def run_skill(skill_id: int) -> dict | None:
    skill = memory.get_skill(skill_id)
    if skill is None:
        return None
    memory.count_skill_run(skill_id)
    return start(skill["goal"], skill["steps"], skill["name"])
