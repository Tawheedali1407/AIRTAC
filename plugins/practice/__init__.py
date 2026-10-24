"""Practice: solve a coding problem you paste, explain a solution step by step, and make a PDF of
what you solved. Contest problems are refused (contests are for solving yourself).

Problem statements belong to their sites: full statements stay on this laptop (data/ and
workspace/ are never shared); shareable PDFs hold only titles, links, two-line summaries,
the approach and the code. Every solution is labelled AI-generated: test it before you submit.
"""
import json
import logging
import re
from datetime import datetime

from airtac import config, pdfgen, safety
from airtac.llm import ask_json, ask_text
from airtac.plugin_api import SAMPLE_TAG, Tool, ToolResult, after, plugin, schema, unreachable

from . import store

plug = plugin(__name__)  # not `p`: the code below uses p for a problem
plug.sql(store.SCHEMA)
log = logging.getLogger("airtac.practice")
AI_LABEL = "AI-generated"

# --- solve_problem

SITES = ("leetcode", "codechef", "geeksforgeeks")
SITE_ALIASES = {"gfg": "geeksforgeeks", "geeks for geeks": "geeksforgeeks", "geeks": "geeksforgeeks",
                "lc": "leetcode", "cc": "codechef"}
EXTENSIONS = {"python": "py", "c++": "cpp", "cpp": "cpp", "java": "java", "c": "c", "javascript": "js"}


def normalize_site(site: str) -> str:
    site = (site or "leetcode").lower().strip()
    site = SITE_ALIASES.get(site, site)
    return site if site in SITES else "leetcode"


def normalize_language(language: str) -> str:
    language = (language or "python").lower().strip()
    return "c++" if language == "cpp" else language


def slugify(text: str, n: int = 50) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:n].strip("-") or "problem"


def _title_from(problem_text: str) -> str:
    first = problem_text.strip().splitlines()[0] if problem_text.strip() else "Untitled problem"
    return first[:80]


def _store(p: dict) -> str:
    """Save the code under workspace/solutions/<site>/ and a row in the problems table.
    Returns where it went, for the screen text. Demo mode stores nothing."""
    folder = config.WORKSPACE_DIR / "solutions" / p["site"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slugify(p['title'])}.{EXTENSIONS.get(p['language'], 'txt')}"
    path.write_text(f"# {p['title']} ({p['site']})\n# AI-generated. Test before submitting.\n\n{p['code']}\n",
                    encoding="utf-8")
    pid = store.save_problem(**p)
    return f"Saved to workspace/solutions/{p['site']}/{path.name} (problem #{pid})"


def _solution_result(p: dict, note: str = "") -> ToolResult:
    where = _store(p)
    screen = (f"{note}{p['title']} ({p['site']}) · AI-generated\n\nSummary: {p['summary']}\n\n"
              f"Approach: {p['approach']}\n\n{p['code']}\n\nComplexity: {p['complexity']}\n{where}")
    first_sentence = p["approach"].split(". ")[0].rstrip(".") + "."
    return ToolResult(f"{first_sentence} {p['complexity']} The code is on screen.", screen)


def _canned(text: str) -> dict | None:
    """Mock mode: find a sample solution whose title or key appears in the text."""
    for p in plug.sample("problems")["problems"]:
        if p["title"].lower() in text or any(k in text for k in p.get("keys", [])):
            return p
    for key, sol in plug.sample("leetcode")["solutions"].items():
        if key in text:
            return {"title": key.title(), "site": "leetcode", "summary": "", **sol, "language": "python"}
    return None


