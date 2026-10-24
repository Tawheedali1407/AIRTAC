"""The tool registry: every tool from every loaded plugin, how to run one, and which ones the
model gets to see for a question.

Why choose tools per question: a small local model (3-4B) picks the right tool far more often
from 10 options than from 35, and the tool list costs context (RAM) on every request. So the
router sends the tools of the plugins whose keywords match, plus the "always" plugins.
"""
import logging
import re

from airtac import config, memory
from airtac.models import SCREEN_LIMIT, SPOKEN_LIMIT, ToolResult, clean_spoken
from airtac.plugin_api import CURRENT, LOADED, NotAllowed, Tool

log = logging.getLogger("airtac.tools")

REGISTRY: dict[str, Tool] = {}
RULES: list = []                 # MockRule objects from every plugin, sorted by priority


def reset() -> None:
    REGISTRY.clear()
    RULES.clear()


def register_plugin(p) -> None:
    for t in p.tools:
        if t.name in REGISTRY:
            log.warning("tool %s from %s replaces the one from %s", t.name, p.id, REGISTRY[t.name].plugin)
        REGISTRY[t.name] = t
    RULES.extend(p.rules)
    RULES.sort(key=lambda r: r.priority)


def _has(need: str) -> bool:
    # "brain" means a real model (not the keyword rules); anything else is a secret in .env.
    return config.LLM_PROVIDER != "mock" if need == "brain" else bool(config.secret(need))


def is_live(tool: Tool) -> bool:
    """The live version only when everything it needs is there (keys, a model). In the offline tests,
    only tools that need just a model (needs=("brain",)) go live: the model runs on this laptop."""
    if tool.live is None:
        return False
    if config.OFFLINE and tool.needs != ("brain",):
        return False
    return all(_has(name) for name in tool.needs)


def _connection_name(key: str) -> str:
    from airtac import connections
    connections.all_connectors()
    for c in connections.REGISTRY.values():
        if any(f.key == key for f in c.fields):
            return c.name
    return key


def not_connected(tool: Tool) -> ToolResult:
    """What to say when a tool's key (or a model) is missing: which one, and where to add it."""
    missing = [n for n in tool.needs if not _has(n)]
    if missing == ["brain"]:
        return ToolResult("That needs a brain. Start Ollama on this PC, or connect a cloud brain on the Connections page.",
                          "Menu, Connections: press Check on Ollama on this PC, or add a Groq or Nebius key.", ok=False)
    names = ", ".join(dict.fromkeys(_connection_name(n) for n in missing if n != "brain"))
    return ToolResult(f"That needs {names}. Connect it on the Connections page, then ask again.",
                      f"Not connected yet: menu, Connections, {names}.", ok=False)


def pick(tool: Tool):
    """The function to run now, or None when a missing key or model stands in the way."""
    if tool.live is None:
        return tool.fixture            # a local tool: this is the real thing
    if is_live(tool):
        return tool.live
    if config.OFFLINE:
        return tool.fixture            # the tests: recorded responses
    return tool.local


def run_tool(name: str, args: dict) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(f"I don't have a tool called {name}.", ok=False)
    # The model sometimes invents extra arguments; keep only the ones we declared.
    args = {k: v for k, v in (args or {}).items() if k in tool.parameters["properties"]}
    fn = pick(tool)
    if fn is None:
        return not_connected(tool)
    token = CURRENT.set(tool.plugin)   # fetch() checks this plugin's permissions
    try:
        result = fn(**args)
    except NotAllowed as e:
        log.warning("blocked: %s", e)
        return ToolResult("That plugin tried to reach a website it isn't allowed to, so I stopped it.", ok=False)
    except TypeError as e:  # usually the model left out a required argument
        log.warning("tool %s got bad arguments: %s", name, e)
        return ToolResult(f"I couldn't run {name} with those details. Could you say it another way?", ok=False)
    except Exception:  # a tool bug must never crash the whole turn
        log.exception("tool %s failed", name)
        return ToolResult(f"Something went wrong while running {name}.", ok=False)
    finally:
        CURRENT.reset(token)
    return ToolResult(clean_spoken(result.spoken, SPOKEN_LIMIT), (result.screen or "")[:SCREEN_LIMIT], result.ok,
                      result.data)


def run_and_log(name: str, args: dict) -> ToolResult:
    result = run_tool(name, args)
    tool = REGISTRY.get(name)
    memory.log_tool(name, tool.plugin if tool else "", result.ok)
    return result


def openai_schemas(names: list[str] | None = None) -> list[dict]:
    """Tool list in the format OpenAI-compatible APIs and Ollama expect."""
    chosen = [REGISTRY[n] for n in names if n in REGISTRY] if names is not None else list(REGISTRY.values())
    return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in chosen]


def _plugin_words(p) -> list[str]:
    words = [w.lower() for w in p.manifest.get("keywords", [])]
    for rule in p.rules:  # the no-model rules' keywords are good routing hints too
        words.extend(k.lower() for k in rule.keywords)
    return words


def select_tools(message: str, app_id: str | None = None, limit: int | None = None, only: list[str] | None = None) -> list[str]:
    """The tool names the model sees for this message.
    In an app's own chat: that app's tools (plus the "always" plugins).
    On Home: plugins whose keywords appear in the message, best match first.
    only: the plugins you picked in the chat's side panel (just those; [] means no tools at all)."""
    limit = limit or config.MAX_TOOLS
    always = [p for p in LOADED.values() if p.manifest.get("always")]
    if only is not None:
        chosen = [LOADED[i] for i in dict.fromkeys(only) if i in LOADED]
    elif app_id and app_id in LOADED:
        chosen = [LOADED[app_id]] + [p for p in always if p.id != app_id]
    else:
        low = " " + re.sub(r"[^a-z0-9+#' ]+", " ", message.lower().replace("’", "'")) + " "
        scored = []
        for p in LOADED.values():
            score = sum(1 for w in _plugin_words(p) if f" {w} " in low or (len(w) > 4 and w in low))
            if score:
                scored.append((score, p))
        scored.sort(key=lambda sp: -sp[0])
        chosen = [p for _, p in scored] + [p for p in always if p not in [q for _, q in scored]]
        if not scored:  # nothing matched: let the model see a bit of everything, "always" plugins first
            chosen = always + [p for p in LOADED.values() if p not in always]
    names = []
    for p in chosen:
        for t in p.tools:
            if t.name not in names:
                names.append(t.name)
    return names[:limit] if limit > 0 else names
