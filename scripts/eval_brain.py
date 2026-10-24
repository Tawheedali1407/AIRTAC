"""Does the brain pick the right tool? A repeatable score for your README, your interviews and
for choosing between models (Nemotron Nano vs Qwen vs the cloud Nemotron Super).

Each prompt has the tool a person would expect. The script routes it exactly like the app does
(the brain for that kind of question, the tools the router offers), asks the model once, and does
NOT run the tool, so nothing changes anywhere.
    .venv\\Scripts\\python.exe scripts\\eval_brain.py                 # the brain in config/airtac.toml
    .venv\\Scripts\\python.exe scripts\\eval_brain.py qwen3.5:4b      # try another Ollama model
Results go to outputs\\evals\\tool-choice-<model>-<date>.json.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AIRTAC_NO_SCHEDULER", "1")

from airtac import brains, config, plugins  # noqa: E402
from airtac.llm import system_prompt  # noqa: E402
from airtac.tools import openai_schemas, select_tools  # noqa: E402

CASES = [
    ("What's on my GitHub today?", "github_summary"),
    ("Show the open issues in my airtac repo", "list_issues"),
    ("Create an issue: add dark mode", "create_issue"),
    ("What's today's LeetCode problem?", "leetcode_daily"),
    ("How many problems have I solved on LeetCode?", "leetcode_profile"),
    ("Give me a medium graph problem to practise", "leetcode_pick"),
    ("Solve two sum in Python", "solve_problem"),
    ("Explain my last solution step by step", "explain_solution"),
    ("What's trending on Hacker News?", "hn_top"),
    ("Find recent papers on retrieval augmented generation", "arxiv_search"),
    ("When is the next Codeforces contest?", "cf_contests"),
    ("Remember that my DBMS exam is on 14 Nov", "memory_save"),
    ("What did I tell you about my exam?", "memory_recall"),
    ("Remind me to pay the exam fee tomorrow at 6 pm", "reminder"),
    ("Make 5 MCQs from unit 2 of data structures", "make_questions"),
    ("Draw a binary search tree with 8 4 12 2 6", "make_image"),
    ("Add GSoC proposal draft on 1 March to my schedule", "schedule_add"),
    ("What's on my schedule this week?", "schedule_list"),
    ("Brief me on my day", "daily_briefing"),
    ("Search the web for Nebius Token Factory pricing", "web_search"),
    ("Summarise this page https://fastapi.tiangolo.com/", "read_page"),
    ("In work mode, research free GPU options for students and write me a PDF", "start_work"),
    ("Search my library for the cardiac cycle", "knowledge_search"),
    ("Make slides about two pointers", "make_slides"),
    ("Make a spreadsheet of my monthly budget", "make_spreadsheet"),
    ("How much battery is left?", "system_status"),
    ("Add my EduSkills Cybersecurity internship starting 1 Dec", "eduskills_add"),
    ("Write my EduSkills internship report", "eduskills_report"),
    ("What are my CodeChef and GeeksforGeeks stats?", "accounts_stats"),
    ("Back up my data", "backup_now"),
]


def run(model: str = "") -> dict:
    plugins.load_all()
    if model:
        config.LLM_MODEL = model
        brains._cache.clear()
    rows = []
    for prompt, expected in CASES:
        role = brains.route(prompt)
        llm = brains.get(role)
        names = select_tools(prompt, None) if brains.uses_tools(role) else []
        start = time.monotonic()
        try:
            reply = llm.chat([{"role": "system", "content": system_prompt([])}, {"role": "user", "content": prompt}],
                             openai_schemas(names))
            got = reply.tool_call.name if reply.tool_call else "(answered without a tool)"
        except Exception as e:
            got = f"(error: {type(e).__name__}: {e})"[:120]
        rows.append({"prompt": prompt, "expected": expected, "got": got, "ok": got == expected, "role": role,
                     "offered": expected in names, "seconds": round(time.monotonic() - start, 1)})
    right = sum(r["ok"] for r in rows)
    brain = "keyword rules" if config.LLM_PROVIDER == "mock" else f"{config.LLM_PROVIDER}:{config.LLM_MODEL}"
    return {"brain": brain, "right": right, "total": len(rows), "rows": rows, "at": datetime.now().isoformat(timespec="seconds")}


def main() -> int:
    result = run(sys.argv[1] if len(sys.argv) > 1 else "")
    for r in result["rows"]:
        note = "" if r["offered"] else "  (router didn't offer it)"
        print(f"{'ok  ' if r['ok'] else 'MISS'} {r['seconds']:5.1f}s {r['prompt'][:50]:50} -> {r['got']}{note}")
    print(f"\n{result['brain']}: {result['right']}/{result['total']} tools picked correctly")
    out = config.OUTPUTS_DIR / "evals"
    out.mkdir(parents=True, exist_ok=True)
    tag = result["brain"].replace(":", "-").replace("/", "-")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", tag).strip("-")  # "nemotron-3-nano:4b" is not a valid Windows file name
    path = out / f"tool-choice-{safe}-{datetime.now():%Y%m%d-%H%M}.json"
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
