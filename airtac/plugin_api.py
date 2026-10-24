"""Everything a plugin needs, in one import:

    from airtac.plugin_api import plugin, Tool, ToolResult, schema, fetch, unreachable, SAMPLE_TAG

    p = plugin(__name__)                      # this plugin, as described by its manifest.toml
    p.add(Tool("hello", "Say hello.", schema(), "local", hello))

A plugin can also give the core hooks (all optional):
    @p.dashboard   def build(page): ...        fill its app page (stats, bars, lists)
    @p.briefing    def say(now): ...           a sentence for "your day" when AIRTAC opens
    @p.feed        def news(): ...             a section for "News around you"
    @p.progress    def nums(): ...             numbers for "Your progress"
    @p.job(60)     def tick(now): ...          runs in the background every 60 seconds
    @p.routes      def api(router): ...        extra HTTP routes under /api/plugins/<id>/
    @p.context     def ctx(message): ...       background text added to the model's instructions (the
                                                knowledge library uses it to bring in passages from your books)
    @p.observe     def saw(message, steps): ... after each reply: notice something in what you said (the
                                                schedule adds dates you mention). Return None, or
                                                {"say": "...", "screen": "...", "undo": {...}}
    p.mock_rule(priority, keywords, tool, args)  keyword rule for the no-model test mode
    p.brain_provider("name", factory, ...)     a new place a model can answer from (a local engine or a
                                                service). Add its name to [failover] order to use it.
    @p.usage       def seen(tier, model, prompt, completion): ...   after every model answer: how many
                                                tokens it took (the tokens plugin counts them)
    @p.brain_gate  def gate(tier, max_tokens): ...  before a brain answers: return None to skip that
                                                cloud tier now (over its daily limit), or the answer length to use
    p.connector(name=..., fields=[...], test=fn) a card on the Connections page for the key this plugin
                                                needs, so it's set up from the app (see airtac/connections.py)

Permissions: a plugin's manifest lists the web domains it may call. fetch() refuses any
other domain, so a plugin can't quietly send your data somewhere it didn't declare.
This catches mistakes and makes intent visible; it is not a sandbox (see docs/SECURITY.md).
"""
import json
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from airtac import config, db
from airtac.models import ToolResult  # noqa: F401  (re-exported for plugins)

log = logging.getLogger("airtac.plugins")

SAMPLE_TAG = "[Sample data]"
RISKS = ("read", "local", "write", "device")  # write and device wait for your Confirm


@dataclass
class Tool:
    name: str
    description: str   # one line, specific, must not overlap with other tools
    parameters: dict   # JSON schema for the arguments
    risk: str          # read | local | write | device
    fixture: Callable[..., ToolResult]                 # a local tool's work; for an outside service, the offline tests' answer
    live: Callable[..., ToolResult] | None = None      # calls an outside service
    needs: tuple = ()  # secrets that must be set before live() is used, e.g. ("GITHUB_TOKEN",)
    summarize: Callable[[dict], str] | None = None     # write/device tools: text for the confirm box
    plugin: str = ""
    local: Callable[..., ToolResult] | None = None     # what still works without the key (if anything)


def schema(properties: dict | None = None, required: tuple = ()) -> dict:
    return {"type": "object", "properties": properties or {}, "required": list(required)}


@dataclass
class MockRule:
    priority: float
    keywords: tuple
    tool: str
    args: Callable[[str, list], dict]
    plugin: str = ""


