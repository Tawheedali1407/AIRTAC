"""Checks AIRTAC Advance's real connections, one at a time, and says plainly what works.
    .venv\\Scripts\\python.exe scripts\\check_live.py
A  Ollama is running and the brain (Nemotron) is downloaded
B  the brain answers and calls a tool
C  web search (DuckDuckGo, then Wikipedia) and the reader
D  LeetCode, CodeChef and GeeksforGeeks public profiles (usernames from the Accounts page)
E  GitHub with your token (only if it's connected)
F  Nemotron 3 Super on Nebius (only if it's connected)
G  every other connection that has a key (the same Test as the Connections page)
Nothing here changes anything: every call only reads.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AIRTAC_NO_SCHEDULER", "1")

from airtac import brains, config, connections, plugins, tools, websearch  # noqa: E402
from airtac.llm import OpenAICompatLLM  # noqa: E402

plugins.load_all()
connections.load_saved()   # keys saved on the Connections page
results = []


def check(letter: str, name: str, fn) -> None:
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    results.append(ok)
    print(f"{letter}  {'OK  ' if ok else 'FAIL'}  {name}: {detail}")


def a():
    st = brains.status()
    if not st["ollama_up"]:
        return False, "Ollama isn't running. Open it from the Start menu."
    general = st["roles"][0]
    return bool(general["installed"]), f"{general['model']} {'is downloaded' if general['installed'] else 'is missing: ollama pull ' + general['model']}"


def b():
    llm = brains.get("general")
    names = tools.select_tools("What's today's LeetCode problem?", None)
    reply = llm.chat([{"role": "user", "content": "What's today's LeetCode problem?"}], tools.openai_schemas(names))
    if reply.tool_call:
        return reply.tool_call.name == "leetcode_daily", f"called {reply.tool_call.name}"
    return False, f"answered without a tool: {reply.text[:80]}"


def c():
    rows, provider = websearch.search("NVIDIA Nemotron", 3, ["ddg", "wikipedia"])
    if not rows:
        return False, "no search provider answered"
    page = websearch.read(rows[0]["url"])
    return True, f"{len(rows)} results from {provider}; read '{page['title'][:50]}' ({len(page['text'])} characters)"


def d():
    r = tools.run_tool("accounts_stats", {})
    return r.ok, r.spoken


def e():
    if not config.secret("GITHUB_TOKEN"):
        return True, "skipped (GitHub isn't connected)"
    r = tools.run_tool("github_summary", {})
    return r.ok and "Sample" not in r.screen, r.spoken


def f():
    key = config.secret("NEBIUS_API_KEY")
    if not key:
        return True, "skipped (Nebius isn't connected)"
    llm = OpenAICompatLLM(base_url=config.NEBIUS_BASE_URL, model=config.NEBIUS_MODEL, api_key=key)
    reply = llm.chat([{"role": "user", "content": "Say hello in five words."}], [], max_tokens=30)
    return bool(reply.text), reply.text[:80]


def g_checks():
    out = []
    for card in connections.all_connectors():
        if card["id"] in ("github", "nebius", "ollama") or card["status"] == "off" or not card["can_test"]:
            continue
        out.append((card["name"], lambda cid=card["id"]: tuple(connections.run_test(cid)[k] for k in ("ok", "message"))))
    return out


for letter, name, fn in (("A", "Brain downloaded", a), ("B", "Brain calls a tool", b), ("C", "Web search and reader", c),
                         ("D", "Coding profiles", d), ("E", "GitHub", e), ("F", "Cloud Nemotron", f)):
    check(letter, name, fn)
for name, fn in g_checks():
    check("G", name, fn)
print(f"\n{sum(results)}/{len(results)} checks passed.")
