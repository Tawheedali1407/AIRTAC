# Changelog

## Greeting fix (18 Oct 2026)
- The greeting now says the time and date, the weather (new Weather plugin, Open-Meteo), today's timed items in order with how long until each (the 11 am session was missed before), festivals by name, and India's top news before AI news.
- Phone layout switch: Fit to screen or Desktop layout (scroll sideways).

## Parts G and H (18 Oct 2026): Airtac Mobile, pairing, QA loop
- **Airtac Mobile** (mobile/, Flutter): pairing by QR, chat with brain picker and share-sheet attachments, offline queue, AIRTAC 1, Today with Listen, Learn and lectures (Android voice), Schedule with phone reminders, Search everything, Apps, Full app web view, fingerprint lock. Built by GitHub Actions (mobile/ci/build_apk.yml).
- **Pairing:** Get mobile app (Main chat header), Settings > Devices. HTTPS phone listener on 8766 only while switched on, pinned self-signed certificate, one-time codes, device tokens, Remove; Google account match when the PC is signed in. API keys are now PC-only even to look at.
- **QA:** scripts/qa_crawl.py; fixes from it: no favicon requests that can only fail, aborted page animations handled; clearer answer when every brain fails (Ollama sees no models / cloud brains busy); briefing summaries retried when the brains were busy; log rotation test.
- **Docs:** FIX_REPORT.md, MOBILE.md, INSPIRATION.md, ARCHITECTURE.md (ports, API contract, modules).

## Parts E and F (17 Oct 2026): persona, learning plan, lectures, briefing, planner; Google sign-in, repositories, plugins
- See docs/STATUS.md (Parts E and F) and docs/MANUAL.md.

## Part D (17 Oct 2026): vault v2, APIs and keys, model picker, hardening
- **Vault v2:** one vault for logins and API keys. Super password -> Argon2id -> AES-256-GCM, a recovery key, locked by default (5 min, hidden window, Lock), 5 tries then 30 s doubling to 1 h, tamper detection, Show 10 s, Copy 20 s, audit log. The 0.4 vault migrates on the first unlock (data\ backed up first).
- **APIs and keys:** Brave, SerpAPI, Context7, CodeRabbit, Nebius Object Storage, SAP AI Core, SAP BTP, AWS S3, Cloudflare R2, Google Cloud Storage, Azure Blob, Dropbox, Supabase, Together AI (brain), custom keys with logo; search, groups, Test, last tested.
- **Model picker:** Local, AIRTAC 1, Google, Groq, xAI, OpenRouter free, Nebius, Hugging Face; key needed/set; provider:model picks remembered per chat; fallback notice. `[failover] local_first` (your PC first, a cloud brain only when it can't).
- **Hardening:** per-install API token cookie, backend plugin network guard, log redaction, scripts/security_check.py (pip-audit, bandit, gitleaks).
- **Clean-up:** History delete (questions, chats, all) and Settings > Memory and cache.
- **Work mode:** PDF, Word and PowerPoint buttons always shown, and a File picker before Start.

## 0.4.1 (14 Oct 2026)
- **Security review, with fixes:**
  - The web reader (Agent Reach) now blocks loopback addresses written in disguise: `http://2130706433/`, `http://127.1/`, `http://0x7f.0.0.1/` and IPv4-mapped IPv6 such as `::ffff:127.0.0.1`. Every host that isn't a plain IP is resolved before it's judged.
  - `/api/login` now counts towards the per-device limit for other devices, on top of the 10-minute lock-out after 5 wrong passcodes.
  - pip-audit is clean. Bandit shows only the documented items. Strix is ready to run from the Dev tools page; it needs Docker and an LLM key.
- **Home's Open tiles** show their logos even when you set your own tiles in config/airtac.toml.
- **Z.ai's free model** is now `glm-4.7-flash` (the old default name doesn't exist).
- **Docs:** [TECH_STACK.md](TECH_STACK.md) lists every language, library, service and tool used. [AI_BRAINS_API_KEYS.md](AI_BRAINS_API_KEYS.md) covers every brain, where to get its key and what it costs. [AI_APPS_AND_LOGOS.md](AI_APPS_AND_LOGOS.md) lists every AI and social app with its link and logo.
- **Checked on a phone:** all 13 pages at 390 px wide, with no sideways scrolling and no errors. **273 tests pass offline.**

