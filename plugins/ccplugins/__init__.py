"""Claude Code plugins (Part F): a checked catalog, with on/off switches on Brains and plugins.

These run inside Claude Code, not inside AIRTAC: AIRTAC lists them (manifest: name, source, the sites it
may reach, what it may do), keeps your on/off choice, and gives the exact way to add each one. "On" puts it
in Dev tools' Claude Code step list. Each source was looked up on 17 Oct 2026; a name that couldn't be
verified is shown as "not found" instead of being guessed.
Official ones install from Anthropic's marketplace:  /plugin marketplace add anthropics/claude-plugins-official
then  /plugin install <name>@claude-plugins-official  (inside Claude Code).
"""
import json

from airtac import memory
from airtac.plugin_api import plugin

p = plugin(__name__)
OFFICIAL = "https://github.com/anthropics/claude-plugins-official"
CATALOG = [
    {"id": "superpowers", "name": "Superpowers", "source": "https://github.com/obra/superpowers", "domains": [],
     "does": "Skills that make Claude Code plan, test and review in steps.", "how": "Its README: add the obra/superpowers-marketplace marketplace, then install superpowers."},
    {"id": "context7", "name": "Context7", "source": OFFICIAL, "domains": ["context7.com"], "key": "CONTEXT7_API_KEY",
     "does": "Up-to-date library docs in Claude Code's context (Upstash).", "how": "/plugin install context7@claude-plugins-official"},
    {"id": "code-review", "name": "Code reviewer", "source": OFFICIAL, "domains": ["github.com"],
     "does": "Reviews pull requests with several agents and a confidence score.", "how": "/plugin install code-review@claude-plugins-official"},
    {"id": "code-simplifier", "name": "Code simplifier", "source": OFFICIAL, "domains": [],
     "does": "Simplifies recently changed code without changing what it does.", "how": "/plugin install code-simplifier@claude-plugins-official"},
    {"id": "gstack", "name": "Gstack", "source": "https://github.com/garrytan/gstack", "domains": [],
     "does": "Garry Tan's Claude Code skills and workflow setup.", "how": "Its README (clone it from Repositories)."},
    {"id": "omniroute", "name": "OmniRoute", "source": "https://github.com/diegosouzapw/OmniRoute", "domains": ["the AI providers you give it"],
     "does": "An AI gateway that routes requests between model providers. Not a Claude Code plugin; a separate server.",
     "how": "Its README. Keys you give it leave AIRTAC's vault: read its security notes first."},
    {"id": "claude-mem", "name": "claude-mem", "source": "https://github.com/thedotmack/claude-mem", "domains": [],
     "does": "Persistent memory across Claude Code sessions (Dev tools can set it up).", "how": "Dev tools > claude-mem, or its README."},
    {"id": "headroom", "name": "Headroom", "source": "https://github.com/chopratejas/headroom", "domains": [],
     "does": "Compresses what agents send to the model, to save tokens.", "how": "Its README."},
    {"id": "claude-code-setup", "name": "Claude Code setup", "source": OFFICIAL, "domains": [],
     "does": "Looks at a codebase and recommends hooks, skills, MCP servers and subagents.", "how": "/plugin install claude-code-setup@claude-plugins-official"},
    {"id": "task-observer", "name": "Task observer", "source": "https://github.com/rebelytics/one-skill-to-rule-them-all", "domains": [],
     "does": "A skill that watches tasks and suggests improvements to your skills.", "how": "Its README (a skill folder for ~/.claude/skills)."},
    {"id": "ralph-loop", "name": "Ralph loop", "source": OFFICIAL, "domains": [],
     "does": "Keeps Claude Code looping on a task until it's done (the \"Ralph Wiggum\" technique).", "how": "/plugin install ralph-loop@claude-plugins-official"},
    {"id": "ponytail", "name": "Ponytail", "source": "https://github.com/DietrichGebert/ponytail", "domains": [],
     "does": "Makes the coding agent write less, simpler code.", "how": "Its README."},
    {"id": "coderabbit", "name": "CodeRabbit", "source": OFFICIAL, "domains": ["api.coderabbit.ai"], "key": "CODERABBIT_API_KEY",
     "does": "AI code review with 40+ static analysers.", "how": "/plugin install coderabbit@claude-plugins-official (key on APIs and keys, optional)"},
]


def _on() -> list[str]:
    try:
        return json.loads(memory.kv_get("ccplugins", "on") or "[]")
    except ValueError:
        return []


def catalog() -> list[dict]:
    on = set(_on())
    return [{**c, "on": c["id"] in on, "found": True, "official": c["source"] == OFFICIAL} for c in CATALOG]


def switch(plugin_id: str, on: bool) -> list[str]:
    if not any(c["id"] == plugin_id for c in CATALOG):
        raise KeyError(plugin_id)
    now = set(_on())
    (now.add if on else now.discard)(plugin_id)
    memory.kv_set("ccplugins", "on", json.dumps(sorted(now)))
    return sorted(now)


@p.routes
def api(router) -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel

    class Switch(BaseModel):
        id: str
        on: bool

    @router.get("")
    def read():
        return {"plugins": catalog(), "marketplace": "/plugin marketplace add anthropics/claude-plugins-official"}

    @router.post("")
    def write(body: Switch):
        try:
            return {"on": switch(body.id, body.on)}
        except KeyError:
            raise HTTPException(404, "Not in the catalog.")
