# Writing a plugin

Start from the template:
```
.venv\Scripts\python.exe scripts\new_plugin.py weather "Weather" "Today's weather where you are."
```
That makes `plugins/weather/manifest.toml`, `plugins/weather/__init__.py` and a test. Restart AIRTAC and it shows up in the menu.

## manifest.toml
```toml
id = "weather"
name = "Weather"
icon = "cloud"            # an icon id from web/index.html (i-<name>)
about = "Today's weather where you are."
order = 100               # menu order; also load order (dependencies need a lower number)
always = false            # true: its tools are offered with every question
app = true                # false: no app page (like Web, which has its own page)
action = ""               # a special panel on its app page: schedule, paste, syllabus, library, lessons
keywords = ["weather", "rain", "temperature"]   # route questions here
quick = ["Will it rain today?"]                 # buttons on its app page
status = "ready"          # "planned": listed with its plan, not loaded

[requires]
imports = ["requests"]    # Python modules; missing ones mean "needs packages" (unless optional = true)
pip = ["requests"]        # pip names, if they differ from the module names
plugins = ["schedule"]    # other plugins it needs

[permissions]
network = ["api.open-meteo.com"]   # the only sites fetch() lets it call; "*" = any public site
risk = "read"             # read | local | write (asks first) | device (asks first)

[plan]                    # for planned plugins: shown on the Plugins page
steps = ["..."]
```

## __init__.py
```python
from airtac.plugin_api import Tool, ToolResult, fetch, plugin, schema, unreachable

p = plugin(__name__)

def weather_now(city: str = "") -> ToolResult:
    data = fetch("https://api.open-meteo.com/v1/forecast", params={...})   # checked against permissions
    if data is None:
        return unreachable("the weather service")
    return ToolResult("It's 31 degrees and clear.", "details for the screen")

p.add(Tool("weather_now", "Get the current weather for a city.", schema({"city": {"type": "string"}}),
           "read", weather_now))
```

## More hooks (all optional)

- `@p.observe def saw(message, tools_used)`: runs after each reply. Notice something in what the user said and act on it, then return `{"say": ..., "screen": ..., "undo": {"method", "url", "label"}}` so the reply mentions it and the page shows an Undo button. The schedule plugin uses this to add dates you mention.
- `p.brain_provider("name", factory, label=..., model=..., key_env=...)`: a new place a model can answer from (a local engine, or any OpenAI-compatible service with `base_url=`). Add its name to `[failover] order` and AIRTAC uses it, with no change to the core.
- `local_only(request)` (from `airtac.plugin_api`): put it first in a route that touches secrets or opens things on the PC. It refuses any other device.
- `@p.usage def seen(tier, model, prompt, completion)`: called after every model answer with its token counts.
- `@p.brain_gate def gate(tier, max_tokens)`: called before a brain answers. Return None to skip that cloud brain now, or the answer length to use (the tokens plugin uses both).
- `p.connector(name=..., about=..., fields=[Field("MY_KEY", "API key")], test=fn, get_key="https://...")`: a card on the Connections page for the key your plugin needs. `test(http)` makes one real call and returns `(ok, sentence)`. Read the key with `p.secret("MY_KEY")`; it may come from the vault or `.env`.
- `airtac.vault`: `save/get/delete(owner, site, field)` for passwords and cookies in the operating system's password store. Never put a secret in a tool result, a log line or the model's context.

## Rules that keep AIRTAC good to use
- **spoken** is 1 to 3 short sentences, with no markdown and no web addresses (it is read aloud). **screen** holds the details, up to 1,500 characters.
- **One clear description per tool.** Don't overlap with other tools: the model picks by description.
- **Anything that changes things outside AIRTAC** is `write` or `device`, so it waits for Confirm. Give it a `summarize=` so the confirm box says exactly what will happen.
- **Never invent data, never show samples.** If there's no answer, say so. A tool whose key is missing gets `local=` (what still works without it) or AIRTAC says which connection to add. `sample/` holds recorded responses for the offline tests only (`AIRTAC_OFFLINE`).
- **Use `fetch()` for web calls.** It checks permissions, has an 8 s timeout, retries once, caches for 5 minutes and returns None on failure.
- **Add a `mock_rule`** so it works with no model, and a test (`python -m pytest` must pass offline).
- **Settings and secrets:** `[plugins.<id>]` in config/airtac.toml (`p.setting(...)`); secrets from the Connections page or .env (`p.secret("NAME")`, add a card with `p.connector`).

## Hooks added in 0.4

- `@p.context def ctx(message, session_id="default")`: a context hook may now take a second argument, the
  current chat's session id. The session-memory plugin uses it to pull in relevant *other* chats without
  ever including the current one. The old one-argument form still works.
- `airtac.plugin_api.CURRENT_SESSION`: a context variable set to the current chat's session id for the
  whole turn, so an observer knows which chat it's in.
- `p.connector(..., logo="deepseek")`: a connection card can carry a logo (a file in `web/logos/`).
- Brain providers can set `get_key`, `logo`, `paid`, `temperature` (None to send none), `token_param`
  (`max_completion_tokens` for newer OpenAI models), `token_scale` and `extra`, so a new OpenAI-compatible
  service drops in with the right request shape. A provider with a key that isn't in `[failover] order` is
  tried after the ones that are (free before paid), unless it may train on your data (then list it yourself).

## The apps catalog and logos (0.4)
- One file, `plugins/desktop/apps.json`, lists every AI app and social site: `id, name, url, section`
  (`ai`/`social`), `group`, `about`, `mark` (two-letter badge), `login` (social sign-in page), `logo`.
- Logos are made once by `scripts/make_logos.py` from the Simple Icons package (CC0) into `web/logos/`.
  Add a logo there and set `"logo": "<name>"`; AIRTAC never fetches a logo at run time. Missing ones show
  the letter badge and are listed in `web/logos/CREDITS.md`.

## The password vault (0.4)
- `plugins/passwords/store.py` is the vault (scrypt + Fernet, `data/vault.db`); the plugin has **no tools**,
  so the model can't reach it, and every route is `local_only`. See [PASSWORDS.md](PASSWORDS.md). Never add a
  tool that reads it.

## Dev tools and the MCP bridge (0.4)
- `plugins/devtools/` sets up Claude Code, claude-mem, Agent Reach and Strix with fixed commands, on this PC
  only. `scripts/mcp_server.py` exposes AIRTAC to Claude Code over MCP (read-only, loopback only). See
  [DEV_TOOLS.md](DEV_TOOLS.md).