## 0.4.0 (13 Oct 2026)
- **AI apps and Social, with logos.** Every app lives in one file, `plugins/desktop/apps.json`. Add one by copying an entry. Logos are open-licence SVGs from Simple Icons (CC0), saved once in `web/logos/` (`scripts/make_logos.py`), never fetched at run time; an app with no open logo shows a two-letter badge. Credits and the "logo missing" list are in `web/logos/CREDITS.md`.
  - **AI apps page:** Claude, Gemini, Groq, Perplexity, DeepSeek, Emergent, NotebookLM, Notewise, Manus, Higgsfield, Lovable and 25+ more, by group, searchable. On this PC they open in AIRTAC's Chrome profile.
  - **Social page:** YouTube, Facebook, Instagram, X, Google, LinkedIn. "Open" opens the site; "Sign in with saved login" copies your user name, opens the sign-in page, then copies the password from the vault. AIRTAC never types a password into a website and never sends it anywhere.
- **Passwords vault** (`plugins/passwords/`, on this PC only). Your logins for every app, encrypted with a master passcode AIRTAC never stores (scrypt + Fernet, in `data/vault.db`).
  - Locks after 5 minutes idle, after you press Lock, and when AIRTAC stops. 5 wrong passcodes mean a 10-minute wait.
  - Passwords are masked; "Show" needs the passcode again; a copied password is cleared from the clipboard after 30 seconds and kept out of Windows clipboard history. "Export encrypted backup" writes a file that opens only with your passcode.
  - Never sent to the AI model, the chat, a log, a PDF or a backup. The plugin has no tools, so the model can't reach it. It shows "Vault works on this PC only" on a phone or hosted copy.