@dataclass
class Plugin:
    id: str
    name: str
    dir: Path
    manifest: dict
    tools: list = field(default_factory=list)
    hooks: dict = field(default_factory=dict)
    jobs: list = field(default_factory=list)       # [(every_seconds, fn)]
    rules: list = field(default_factory=list)      # [MockRule]

    # --- tools
    def add(self, tool: Tool) -> Tool:
        if tool.risk not in RISKS:
            raise ValueError(f"{tool.name}: risk must be one of {RISKS}")
        tool.plugin = self.id
        if isinstance(tool.needs, str):
            tool.needs = (tool.needs,) if tool.needs else ()
        self.tools.append(tool)
        return tool

    # --- hooks (decorators)
    def _hook(self, name):
        def deco(fn):
            self.hooks[name] = fn
            return fn
        return deco

    @property
    def dashboard(self):
        return self._hook("dashboard")

    @property
    def briefing(self):
        return self._hook("briefing")

    @property
    def feed(self):
        return self._hook("feed")

    @property
    def progress(self):
        return self._hook("progress")

    @property
    def routes(self):
        return self._hook("routes")

    @property
    def observe(self):
        return self._hook("observe")

    @property
    def context(self):
        return self._hook("context")

    @property
    def usage(self):
        return self._hook("usage")

    @property
    def attached(self):
        """fn(ids, message) -> {"text", "sources"}: what files attached to this message say."""
        return self._hook("attached")

    @property
    def startup(self):
        """fn(): runs once when the server starts (not in the tests), e.g. Dictation starting its helper."""
        return self._hook("startup")

    @property
    def brain_gate(self):
        return self._hook("brain_gate")

    def job(self, every_seconds: int):
        def deco(fn):
            self.jobs.append((every_seconds, fn))
            return fn
        return deco

    def brain_provider(self, name: str, factory=None, *, label: str = "", model: str = "", key_env: str = "",
                       base_url: str = "", note: str = "", trains_on_data: bool = False) -> None:
        """Add a brain provider without touching the core. Give either factory(model) -> an object with
        chat(messages, tools, max_tokens), or base_url of an OpenAI-compatible server (plus key_env)."""
        from airtac import providers
        providers.register(providers.Provider(name, label or name, base_url, key_env, model,
                                              note or f"Added by the {self.name} plugin.", factory, trains_on_data))

    def connector(self, *, name: str, about: str, fields: list, powers: list | None = None, get_key: str = "",
                  test=None, order: int = 50, note: str = "", id: str = "", logo: str = "", group: str = "Services",
                  docs: str = "") -> None:
        """A card on the APIs and keys page. fields: [Field("TAVILY_API_KEY", "API key"), ...] from
        airtac.connections; test(http) -> (ok, sentence) makes one real call with the saved key.
        group: "Search and dev tools", "Hackathon and cloud" or "Services"."""
        from airtac import connections
        connections.add(connections.Connector(id or self.id, name, about, powers or [], fields, get_key, test, order,
                                              group, note, self.id, logo, docs))

    def mock_rule(self, priority: float, keywords: tuple, tool: str, args=None) -> None:
        self.rules.append(MockRule(priority, tuple(keywords), tool, args or (lambda t, m: {}), self.id))

    # --- data and settings
    def sample(self, name: str) -> dict:
        """plugins/<id>/sample/<name>.json, read once."""
        key = (self.id, name)
        if key not in _SAMPLES:
            _SAMPLES[key] = json.loads((self.dir / "sample" / f"{name}.json").read_text(encoding="utf-8"))
        return _SAMPLES[key]

    def setting(self, key: str, default=None):
        return config.plugin_settings(self.id).get(key, default)

    def secret(self, name: str) -> str:
        return config.secret(name)

    def output_dir(self, sub: str = "") -> Path:
        """outputs/<id>/<sub>/: where this plugin's files go (gitignored, served under /files/<id>/)."""
        path = config.OUTPUTS_DIR / self.id / sub if sub else config.OUTPUTS_DIR / self.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sql(self, ddl: str) -> None:
        db.add_schema(ddl)

    @property
    def network(self) -> list[str]:
        return list(self.manifest.get("permissions", {}).get("network", []))


_SAMPLES: dict = {}
LOADED: dict[str, Plugin] = {}          # filled by airtac.plugins before each plugin is imported
CURRENT: ContextVar[str] = ContextVar("airtac_plugin", default="")
CURRENT_SESSION: ContextVar[str] = ContextVar("airtac_session", default="default")


def plugin(module_name: str) -> Plugin:
    """The Plugin object for a plugin package ("plugins.github" -> LOADED["github"])."""
    plugin_id = module_name.split(".")[1] if module_name.startswith("plugins.") else module_name
    return LOADED[plugin_id]


# ---------------------------------------------------------------- outside calls

CACHE_SECONDS = 300
_cache: dict[str, tuple[float, object]] = {}


class NotAllowed(Exception):
    pass


def allowed(url: str, plugin_id: str | None = None) -> bool:
    """Is this plugin allowed to call this URL? Its manifest lists domains; subdomains count."""
    plugin_id = plugin_id if plugin_id is not None else CURRENT.get()
    host = (urlparse(url).hostname or "").lower()
    p = LOADED.get(plugin_id)
    if p is None or not host:
        return False
    domains = [d.lower() for d in p.network]
    if any(host == d or host.endswith("." + d) for d in domains):
        return True
    if "*" in domains:  # "any public website" (the web reader): never an address inside your network
        from airtac import websearch
        try:
            websearch.check_public(url)
            return True
        except websearch.Blocked:
            return False
    return False


