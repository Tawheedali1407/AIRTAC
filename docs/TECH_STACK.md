# Tech stack: every language, library, service and tool AIRTAC Advance uses

Version 0.4.1 (14 Oct 2026). The licences are in [OPEN_SOURCE.md](OPEN_SOURCE.md). How the parts fit together
is in [ARCHITECTURE.md](ARCHITECTURE.md).

## In one paragraph
AIRTAC Advance is a **Python** web server (**FastAPI** on **Uvicorn**) that runs on your Windows PC. It serves
a single web page written in plain **HTML, CSS and JavaScript**, with no framework and no build step.
The page installs like an app on your PC and phone (a PWA). Everything it knows is kept in **SQLite** files in `data/`.
The "brain" is a large language model: **Ollama** on your PC, or a cloud model you add a key for. Every
feature is a **plugin** (a folder in `plugins/`). It's tested **offline** with **pytest** (273 tests).

## Languages

| Language | Where | Lines (approx.) | What it does |
|---|---|---|---|
| **Python** (3.11+, runs on 3.14) | `airtac/` (core), `plugins/`, `scripts/`, `tests/` (89 files) | 14,400 | the server, the agent loop, every plugin, the vault, the tests |
| **JavaScript** (plain ES2020+, no framework) | `web/app.js`, `hub.js`, `dock.js`, `boot.js`, `login.js`, `sw.js` | 3,900 | the page: chat, apps, Social, AI apps, Passwords, Settings, side panel, start-up animation, offline shell |
| **CSS** | `web/style.css`, `dock.css`, `boot.css` | 1,650 | five themes (Dark, Light, High contrast, Arc, Gilded) from CSS variables; phone layout |
| **HTML** | `web/index.html`, `login.html` | 760 | one page with every view, and the passcode page |
| **SQL** (SQLite, FTS5) | inside the Python code | — | memory, schedule, chats, notes, token counts, the vault |
| **TOML** | `config/airtac.toml`, each plugin's `manifest.toml` | 780 | your settings, and what each plugin is and may do |
| **JSON** | `plugins/desktop/apps.json`, test data, the web manifest | 900 | the apps catalog (one file for every AI and social app) |
| **PowerShell** and **Windows batch** (`.ps1`, `.cmd`) | `scripts/`, `Install-AIRTAC.cmd`, `AIRTAC-Advance.cmd` | 200 | the one-click installer, start/stop, shortcuts, model downloads |
| **SVG** | `web/logo.svg`, `web/logos/` | — | the AIRTAC logo and 30 app logos |
| **Markdown** | `README.md`, `docs/`, `knowledge/` | 950 | documentation, your notes and library |

## The server (Python packages, pinned in `requirements.txt`)

| Package | Version | What for |
|---|---|---|
| FastAPI | 0.143.0 | the web API (routes, request checking) |
| Uvicorn | 0.54.0 | runs the server |
| Pydantic | 2.14.0 | checks every request body and limits its size |
| httpx | 0.28.1 | every call to an outside service (8 s timeout, one retry) |
| python-dotenv | 1.2.4 | reads `.env` if you use one |
| fpdf2 | 2.8.9 | makes PDFs (study notes, solutions, Work mode reports) |
| Pillow | 12.3.0 | draws diagrams (stacks, queues, trees) |
| pypdf | 6.20.0 | reads syllabus and book PDFs |
| rapidocr | 3.10.0 | text in photos and scanned pages (OCR), offline (Apache-2.0) |
| onnxruntime | 1.31.0 | runs the OCR models (MIT) |
| faster-whisper | 1.2.1 | speech to text for audio attachments, offline, Whisper "base" (MIT) |
| ddgs | 9.16.0 | web search with no key: DuckDuckGo, Bing, Brave, Google, Mojeek results pages (MIT) |
| python-docx, python-pptx, openpyxl | 1.2.0, 1.0.2, 3.1.5 | Word, PowerPoint and Excel files |
| psutil | 7.2.2 | battery, memory and processor for the System app |
| keyring | 25.7.0 | keeps API keys in Windows Credential Manager until the vault exists (then they move into it) |
| **cryptography** | 50.0.2 | the vault: AES-256-GCM per entry (Part D); reads the old scrypt + Fernet vault once to migrate it; signs the Google Cloud Test |
| **argon2-cffi** | 25.1.0 | the vault's Argon2id key from your super password (MIT; Part D) |
| numpy | 2.5.4 | fast meaning search in your knowledge library |
| winotify | 1.1.0 | Windows notifications |
| pytest | 9.1.1 | the tests |
| faster-whisper, piper-tts (optional) | 1.2.1, 1.8.0 | private speech to text and text to speech on the PC |

Standard library used heavily: `sqlite3`, `json`, `re`, `threading`, `subprocess` (fixed argument lists only,
never a shell), `ctypes` (Windows clipboard), `ipaddress` and `socket` (blocking private addresses), `hmac`,
`hashlib`, `secrets` (sign-in sessions), `xml.etree` (arXiv feeds, with an XML-bomb guard).

## The page (browser features, no libraries)

- **One HTML page**, views switched in JavaScript (Home, Web, Work, Research, AI apps, Social, Passwords, Dev tools,
  Settings, Connections, Brains, History, Files, and one page per app).
