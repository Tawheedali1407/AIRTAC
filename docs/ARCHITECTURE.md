# How AIRTAC Advance works

```mermaid
flowchart LR
  You((You: voice or text)) --> Page[web/ page + PWA<br/>localhost:8765]
  Phone((Airtac Mobile<br/>Android)) -->|HTTPS :8766, pinned cert,<br/>device token| LAN
  subgraph Server [one Python process: scripts/serve.py]
    Gate[middleware<br/>host check, Origin,<br/>passcode/session, install token,<br/>rate limit] --> API[FastAPI routes<br/>airtac/main.py]
    LAN[Wi-Fi listener<br/>airtac/pairing.py<br/>only while switched on] --> Gate
    API --> Agent[agent.py<br/>one tool per step,<br/>Confirm for write/device]
    Agent --> Brains[brains.py + providers.py<br/>roles, picker, failover]
    Agent --> Router[tools.py<br/>at most 12 tools]
    Scheduler[scheduler.py<br/>plugin jobs] --> Notes[(notifications)]
    Work[work.py<br/>Work mode jobs]
    Vault[plugins/passwords<br/>vault.bin: Argon2id +<br/>AES-256-GCM]
  end
  Page --> Gate
  Brains --> Ollama[Ollama on this PC<br/>F:/Ollama/models]
  Brains -->|only with your key| Cloud[Gemini, Groq, Nebius,<br/>NVIDIA, OpenRouter, ...]
  Router --> Plugins
  subgraph Plugins [plugins/ (one folder each)]
    P1[schedule] & P2[knowledge] & P3[daily, learn, lectures] & P4[account, repos] & P5[...]
  end
  Plugins -->|guard_http: manifest sites only| Web[(the web)]
  Plugins --> DB[(data/airtac.db<br/>SQLite)]
  Notes --> Page
```

## Ports and processes
| Port | Who listens | Reached from | When |
|---|---|---|---|
| 8765 | AIRTAC (uvicorn, `scripts/serve.py`), 127.0.0.1 only | this PC (other devices via Tailscale `serve` or `trusted_hosts` + passcode) | always |
| 8766 | AIRTAC's phone listener, HTTPS, all interfaces | paired phones on Wi-Fi or Tailscale | only while Settings > Devices > Phone access on Wi-Fi is on |
| 11434 | Ollama | AIRTAC on this PC | when Ollama runs |
| random | one-time Google sign-in listener on 127.0.0.1 | your browser, once | during Sign in with Google |

## API contract (core routes)
Every `/api/` call needs the install token (cookie `airtac_install` or header `X-AIRTAC-Token`) except `/api/health`,
`/api/login` and `/api/pair/finish`; a paired phone sends `Authorization: Bearer <device token>` instead. Errors are
JSON `{"detail": "<a sentence for you>"}` and never repeat what was typed. 401 = sign in, 403 = not from here / not
allowed, 423 = vault locked, 429 = request limit.

| Area | Routes |
|---|---|
| Health and login | `GET /api/health`, `POST /api/login`, `POST /api/logout` |
| Chat | `POST /api/chat` {message, session_id, brain?, plugins?, attachments?} -> {spoken, screen, brain, sources, pending?, usage}; `POST /api/confirm`; `POST /api/feedback`; `POST /api/upload` {section, name, data_base64} |
| Day | `GET /api/briefing`, `GET /api/notifications`, `GET /api/feed`, `GET /api/progress` |
| History and memory | `GET /api/history?q=`, `GET /api/history/chats`, `POST /api/history/delete`, `GET/POST /api/memory_cache/...` |
| Brains | `GET /api/brains`, `GET/POST /api/brains/models`, `GET /api/brain_choices`, `POST /api/brain_choices/refresh` |
| Keys (PC only) | `GET /api/connections`, `POST /api/connections/{id}` (+ `/test`, `/disconnect`, `/reveal`, `/copy`) |
| Plugins and apps | `GET /api/plugins`, `POST /api/plugins/{id}/switch`, `GET /api/apps`, `GET /api/apps/{id}`; each plugin's own API under `/api/plugins/<id>/` |
| Work mode | `GET/POST /api/work`, `GET /api/work/{id}`, `.../stop`, `.../convert/{fmt}`, `.../confirm` |
| Files | `GET /api/files`, `GET /files/<plugin>/<name>` (only inside `outputs/`) |
| Phones (Part G) | `POST /api/pair/start` (PC only), `POST /api/pair/finish`, `GET /api/pair/me`, `GET /api/pair/web`, `GET /api/devices`, `POST /api/devices/{id}/revoke`, `POST /api/devices/lan`, `GET /download/airtac.apk` |