def solve_problem_fixture(problem_text: str, language: str = "python", site: str = "leetcode",
                          title: str = "", url: str = "") -> ToolResult:
    if safety.is_contest_link(problem_text) or safety.is_contest_link(url):
        return ToolResult(safety.CONTEST_REFUSAL)
    language, site = normalize_language(language), normalize_site(site)
    sample = _canned(f"{title} {problem_text}".lower())
    if sample:
        note = f"{SAMPLE_TAG} Canned solution" + (" (only Python samples exist in mock mode)" if language != "python" else "")
        p = {"site": sample.get("site", site), "title": title or sample["title"], "url": url or sample.get("url", ""),
             "difficulty": sample.get("difficulty", ""), "tags": ", ".join(sample.get("tags", [])),
             "language": "python", "statement": problem_text, "summary": sample.get("summary", ""),
             "approach": sample["approach"], "code": sample["code"], "complexity": sample["complexity"]}
        return _solution_result(p, note + ".\n\n")
    p = {"site": site, "title": title or _title_from(problem_text), "url": url, "difficulty": "", "tags": "",
         "language": language, "statement": problem_text,
         "summary": "No summary in mock mode.",
         "approach": "Mock mode only has canned answers for a few sample problems. "
                     "Add a Nebius key and Nemotron will solve new problems.",
         "code": "def solve(*args):\n    # TODO: write the solution here\n    pass",
         "complexity": "Unknown until solved."}
    return _solution_result(p, f"{SAMPLE_TAG} No canned solution for this problem.\n\n")


SOLVER_PROMPT = (
    "You are a coding tutor helping a student practise. Reply in exactly this format and nothing else:\n"
    "TITLE: the problem's short name\n"
    "SUMMARY: two lines describing the problem in your own words. Never copy the statement.\n"
    "APPROACH: two sentences.\n"
    "CODE:\n<code only, no explanation>\n"
    "COMPLEXITY: one line with time and space.")

_SOLVER_RE = re.compile(r"TITLE:(.*?)SUMMARY:(.*?)APPROACH:(.*?)CODE:(.*?)COMPLEXITY:(.*)", re.S)


def solve_problem_live(problem_text: str, language: str = "python", site: str = "leetcode",
                       title: str = "", url: str = "") -> ToolResult:
    if safety.is_contest_link(problem_text) or safety.is_contest_link(url):
        return ToolResult(safety.CONTEST_REFUSAL)
    language, site = normalize_language(language), normalize_site(site)
    try:
        text = ask_text(SOLVER_PROMPT, f"Language: {language}\n\n{problem_text[:6000]}", role="coding", max_tokens=1400)
    except Exception:
        return unreachable("the model")
    m = _SOLVER_RE.search(text)
    if not m:
        return ToolResult("Here's my solution. It's on screen, but I couldn't save it in the usual format.", text)
    code = re.sub(r"^```\w*\n?|```$", "", m.group(4).strip()).strip()
    p = {"site": site, "title": title or m.group(1).strip()[:80] or _title_from(problem_text), "url": url,
         "difficulty": "", "tags": "", "language": language, "statement": problem_text,
         "summary": m.group(2).strip(), "approach": m.group(3).strip(), "code": code,
         "complexity": m.group(5).strip()}
    return _solution_result(p)



# ---------------------------------------------------------------- explain_solution

def _demo_problem(title: str = "") -> dict | None:
    for p in plug.sample("problems")["problems"]:
        if not title or p["title"].lower() == title.lower():
            return p
    return None


def _steps_result(problem: dict, steps: dict, tag: str = "") -> ToolResult:
    items = steps.get("steps", [])
    lines = [f"{problem['title']}: step by step ({AI_LABEL}) {tag}".strip()]
    for n, s in enumerate(items, 1):
        lines.append(f"\n{n}. {s.get('title', '')}\n   {s.get('explanation', '')}")
        if s.get("small_example"):
            lines.append(f"   Example: {s['small_example']}")
    if steps.get("edge_cases"):
        lines.append("\nEdge cases: " + "; ".join(map(str, steps["edge_cases"])))
    if steps.get("complexity"):
        lines.append(f"Complexity: {steps['complexity']}")
    first = items[0].get("title", "") if items else ""
    spoken = f"Here's {problem['title']} in {len(items)} steps. Step one: {first}. Details are on screen."
    return ToolResult(spoken, "\n".join(lines))