- Elements are built with `textContent`, never `innerHTML`, so text from a website or a model can't run as code.
- **Content Security Policy:** only AIRTAC's own scripts run; `frame-src` lists only the sites whose own headers allow tabs (sandboxed iframes, no referrer).
- **Service worker + web manifest:** installable on Windows and Android (PWA), with an offline shell.
- **Web Speech API:** talk and listen in the browser. **View Transitions API:** the iris effect when you switch modes.
  **Clipboard API**, used only when the PC can't copy a password itself. **`<dialog>`** for sheets and the passcode box.
- **localStorage:** small things only (theme, last tab, starred apps). Never a key or a password.

## Data and secrets

| What | Where | How it's protected |
|---|---|---|
| Memory, schedule, chats, notes, token counts | `data/airtac.db` (SQLite with FTS5) | stays on the PC; gitignored; in backups |
| Website logins (Passwords page) | `data/vault.db` | each field encrypted with **Fernet**; the key comes from your master passcode through **scrypt**; the passcode is never stored; never in a backup |
| API keys | the vault (`data/vault.bin`), sealed to your Windows account (DPAPI) | AES-256-GCM; dots on screen, Show 10 s and Copy 20 s need the super password; PC only |
| Your settings | `config/airtac.toml`, `data/settings.json` | no secrets in them |
| Other devices' sign-ins | SHA-256 of a random token in the database | 30-day cookie, HttpOnly, SameSite=Strict |

## The brains (AI models)

- **On the PC:** Ollama running **NVIDIA Nemotron 3 Nano 4B** (default), with Qwen 3.5 and Qwen 2.5 Coder as
  options, MedGemma for medicine questions, and nomic-embed-text for library search.
- **In the cloud (optional, by API key):** Groq, Nebius Token Factory (Nemotron 3 Super), NVIDIA build,
  OpenRouter, Hugging Face, Cerebras, Cohere, Z.ai, Google Gemini, Mistral, DeepSeek, Claude (Anthropic),
  OpenAI and xAI. All are called through the same **OpenAI-compatible chat API**. They take turns automatically
  (failover), with free ones first and Ollama as the last stop. The full list and where to get each key is in
  [AI_BRAINS_API_KEYS.md](AI_BRAINS_API_KEYS.md).
- **Perplexity (Sonar)** is a tool, not a brain: answers from the web with sources.

## Outside services it reads

| Service | For | Key? |
|---|---|---|
| LeetCode (public GraphQL) | your profile and the daily problem | no |
| Hacker News (Algolia API), arXiv, Codeforces | News around you | no |
| DuckDuckGo, Wikipedia, your own SearXNG | web search | no |
| Jina Reader (`r.jina.ai`) | reading a page, tweet, Reddit thread or video as text (Agent Reach) | no |
| Tavily | better web search | yes |
| GitHub REST API | your repos and issues | yes (fine-grained token) |
| Perplexity Sonar | answers with sources | yes (paid) |

## On Windows

- **Google Chrome with AIRTAC's own profile** (`data\chrome_profile`), so you stay signed in to the sites AIRTAC opens.
- **Windows clipboard through `ctypes`:** a copied password is cleared after 30 seconds and kept out of clipboard history.
- **Credential Manager** (API keys), **toast notifications** (winotify), and **`os.startfile`** for apps you allow.
- **Optional programs:** Ollama, rclone (cloud backup), Tailscale (phone access), Docker (for Strix).

## Building and checking it

| Tool | Used for |
|---|---|
| **pytest** | 273 offline tests. No network and no model are needed: the tests use recorded answers and keyword rules |
| **Playwright + Chromium** (headless) | checking every page at phone width (390 px) for overflow and console errors |
| **Bandit** 1.9.4 | static security scan of the Python code |
| **pip-audit** 2.10.1 | known vulnerabilities in the pinned packages (none found) |
| **gitleaks** 8.30.1 | secrets in the git history or folder (`tools\gitleaks\`; MIT) |
| `scripts/security_check.py` | runs pip-audit, bandit and gitleaks together: PASS, FAIL or SKIP for each |
| **Strix** (optional, on your PC) | an AI agent that tests AIRTAC's web routes for weaknesses (`scripts/strix_scan.py`; needs Docker and an LLM key) |
| **Simple Icons** (npm package, CC0) | the 30 app logos, saved once by `scripts/make_logos.py` |
| **Claude** (Anthropic), as a pair programmer | planning, writing and reviewing the code and docs, with you deciding and testing |
| **Claude Code + claude-mem, Agent Reach** (optional) | set up from the Dev tools page; `scripts/mcp_server.py` links AIRTAC to Claude Code over MCP |
| **Git and GitHub** | version control; you push it yourself |

## Design rules that shaped the choices
- **An Intel i3 with 8 GB of RAM:** one local model in memory at a time, SQLite instead of a database server, and no frontend build.
- **Local first:** with no keys set, nothing leaves the PC.
- **Everything is a plugin.** A new feature is a folder with a `manifest.toml` that states which websites it may reach and whether it can change anything.
- **Anything that changes something outside AIRTAC waits for your Confirm.**
