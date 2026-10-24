"""The core: plugins load, every tool works offline within the voice limits, permissions hold,
the tool router and the no-model rules pick the right tool."""
import pytest

from airtac import agent, config, plugin_api, plugins, tools
from airtac.models import ToolResult
from airtac.plugin_api import NotAllowed, fetch

ON = {"schedule", "web", "knowledge", "memory", "study", "practice", "leetcode", "documents", "github", "news", "agentreach",
      "devtools",
      "diagrams", "system", "desktop", "music", "backup", "coach", "eduskills", "accounts", "research", "tokens",
      "perplexity", "passwords", "settings", "recall", "agentreach", "devtools", "finder", "attachments", "apikeys", "learn", "lectures", "daily", "account", "repos", "ccplugins", "weather", "sections", "crm", "dictation", "connectors", "reverse"}
PLANNED = {"phone", "telegram", "wakeword", "vision", "home", "email", "youtube", "social", "imagegen"}


def test_plugins_load_and_planned_ones_wait():
    states = {s["id"]: s["state"] for s in plugins.STATUS.values()}
    assert {k for k, v in states.items() if v == "on"} == ON
    assert {k for k, v in states.items() if v == "planned"} == PLANNED
    for s in plugins.STATUS.values():
        if s["state"] == "planned":
            assert s["plan"]["steps"], s["id"]


def test_every_tool_has_a_valid_risk_and_a_unique_name():
    names = [t.name for p in plugins.enabled() for t in p.tools]
    assert len(names) == len(set(names)) == len(tools.REGISTRY)
    assert all(t.risk in plugin_api.RISKS for t in tools.REGISTRY.values())
    # Anything that changes things outside AIRTAC is write or device, so it waits for Confirm.
    for name in ("create_issue", "open_app", "open_website", "backup_upload"):
        assert tools.REGISTRY[name].risk in agent.CONFIRM_RISKS


SAMPLE_ARGS = {
    "schedule_add": {"title": "DBMS exam", "when": "14 Nov"}, "schedule_done": {"what": "DBMS"},
    "web_search": {"query": "Nebius"}, "read_page": {"url": "https://fastapi.tiangolo.com/"},
    "start_work": {"goal": "Research free GPU options for students"},
    "knowledge_search": {"query": "binary search"},
    "memory_save": {"text": "my exam is on 12 Nov"}, "memory_recall": {"query": "exam"},
    "reminder": {"text": "pay the fee", "when": "tomorrow at 6 pm"},
    "make_pdf": {"title": "Plan", "body": "Day 1: graphs.\n" * 100},
    "set_syllabus": {"subject": "DS", "text": "Unit 1: Arrays, Recursion\nUnit 2: Stacks, Queues"},
    "make_questions": {"unit": "Unit 2", "count": 3},
    "solve_problem": {"problem_text": "two sum"}, "leetcode_pick": {"topic": "dp", "difficulty": "Medium"},
    "make_document": {"title": "Notes", "body": "## Intro\n- one\n- two\nText."},
    "make_slides": {"title": "Pointers", "slides": "## What\n- a\n## Why\n- b"},
    "make_spreadsheet": {"title": "Budget", "rows": "Item,Amount\nRent,5000\nFood,3000"},
    "list_issues": {"repo": "airtac"}, "repo_readme": {"repo": "airtac"},
    "create_issue": {"title": "Add dark mode", "repo": "airtac"},
    "arxiv_search": {"query": "agents"}, "make_image": {"prompt": "binary tree 8 4 12"},
    "open_app": {"name": "something not allowed"}, "open_website": {"name": "nowhere"},
}


@pytest.mark.parametrize("name", sorted(tools.REGISTRY))
def test_every_tool_runs_offline_within_the_voice_limits(name, monkeypatch, tmp_path):
    monkeypatch.setitem(config.RAW.setdefault("plugins", {}), "music", {"folder": str(tmp_path / "no-music")})
    result = tools.run_tool(name, SAMPLE_ARGS.get(name, {}))
    assert isinstance(result, ToolResult)
    assert 0 < len(result.spoken) <= 300, result.spoken
    assert len(result.screen) <= 1500
    assert "http" not in result.spoken and "**" not in result.spoken