## Modules
| Module | Job |
|---|---|
| `airtac/main.py` | the HTTP API, middleware and the page |
| `airtac/auth.py` | passcode sessions, "this PC" check, per-install token |
| `airtac/pairing.py` | phone pairing, device tokens, TLS certificate, the Wi-Fi listener |
| `airtac/agent.py`, `tools.py`, `brains.py`, `providers.py`, `llm.py` | the answer loop, tool routing, brain choice and failover, model calls and the persona |
| `airtac/plugin_api.py`, `plugins.py` | the plugin hooks, `fetch()` with the manifest's sites, `guard_http` |
| `airtac/connections.py`, `vault.py` | API key cards (stored in the vault) |
| `airtac/memory.py`, `db.py`, `cleanup.py` | SQLite, facts, history, the Memory and cache page |
| `airtac/scheduler.py`, `work.py`, `voice.py`, `websearch.py`, `logsafe.py` | background jobs, Work mode, speech, the web reader, log redaction |
| `web/` | `app.js` (views, the one `api()` client), `hub.js` (menu pages), `vault.js`, `dock.js`, `tabs.js`, `attach.js`, `devices.js` |
| `mobile/` | Airtac Mobile (Flutter) |
| `scripts/` | start/stop, doctor, security_check, qa_crawl, eval_persona, probe_embed, Ollama helpers |


## A question, step by step
1. **The page** sends your words to `/api/chat`, or `/api/voice/stt` first if speech is on the PC.
2. **Safety** refuses contest links and similar things before anything else runs.
3. **Brains** picks a role from your words and the app you're in:
   - medical words go to the medicine brain (safety prompt, no tools),
   - code words go to coding,
   - maths and engineering words go to reasoning (thinking switched on),
   - everything else goes to general.
4. **Context**: the system prompt plus up to:
   - 5 remembered facts,
   - 3 lessons from your corrections,
   - 3 passages from your library (only if they really match), with their sources.
5. **Router** sends the model only the tools of the plugins your words point to, at most 12, because small models choose better from fewer.
6. **Loop**, at most 6 steps and 240 s:
   - the model either answers or calls one tool;
   - read and local tools run straight away;
   - write and device tools wait for your Confirm.
7. **Reply** has a spoken line, details on screen, which brain answered, your library sources, and a message id for 👍 / 👎.

## Plugins
A plugin is a folder `plugins/<id>/` with `manifest.toml` and `__init__.py`. The manifest declares:
- its name, icon, keywords and quick actions,
- the Python packages it needs (`requires.imports`),
- the other plugins it needs (`requires.plugins`),
- the websites it may call (`permissions.network`),
- how risky its tools are.

The loader turns on every plugin that can run. The rest get a reason on the Plugins page: planned, switched off, needs packages, or failed to load. A broken plugin never stops AIRTAC from starting.

Hooks a plugin can give: tools, `dashboard` (its app page), `briefing` (a line in your day), `feed` (News on Home), `progress` (numbers on Home), `context` (background for every answer), `job(seconds)` (background work), `routes` (its own HTTP API under `/api/plugins/<id>/`) and `mock_rule` (for the no-model mode). See [PLUGINS.md](PLUGINS.md).

## Data
One SQLite file, `data/airtac.db`.

| Owner | Tables |
|---|---|
| Core | messages, facts (full-text index), tool_runs, notifications, kv, feedback, lessons, jobs, skills |
| Plugins | their own tables, through `p.sql(...)`: events, problems, syllabus, qa, kdocs/kchunks, edu_internships, edu_log |

Files AIRTAC makes go to `outputs/<plugin>/`, served at `/files/<plugin>/...`, and only from inside `outputs/`.

## Why these choices
- **SQLite, not a server database**: nothing to install, one file to back up, fast enough for one person.
- **Ollama's own API**: it can switch thinking off. A 4B model thinking on an i3 takes minutes.
- **One model in memory**: `OLLAMA_MAX_LOADED_MODELS=1`, so the brains take turns inside 8 GB.
- **Plain HTML and JS, no build step**: easy to read, quick on an old laptop, and installable as an app.
- **Base64 uploads**: no extra upload library, and a 60 MB limit.