def _simple_steps(problem: dict) -> dict:
    """Mock mode without a sample: split the stored approach into steps."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", problem.get("approach", "")) if s.strip()]
    # Title = the sentence's first few words, so the steps read like headings.
    steps = [{"title": " ".join(s.rstrip(".").split()[:5]), "explanation": s, "small_example": ""} for s in sentences]
    return {"steps": steps or [{"title": "Read the problem", "explanation": "No approach saved yet.", "small_example": ""}],
            "edge_cases": [], "complexity": problem.get("complexity", "")}


def explain_solution_local(problem_id: int = 0) -> ToolResult:
    """Without a brain: split your solved problem's own approach into steps (nothing made up)."""
    problem = store.get_problem(problem_id or None)
    if problem is None:
        return ToolResult(f"I can't find problem number {problem_id}." if problem_id
                          else "Solve or paste a problem first, then ask me to explain it.")
    steps = _simple_steps(problem)
    store.set_problem_steps(problem["id"], json.dumps(steps))
    return _steps_result(problem, steps)


def explain_solution_fixture(problem_id: int = 0) -> ToolResult:
    problem = store.get_problem(problem_id or None)
    if problem is None:
        if problem_id:
            return ToolResult(f"I can't find problem number {problem_id}.")
        problem = _demo_problem()  # nothing solved yet: use the sample
        return _steps_result(problem, problem["steps"], SAMPLE_TAG)
    sample = _demo_problem(problem["title"])
    steps = sample["steps"] if sample and sample.get("steps") else _simple_steps(problem)
    store.set_problem_steps(problem["id"], json.dumps(steps))
    return _steps_result(problem, steps, SAMPLE_TAG if sample else "")


EXPLAIN_PROMPT = (
    "Explain this solution to a second-year student. Reply with JSON only, no markdown, in this shape: "
    '{"steps": [{"title": "...", "explanation": "...", "small_example": "..."}], '
    '"edge_cases": ["..."], "complexity": "..."}. Use 3 to 6 steps.')


def explain_solution_live(problem_id: int = 0) -> ToolResult:
    problem = store.get_problem(problem_id or None)
    if problem is None:
        return ToolResult("Solve a problem first, then ask me to explain it.")
    data = ask_json(EXPLAIN_PROMPT, role="coding", content=f"Problem: {problem['title']}\nSummary: {problem['summary']}\n"
                                    f"Approach: {problem['approach']}\nCode ({problem['language']}):\n{problem['code']}")
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        return unreachable("the model in the right format")
    store.set_problem_steps(problem["id"], json.dumps(data))
    return _steps_result(problem, data)



# ---------------------------------------------------------------- make_solutions_pdf

def make_solutions_pdf(site: str = "all", since: str = "today", tag: str = "", mode: str = "personal") -> ToolResult:
    site = (site or "all").lower()
    since = since if since in ("today", "week", "all") else "today"
    mode = mode if mode in ("personal", "shareable") else "personal"
    note = ""
    problems = store.list_problems(None if site == "all" else site, since, tag or None)
    if not problems:
        when = {"today": "today", "week": "this week", "all": "yet"}[since]
        return ToolResult(f"I don't have any solved problems {when}. Solve one first, then ask again.")
    sites = sorted({p["site"] for p in problems})
    path = plug.output_dir() / f"solutions_{site}_{datetime.now():%Y%m%d-%H%M%S}.pdf"
    pdfgen.render(pdfgen.solutions_sections(problems, mode), path, pdfgen.SOLUTIONS_FOOTER)
    plural = "problem" if len(problems) == 1 else "problems"
    return ToolResult(f"I made a {mode} PDF with {len(problems)} {plural} from {', '.join(pdfgen.site_name(s) for s in sites)}. "
                      "It's in your files list.",
                      f"Saved {path.name} {note}\nMode: {mode}" +
                      (" (includes statements: keep it private)" if mode == "personal" else " (no statements)"))



plug.add(Tool("solve_problem", "Explain an approach and write code for a practice coding problem the user gives, and save it; refuses contest problems.",
              schema({"problem_text": {"type": "string", "description": "The problem statement or its name"},
                      "language": {"type": "string", "enum": ["python", "c++", "java", "c"], "description": "Default python"},
                      "site": {"type": "string", "enum": list(SITES), "description": "Where the problem is from"},
                      "title": {"type": "string"}, "url": {"type": "string"}},
                     required=("problem_text",)),
              "local", solve_problem_fixture, solve_problem_live, ("brain",)))