def fetch(url: str, *, method: str = "GET", headers: dict | None = None, params: dict | None = None,
          json_body: dict | None = None, as_text: bool = False, write: bool = False, timeout: float = 8,
          retry: bool = True):
    """One outside HTTP call: checked against the plugin's permissions, 8 s timeout, one retry,
    5-minute cache. Returns the parsed body, or None on failure so the tool can say a plain sentence.
    Writes are never cached or retried: a timed-out write may still have worked."""
    if config.OFFLINE:
        log.warning("offline mode: refused %s", url)
        return None
    if not allowed(url):
        log.warning("plugin %r may not call %s (not in its manifest's permissions.network)", CURRENT.get(), url)
        raise NotAllowed(f"{CURRENT.get() or 'unknown plugin'} may not call {urlparse(url).hostname}")
    key = f"{method} {url} {params} {json_body}"
    hit = _cache.get(key)
    if hit and not write and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    attempts = 1 if write or not retry else 2   # retry=False: a page that must answer fast (Home's news)
    for attempt in range(1, attempts + 1):
        try:
            r = httpx.request(method, url, headers=headers, params=params, json=json_body, timeout=timeout)
            r.raise_for_status()
            data = r.text if as_text else r.json()
            if not write:
                _cache[key] = (time.time(), data)
            return data
        except (httpx.HTTPError, ValueError) as e:
            log.warning("live call to %s failed (attempt %d/%d): %s", urlparse(url).hostname, attempt, attempts, type(e).__name__)
    return None


# ---------------------------------------------------------------- the backend's own allowlist check
# fetch() checks a plugin's manifest, but a plugin could also use httpx directly. guard_http() wraps
# httpx's transports, so every request is checked: when the code that made it lives in plugins/<id>/,
# the address must be in that plugin's permissions.network (or be Ollama on this PC). Calls made by the
# core (brains, fetch(), the web reader, Connections tests) are checked where they're made, so they pass.

class Blocked(Exception):
    pass


def _calling_plugin() -> str:
    """The plugin whose code made this HTTP request, or "" when the core made it."""
    import sys
    frame = sys._getframe(2)
    while frame is not None:
        mod = frame.f_globals.get("__name__", "")
        if not mod.startswith(("httpx", "httpcore", "anyio", "h11", "h2", "ssl", "socket", "contextlib", "airtac.plugin_api")):
            if mod.startswith("plugins."):
                return mod.split(".")[1]
            return ""
        frame = frame.f_back
    return ""


def check_request(url: str) -> None:
    pid = _calling_plugin()
    if not pid or pid not in LOADED:
        return
    if url.startswith(config.LLM_BASE_URL.rstrip("/") + "/"):     # Ollama on this PC (embeddings, loaded models)
        return
    if not allowed(url, pid):
        log.warning("blocked: plugin %r tried to reach %s (not in its manifest)", pid, urlparse(url).hostname)
        raise Blocked(f"The {pid} plugin may not reach {urlparse(url).hostname} (not in its manifest's permissions.network).")


_GUARDED = {"on": False}


def guard_http() -> None:
    if _GUARDED["on"]:
        return
    real_sync, real_async = httpx.HTTPTransport.handle_request, httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(self, request):
        check_request(str(request.url))
        return real_sync(self, request)

    async def handle_async_request(self, request):
        check_request(str(request.url))
        return await real_async(self, request)

    httpx.HTTPTransport.handle_request = handle_request
    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request
    _GUARDED["on"] = True


# Sites the page may show inside a frame (the Tabs view). Each plugin gives a function that returns
# origins like "https://example.org"; main.py adds them to the page's Content-Security-Policy (frame-src).
_FRAME_FNS: dict = {}
_FRAMES = {"at": 0.0, "set": frozenset()}


def frame_source(plugin_id: str, fn) -> None:
    _FRAME_FNS[plugin_id] = fn


def frame_sources(refresh: bool = False) -> frozenset:
    """Every plugin's frame origins (only plain https origins), worked out at most every 30 seconds."""
    if refresh or time.time() - _FRAMES["at"] > 30:
        out = set()
        for pid, fn in list(_FRAME_FNS.items()):
            try:
                out |= {o for o in fn() if re.fullmatch(r"https://[a-z0-9.-]+(:\d+)?", o or "")}
            except Exception as e:  # a broken list must not break the page
                log.warning("frame sources from %s failed: %s", pid, type(e).__name__)
        _FRAMES.update(at=time.time(), set=frozenset(out))
    return _FRAMES["set"]


def local_only(request) -> None:
    """For routes that touch secrets or open things on this PC: refuse anything but this PC itself."""
    from fastapi import HTTPException
    from airtac import auth
    if not auth.is_local_request(request):
        raise HTTPException(403, "For your safety this only works on the PC running AIRTAC, not from another device.")


def unreachable(service: str) -> ToolResult:
    return ToolResult(f"I couldn't reach {service} right now. Please try again in a minute.", ok=False)


# ---------------------------------------------------------------- small text helpers for mock rules

def after(text: str, pattern: str) -> str:
    """The part of text after the first match of pattern, trimmed."""
    import re
    m = re.search(pattern, text, re.IGNORECASE)
    return text[m.end():].strip(" :,.?!") if m else ""


def capitalize(s: str) -> str:
    return s[:1].upper() + s[1:]