def test_the_router_offers_the_matching_plugins_first():
    names = tools.select_tools("search the web for free gpus", None, 12)
    assert names[0] in ("web_search", "read_page", "start_work") and "web_search" in names
    assert len(names) <= 12
    in_app = tools.select_tools("anything", "leetcode", 12)
    assert in_app[:3] == ["leetcode_daily", "leetcode_profile", "leetcode_pick"]
    assert "memory_save" in in_app  # "always" plugins come along


ROUTES = [
    ("Brief me on my day", "daily_briefing"),
    ("Add DBMS exam on 14 Nov to my schedule", "schedule_add"),
    ("What's on my schedule this week?", "schedule_list"),
    ("Search the web for Nebius Token Factory", "web_search"),
    ("Read https://fastapi.tiangolo.com/", "read_page"),
    ("In work mode, research free GPU options", "start_work"),
    ("Search my library for big O", "knowledge_search"),
    ("Remember my exam is on 12 Nov", "memory_save"),
    ("Remind me to pay the fee tomorrow at 6 pm", "reminder"),
    ("Make 5 mcq questions from unit 2", "make_questions"),
    ("Explain the solution step by step", "explain_solution"),
    ("Make slides about two pointers", "make_slides"),
    ("Make a spreadsheet of my budget", "make_spreadsheet"),
    ("What's on my GitHub today?", "github_summary"),
    ("What's today's LeetCode problem?", "leetcode_daily"),
    ("New agentic AI papers", "arxiv_search"),
    ("Draw a binary tree 8 4 12", "make_image"),
    ("How much battery is left?", "system_status"),
    ("Play some music", "play_music"),
    ("Back up my data", "backup_now"),
    ("Weekly review", "weekly_review"),
    ("Make my EduSkills internship report", "eduskills_report"),
    ("EduSkills log: finished module 3", "eduskills_log"),
    ("When is my exam?", "memory_recall"),
]


@pytest.mark.parametrize("message,tool", ROUTES)
def test_no_model_rules_pick_the_right_tool(message, tool):
    from airtac.llm import MockLLM
    names = list(tools.REGISTRY)  # all tools available, so the rules' order decides
    reply = MockLLM().chat([{"role": "user", "content": message}], tools.openai_schemas(names))
    assert reply.tool_call is not None and reply.tool_call.name == tool, (message, reply)


def test_a_plugin_can_only_call_the_websites_it_declared(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    token = plugin_api.CURRENT.set("leetcode")
    try:
        assert plugin_api.allowed("https://leetcode.com/graphql")
        assert not plugin_api.allowed("https://evil.example.com/steal")
        with pytest.raises(NotAllowed):
            fetch("https://evil.example.com/steal")
    finally:
        plugin_api.CURRENT.reset(token)
    token = plugin_api.CURRENT.set("web")  # "*" = any public site, never a private address
    try:
        assert not plugin_api.allowed("http://127.0.0.1:8765/api/health")
        assert not plugin_api.allowed("http://192.168.1.1/")
    finally:
        plugin_api.CURRENT.reset(token)


def test_offline_mode_makes_no_calls():
    token = plugin_api.CURRENT.set("leetcode")
    try:
        assert fetch("https://leetcode.com/graphql") is None
    finally:
        plugin_api.CURRENT.reset(token)


def test_a_broken_plugin_is_reported_not_fatal(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    (root / "broken").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "broken" / "manifest.toml").write_text('id = "broken"\nname = "Broken"\n')
    (root / "broken" / "__init__.py").write_text("raise RuntimeError('oops')\n")
    (root / "needy").mkdir()
    (root / "needy" / "manifest.toml").write_text('id = "needy"\n[requires]\nimports = ["no_such_package_xyz"]\n')
    monkeypatch.setattr(config, "PLUGINS_DIR", root)
    monkeypatch.syspath_prepend(str(tmp_path))
    import sys
    for mod in [m for m in sys.modules if m == "plugins" or m.startswith("plugins.")]:
        monkeypatch.delitem(sys.modules, mod)
    try:
        plugins.load_all(force=True)
        assert plugins.STATUS["broken"]["state"] == "error" and "oops" in plugins.STATUS["broken"]["reason"]
        assert plugins.STATUS["needy"]["state"] == "needs-packages"
    finally:
        monkeypatch.undo()
        for mod in [m for m in sys.modules if m == "plugins" or m.startswith("plugins.")]:
            del sys.modules[mod]
        plugins.load_all(force=True)
    assert plugins.STATUS["schedule"]["state"] == "on"