plug.add(Tool("explain_solution", "Explain a saved solution step by step with small examples (newest one by default).",
              schema({"problem_id": {"type": "integer", "description": "Problem number; 0 means the newest"}}),
              "local", explain_solution_fixture, explain_solution_live, ("brain",), local=explain_solution_local))
plug.add(Tool("make_solutions_pdf", "Build a PDF of the coding problems the user solved, grouped by site.",
              schema({"site": {"type": "string", "enum": ["all", "leetcode", "codechef", "geeksforgeeks"]},
                      "since": {"type": "string", "enum": ["today", "week", "all"]},
                      "tag": {"type": "string"},
                      "mode": {"type": "string", "enum": ["personal", "shareable"],
                               "description": "personal includes statements and stays private"}}),
              "local", make_solutions_pdf))


# ---------------------------------------------------------------- app page and Home numbers

@plug.dashboard
def page(pg: dict) -> None:
    rows = store.list_problems()
    names = {"leetcode": "LeetCode", "codechef": "CodeChef", "geeksforgeeks": "GeeksforGeeks"}
    by_site = {s: sum(1 for r in rows if r["site"] == s) for s in names}
    explained = sum(1 for r in rows if r.get("steps_json") not in ("", "{}", None))
    pdfs = len(list(plug.output_dir().glob("solutions_*.pdf")))
    pg["stats"] = [{"label": "Problems solved", "value": len(rows)}, {"label": "Explained step by step", "value": explained},
                   {"label": "Solution PDFs", "value": pdfs}]
    pg["bars"] = {"title": "Problems by site", "items": [{"label": names[s], "value": n} for s, n in by_site.items()]}
    pg["lists"] = [{"title": "Recent problems", "empty": "Nothing solved yet. Paste a practice problem to start.",
                    "items": [{"title": r["title"], "meta": f"{names.get(r['site'], r['site'])}, {r.get('language') or 'python'}",
                               "url": r.get("url") or None} for r in reversed(rows[-8:])]}]


@plug.progress
def numbers() -> dict:
    return {"order": 30, "counts": [{"label": "Solved with AIRTAC", "value": len(store.list_problems())}]}


# ---------------------------------------------------------------- rules for the no-model mode

LANGS = ("python", "javascript", "java", "c++", "cpp", "c")
PASTED_RE = re.compile(r"\[Pasted problem \| site: (\w+) \| language: ([\w+]+)\]\n(.*)", re.S)


def _language_in(text: str) -> str:
    low = text.lower()
    return next((lg for lg in LANGS if re.search(rf"(?<![\w+]){re.escape(lg)}(?![\w+])", low)), "")


def _solve_args(text, _messages):
    pasted = PASTED_RE.search(text)
    if pasted:  # the "Paste problem" button: the statement comes after the header
        said = _language_in(text[: pasted.start()])
        return {"problem_text": pasted.group(3).strip(), "site": pasted.group(1), "language": said or pasted.group(2)}
    return {"problem_text": after(text, r"\bsolve\b") or text, "language": _language_in(text) or "python"}


def _solutions_pdf_args(text, _messages):
    low = text.lower()
    since = "week" if "week" in low else "all" if re.search(r"\ball\b", low) else "today"
    site = next((s for s in ("leetcode", "codechef", "geeksforgeeks") if s in low), "all")
    return {"site": site, "since": since, "mode": "shareable" if "shareable" in low or "share" in low else "personal"}


plug.mock_rule(4, ("explain the solution", "explain solution", "explain my solution", "explain it step by step",
                   "step by step"), "explain_solution", lambda t, m: {"problem_id": 0})
plug.mock_rule(5, ("solve", "solution for", "solution to"), "solve_problem", _solve_args)
plug.mock_rule(6, ("solutions pdf", "pdf of today's solutions", "pdf of my solutions", "pdf of the solutions",
                   "pdf of this week's solutions", "solutions as a pdf", "problems i solved"),
               "make_solutions_pdf", _solutions_pdf_args)