- **More AI brains, by API key.** On top of Groq, Nebius, NVIDIA and OpenRouter, you can now add: Hugging Face, Cerebras (free tiers); Cohere, Z.ai GLM, Google Gemini, Mistral (free tiers that may train on what you send — add them to `[failover] order` yourself); and the paid DeepSeek, Claude (Anthropic), OpenAI and xAI. Adding a key is enough: free brains are tried before paid ones, and each paid brain starts with a daily token limit so a long chat can't run up a bill. **Perplexity (Sonar)** is added as a tool for answers-with-sources. Keyless requests from the model honour per-provider request shapes (Claude sends no temperature; OpenAI uses `max_completion_tokens`).
- **Chat header and picker.** The chat shows which brain answered (provider and model) and this session's tokens (in/out/total), with a warning past a limit you set. A side panel (Plugins button) picks the brain the chat asks first and which plugins it may use — Automatic, only the ones you tick, or none (just talk). A tool that wasn't offered can never run, so text on a web page can't make the model use a tool you switched off.
- **Settings page:** themes (now with a **high-contrast** option), the token warning, the default brain, which browser opens social sites, a masked table of your API keys with a Test button, and the vault's master-passcode change.
- **Agent Reach** (`plugins/agentreach/`): the chat can read a public page, tweet, Reddit thread or YouTube video as clean text (Jina Reader, no key). Install the open-source Agent Reach CLI from Dev tools for deeper reads (X search, subtitles, Reddit). Only public addresses: every private or local IP (IPv4 and IPv6) is refused.
- **Session memory** (`plugins/recall/`): AIRTAC remembers what each chat was about and brings up anything relevant from earlier chats, like claude-mem but local and built in. App chats are kept out of it.
- **Dev tools page:** set up Claude Code, claude-mem, Agent Reach and Strix on this PC, and link AIRTAC to Claude Code over MCP (`scripts/mcp_server.py` exposes `ask_airtac` and `airtac_read`). Each step shows its exact, fixed command and runs only on the PC when you press Run. **Strix** (`scripts/strix_scan.py`) scans AIRTAC's own address for weaknesses.
- **Focus writing** in Research: a distraction-free full-width editor (Esc to leave), plus a recent-notes sidebar.
- **Security pass (0.4):**
  - the Agent Reach reader blocks SSRF to every private/loopback/link-local/reserved address and resolves host names before deciding;
  - invalid requests never echo what was typed back (so a mistyped password or key can't leak in an error);
  - Dev tools setup commands are fixed in code and chosen by a whitelisted id, never taken from the request; the MCP bridge only ever talks to 127.0.0.1 and takes a digits-only port;
  - all vault, Dev-tools-setup and open-app routes are refused from anything but the PC itself;
  - Bandit and pip-audit are clean apart from two documented, guarded false positives (the arXiv XML-bomb guard and the loopback MCP call).
- Tests: **271 pass offline.**

## 0.3.0 (13 Oct 2026)
- **No sample data anywhere in the app.** LeetCode, news and your accounts are always live. When a site doesn't answer, the page says so. A feature whose key or model is missing says which connection to add, or does the part that works without it (exact diagrams; explaining your own solved problem from its steps). The sample/ folders are now used only by the offline tests. `[general] offline` is gone from config/airtac.toml.
- The "No model" badge is gone. The top bar shows the model's name only when a real model is answering.
- **Security pass:**
  - every page gets security headers and a strict Content Security Policy (only AIRTAC's own scripts run; the sign-in page's script moved to login.js);
  - changes sent from another website are refused (Origin check, on top of FastAPI reading only real JSON);
  - a request through a proxy (X-Forwarded-For) is never treated as this PC;
  - a new passcode signs every other device out;
  - other devices are limited to 120 brain requests an hour and 800 a day (`remote_per_hour`, `remote_per_day`);
  - the API docs pages are off;
  - arXiv XML with a DOCTYPE or entities is refused (XML bombs);
  - request fields on the Accounts, Desktop and Research routes are bounded;
  - pip-audit finds no known vulnerabilities in the pinned packages.

## 0.2.0 (12 Oct 2026)
- **Connections page:** keys are set up in the app, like plugins. One card per service:
  - the free cloud brains (Groq, Nebius, NVIDIA build, OpenRouter, Gemini),
  - Tavily, GitHub, Ollama on this PC and the phone passcode.

  Paste a key, Save, Test. Keys go to Windows Credential Manager and are never shown again (only the last 4 characters). They can only be changed on the PC itself, and take effect without a restart. A plugin adds its own card with `p.connector(...)`.
- **Free cloud brains with failover:** Groq, then Nebius, NVIDIA and OpenRouter, then Ollama on your PC as the last stop. A service that runs out of free requests (HTTP 429 or 402) rests, and the next one answers.
- **Credential vault and Accounts settings:**
  - website passwords and session cookies go to Windows Credential Manager, used only to call the site they belong to;
  - a persistent Chrome profile (data\chrome_profile) and launch tiles for YouTube, Instagram and Google.
- **Home:** your top 5 apps and an All apps button. Dates you mention in chat ("my DBMS exam is on 14 Nov") are added to your schedule, with Undo.
- **Research workspace:** notes on the left (Markdown, saved as you type), a co-pilot on the right (summarise, outline, analyse, find sources).
- **Phone:**
  - a "Start AIRTAC" button unlocks sound on the first tap (it shows on Home only);
  - the layout fits phone width.
- `scripts\check_live.py` also tests every connected service.
- **Connections looks like a board:**
  - small tiles side by side (2 across on a phone), each with a status light;
  - a row of lights at the top shows what's connected at a glance;
  - tap a tile to open its key sheet (it rises from the bottom on a phone);
  - filters for Brains, Services and This PC.
- **Start-up and mode switching:**
  - when AIRTAC opens, the element wheel assembles, the name decodes, the server and brain report in, and the doors open (tap or a key skips it; turn it off in the menu under Voice);
  - switching modes opens the new view like an iris from where you tapped, with a readout naming the mode;
  - Alt+1 to Alt+8 open the menu's pages, and `/` jumps to the question box.
- **AI apps:**
  - 34 in `plugins/desktop/ai_apps.json`: Claude, ChatGPT, Gemini, DeepSeek, Groq, Perplexity, NotebookLM, Notewise, Manus, Emergent, Lovable, Higgsfield, Runway, ElevenLabs, Hugging Face and more;
  - Home shows the starred ones and an "All 34" tile;
  - star or add your own in the side panel (saved on the PC), or with `ai_tiles` in config/airtac.toml;
  - on the PC they open in AIRTAC's Chrome profile, so you stay signed in.
- **Side panel**, like VS Code's side bar: an icon strip on the right edge (Ctrl+B).
  - Ask: a quick question from any page;
  - Web: search, and read a page as text right in the panel;
  - AI apps, Today and Tokens.

  On a phone, the panel button in the top bar opens it full screen.
- **Command palette (Ctrl+K):** type any mode, app, panel or AI app and press Enter. It opens with a HUD-style lock-on, and the menu shows each mode's Alt key.
- **Tokens plugin:**
  - counts every brain's tokens per day;
  - daily limits per cloud brain (Nebius 100,000 by default): over the limit, the brain is skipped until midnight;
  - warnings at 80% and 100%;
  - answer length Short, Normal or Long;
  - ask "How many tokens did I use today?".

  New core hooks: `@p.usage` and `@p.brain_gate`.
- **Install app** in the menu installs AIRTAC on the PC (Chrome or Edge) or the phone (over Tailscale's https).

## 0.1.0 (11 Oct 2026)
- First version: core and plugin system, local brains on Ollama (Nemotron 3 Nano 4B), knowledge library, Work mode, web search and reader, backup, phone passcode, PWA and installer.
