# AIRTAC Advance manual

One entry per feature, written as it was built: what it does, how to use it, files, config, troubleshooting.

## Starting, stopping and the offline banner
- **What:** AIRTAC is a server on your PC (port 8765) plus a page that talks to it. The page can open without the server (it keeps a saved copy), so a banner says when the server isn't running.
- **Use:** double-click `start_airtac.bat` (starts it, waits until it answers, opens the app). Stop with `scripts\stop.ps1`. To have it always ready, run `scripts\make_shortcut.ps1 -AtLogin` once: the server then starts hidden when you sign in.
- **Banner:** "AIRTAC's server isn't running" with the real error, **Start AIRTAC** (asks Windows to open the `airtac://start` link) and **Try again**. It checks every 4 seconds and disappears by itself when the server answers.
- **Files:** `scripts/serve.py`, `scripts/start.ps1`, `scripts/stop.ps1`, `scripts/make_shortcut.ps1`, `web/app.js` (`api()`, `connectionLost()`), `web/index.html` (`#conn-banner`).
- **Troubleshooting:** the log is `data\logs\server.log`. Error 10048 = port 8765 is taken by another program or an old AIRTAC: run `scripts\stop.ps1`, then start again.

## AIRTAC doctor
- **What:** checks Python, config, the server and its port, the database, plugin manifests, Ollama and models, which keys are set (never their values), disk and memory, and the last error in the log.
- **Use:** Dev tools → **Run doctor**, or `.venv\Scripts\python scripts\doctor.py` in a terminal (`--json` for machines).
- **Files:** `scripts/doctor.py`, `plugins/devtools/__init__.py` (`POST /api/plugins/devtools/doctor`, this PC only).

## News around you (Home)
- **What:** Hacker News, new arXiv papers on your topic and upcoming Codeforces contests. Free, no keys.
- **How it stays fast:** the three sources are fetched at the same time, 6 seconds each at most. A source that fails shows its last good copy, with when it was fetched and which site didn't answer. If nothing was saved yet, it says so. Nothing is made up.
- **arXiv fallback:** when arXiv's search is down, today's new cs.AI papers from its RSS feed (rss.arxiv.org) are shown, the ones matching your topic first.
- **Config:** the topic is `[plugins.news] topic` in config/airtac.toml (default "LLM agents").
- **Files:** `plugins/news/__init__.py` (`all_sections`, `parse_arxiv_rss`), `web/app.js` (`loadNews`, `drawNews`).

## Main chat and the menu
- **What:** Main chat (Home) holds only the chat: the question box, the brain and plugin picker, the answer, your day, News and Your progress. Every section and every app is in the left menu (☰).
- **Where things went:** Apps → menu "Apps" list (all of them, no "top 5"); AI tools → menu **AI apps**; Open (YouTube, Instagram, Google) → menu **Social**.
- **Files:** `web/index.html` (`#view-home`, drawer), `web/app.js` (`drawApps`).

## Search in Main chat and History
- **What:** typing in the question box suggests questions you asked before (and examples); the History page lists everything you asked. Both match **every word in any order** ("exam dbms" finds "When is my DBMS exam?") and highlight the matches.
- **Details:** waits 0.2 s after you stop typing; `%` and `_` are searched as themselves; "Nothing you asked before matches. Press Enter to ask it." when nothing matches.
- **Files:** `airtac/memory.py` (`search_messages`), `web/app.js` (`highlight`, `updateSuggest`, `loadHistory`).

## Your progress (Home)
- **What:** your day streak (days in a row with at least one question; it still shows in the morning before you ask), chats this week, and schedule tasks done, then LeetCode and the requests-per-day chart.
- **Empty state:** "Nothing yet. Ask your first question and your streak starts today." If it can't load, the real reason is shown.
- **Files:** `airtac/memory.py` (`you`), `airtac/main.py` (`/api/progress`), `web/app.js` (`loadProgress`).

## Phone, tablet and desktop
- **What:** the same page works at every width and installs as an app (PWA) on the phone and the PC.
- **Rules:** no sideways scrolling; on phones and touch screens every button, link-button, field and menu item is at least 44 x 44 px.
- **Checked:** all 31 pages (13 sections + 18 app pages) at 390, 768 and 1280 px wide, with Playwright.
- **Files:** `web/style.css` (the "Touch targets" block at the end).

## Apps catalog: AI apps, Apps and Social
- **What:** every app AIRTAC can open, in one file: `plugins/desktop/apps.json`. Three pages in the menu (AI apps, Apps, Social), grouped like a college portal, with search and group chips.
- **Use:** on this PC, **Add an app** or the pencil on a card (name, https address, page, group, about, how it opens). Remove is in the same form. Each save keeps a copy of the old list in `data\backups\apps`.
- **Opens:** "Open in tab" when the site allows it, else "Opens in browser ↗" (AI apps: AIRTAC's Chrome profile; Apps and Social: the browser chosen in Settings). "Other apps" open AIRTAC's own pages.
- **Logos:** `web/logos/<name>.svg` (Simple Icons, `scripts/make_logos.py`), else the site's favicon (kept in `data\favicons`), else a two-letter badge. Credits: `web/logos/CREDITS.md`.

## Tabs (sites inside AIRTAC)
- **What:** sites that allow it open as tabs inside AIRTAC. A site decides with its own headers (X-Frame-Options, CSP frame-ancestors); AIRTAC obeys and never strips or proxies them. Most big sites (Gmail, YouTube, Instagram, ChatGPT) say no, so they open in the browser.
- **Use:** "Open in tab" on a card, or **College view** on the Tabs page. Drag tabs (or Ctrl+Shift+← →) to reorder, Ctrl+W closes, Ctrl+Tab moves on. Tabs come back after a restart; only the 5 used last stay loaded.
- **Check again:** **Re-probe tabs** on any catalog page, or `.venv\Scripts\python scripts\probe_embed.py [--force] [ids]`. Answers are kept 7 days.
- **Note:** signing in inside a tab may not be kept (browsers limit cookies in frames). Use "Open in browser" then.
- **Files:** `web/tabs.js`, `plugins/desktop/embed.py`, `airtac/main.py` (frame-src), `airtac/plugin_api.py` (`frame_source`).

## Web search engines
- **What:** the Web page asks DuckDuckGo, Bing, Brave, Google and Wikipedia at once (**All results**, merged; each result says which engines found it) or one engine. No keys needed (`ddgs` package). Optional: your SearXNG (`searxng_url` in config/airtac.toml), Tavily and Google Programmable Search keys on the Connections page.
- **Default engine:** Settings → Default search engine (Google, Bing, DuckDuckGo, Brave, Perplexity): the "Search on ..." link opens it in the browser. The tab bar has a search box too.
- **Troubleshooting:** "Not this time: Bing too slow" means that engine didn't answer within 12 s; the others still show.

## Search everything (Ctrl+K)
- **What:** one box for your chats, research notes, knowledge library, schedule, files, apps and settings, plus every page and command. Read only, on this PC.
- **Use:** Ctrl+K (Cmd+K on a Mac), or the search button in the top bar on a phone. Filters: All, Commands, Chats, Notes, Knowledge, Schedule, Files, Apps, Settings. ↑ ↓ to move, Enter to open. With an empty box, your recent searches show.
- **Files:** `plugins/finder/` (`GET /api/plugins/finder?q=&kinds=`), `web/dock.js` (palette), `web/app.js` (`goToResult`).

## Attachments (the paperclip)
- **What:** attach files to any chat: Main chat, every app's chat, Research. PDF, PowerPoint, Word, Excel/CSV, text, calendar (.ics), photos and audio, up to 30 MB, 10 per message.
- **Use:** the paperclip, or drop files on the chat, or paste a screenshot. Each chip says Uploading, Reading, Ready (with pages) or why it couldn't be read. Click the name to see what AIRTAC read. Send asks wait until a file is Ready.
- **Reading:** photos and scanned PDF pages with OCR (RapidOCR, offline); audio with speech-to-text (faster-whisper, offline). Old .doc/.ppt: save as .docx/.pptx first.
- **Safety:** only those types; the first bytes must match the type; Office files are measured before opening (zip bombs refused); safe file names. Kept in data\uploads\<section>\.
- **Files:** `plugins/attachments/`, `web/attach.js`, `airtac/agent.py` (`attached_context`).

## Knowledge: cited search and GitHub collections
- **Search:** the box on the Knowledge page answers in your files' own words with [1] [2] marks and lists each passage with file, page and score.
- **Add a GitHub repository:** owner/name (e.g. mytechnotalent/Reverse-Engineering) and a collection name. Public repos only; only Markdown, text, PDF and HTML are kept (never programs); the licence goes in SOURCE.md. Runs in the background (a big tutorial takes minutes). Add it again to update.
- **Code:** `knowledge.search(query, k, collection=None)` -> text, file, page, score (one function, so AIRTAC 1 can swap the engine).

## Research: Ask and sub-brains
- **Ask** answers from your notes, attached files, your library (or the chosen sub-brain), About you, and then general knowledge, labelled "From general knowledge".
- **Sub-brain:** pick a collection (e.g. reverse-engineering) above the co-pilot; Ask then answers mainly from it, citing file and page.

## About you (Settings)
- Name, studies, college, interests, goals, other details. Every chat gets these lines, so "who is Ali?" works everywhere. Stored in AIRTAC's database on this PC.

## Work mode: report first, files on request
- A finished job shows its report in Work mode. **PDF, Word and PowerPoint buttons** are always under the job (usable once the report is ready), or say "convert to PDF" in a chat.
- **File** (next to Start): pick PDF, Word or PowerPoint before you start, and the file is made by itself when the report is ready.

## Schedule: import, calendar, ask
- **Import a file:** PDF, photo of a notice, .ics (Google Calendar or Outlook export), Word, Excel. Choose "Keep the file's dates", "Start next week" (for week-by-week plans) or a day. Check the preview, untick what you don't want, Add. Duplicates are skipped.
- **Views:** List or Calendar (month grid). **Ask your schedule:** "what's next week?", "when is my DBMS exam?" — answered only from your items.

## Brains and plugins
- **Models on this PC:** each downloaded model with size, memory it needs and the roles using it; pick a model per role (saved to config\airtac.toml with comments kept, a backup in data\backups\config; used from the next question).
- **Plugins:** switch on or off (after a restart); **Add a plugin** makes plugins\<id>\ from the template; a folder added while AIRTAC runs shows as "New: restart to load".

## Dev tools
- Each card shows its exact command; Run (press twice) runs it on this PC with the output appearing line by line. Run doctor checks everything.


## Passwords and APIs and keys: the vault (Part D)
- The first visit asks you to **create a super password** (twice, 12+ characters, strength meter) and shows a **recovery key** once: keep it safe, tick "I saved it". From 0.4: type your old master passcode once and the vault upgrades.
- **Locked by default.** Unlock with the super password; it locks after 5 minutes, when the window is hidden, or with Lock. Wrong tries: 5 free, then 30 s doubling to 1 hour.
- Secrets show as dots. **Show** = 10 seconds. **Copy** = PC clipboard, cleared after 20 seconds. Each is logged in data\logs\audit.log (never the value).
- Passwords page: search, Add a login (with **Generate**), Change super password, New recovery key, Export encrypted backup.

## APIs and keys
- Groups: Brains, Search and dev tools (Tavily, Brave, SerpAPI, Google, GitHub, Context7, CodeRabbit, Perplexity), Hackathon and cloud (Nebius Object Storage, SAP AI Core, SAP BTP, AWS S3, Cloudflare R2, Google Cloud Storage, Azure Blob, Dropbox, Supabase; Google Drive comes with Google sign-in), Custom, This PC. Search box and group chips at the top.
- Tap a card: paste the key, **Save**, **Test** (one read-only call; "What Test calls" links its documentation). Last tested time shows on the card.
- **+ Add custom API key:** name, service address, up to 4 fields (secret or not), a Test address and header, notes, a PNG/JPEG/WebP logo.

## Chat brain menu
- The model menu (chat header, or the side panel): **Automatic**, then Local (each Ollama model), AIRTAC 1 (not installed yet), Google Gemini, Groq, xAI, OpenRouter free models, Nebius, Hugging Face, and any other brain with a key. "key needed" groups can't be picked until you add the key.
- The pick is **remembered for this chat**. If it's busy or out of free requests, the next brain answers and the chat says "(fallback)".
- **Update the model lists** asks each brain with a key which models it offers (kept for a day).
- `[failover] local_first = true` in config\airtac.toml: your PC's model answers first; the brains in `order` (e.g. Gemini) answer only when it can't.

## History and Memory and cache
- **History:** Questions or Chats. Tick rows and **Delete selected**, or the X on a row (press twice). A question goes with its answer; a chat goes with its summary and token count. **Clear all history** asks you to type DELETE.
- **Settings > Memory and cache:** saved facts, chat summaries, notifications, tool-use counts, the web cache and brain model lists, each with a count, **View** (delete one by one) and **Clear** (press twice). Lessons, schedule, knowledge, files and the vault aren't touched.

## Security check
- `.venv\Scripts\python scripts\security_check.py` runs pip-audit, bandit and gitleaks (put gitleaks.exe in tools\gitleaks\). PASS, FAIL or SKIP for each.
- Every /api/ call carries this install's token (a cookie the page gets when it loads). Scripts on this PC send the X-AIRTAC-Token header (data\.install_token).

## Moving Ollama's models to F:
- `powershell -ExecutionPolicy Bypass -File "F:\AIRTAC Advance\scripts\move_ollama_to_f.ps1"`: quits Ollama, moves the models to F:\Ollama\models, sets OLLAMA_MODELS for your account, starts Ollama, and copies Ollama's log to tmp\ollama-server.log.

## Become an AI expert (your roadmap)
- Menu > Become an AI expert: this week's DSA, study and lab; the 7 stages with free links; a 10-question level check; tick weeks done.
- **Add the plan to Schedule** adds each week's lab, the mocks and the kickoff (once). **I fell behind** moves every unfinished item by a week.
- The plan is plans\learn_ai.json (edit it to change hours or weeks). The full roadmap is docs\ROADMAP_AGI.md and is in Knowledge.

## Lectures
- Menu > Lectures: pick an attached file (paperclip anywhere) or paste notes, choose One teacher or Two voices, Make it.
- You get a summary, key concepts, the lecture, 5 quiz questions (tap Answer), all with page numbers, from your file only. A copy goes to Knowledge.
- **Play / Pause / Stop**, speed and voice (this PC's voices work offline; Preview 3 voices). Save audio needs Piper.

## Your day: briefing, plan, check-in
- **Briefing:** builds by itself at 06:15 (or Build now): AI today + other industries, one line each with its link, only from news AIRTAC fetched. Offline it says so. Home's Today reads the top 3 headlines aloud.
- **Plan for today:** proposed around college and timed items, max 3 study blocks, 10% free; untick, then Accept into Schedule.
- **Evening check-in (21:30):** tick what got done; the rest moves to tomorrow. Settings: times, topics, quiet hours, switches (at most 3 messages a day).

## How AIRTAC talks
- Settings > How AIRTAC talks: Friendly (a smart, honest study buddy; the default) or Neutral. The same file (prompts\persona_v1.txt) is for AIRTAC 1 and the phone.
- Check it: `.venv\Scripts\python scripts\eval_persona.py` (10 prompts, answers in data\logs\persona_eval.md).

## Repeating items, festivals, tidy-up (Schedule)
- **Repeats:** Once, Every year (birthdays), Every month, Every week. Or say "add Ammi's birthday on 5 June every year". Google Calendar birthdays (.ics) import as yearly.
- **Add festivals and holidays:** this year's and next year's from Google's public "Holidays in India" calendar (press again next year).
- **Remove done items after:** never (default), 7, 30 or 90 days. Repeating items are never removed.

## Ollama on the CPU
- If chats said "Ollama's model runner crashed": `powershell -ExecutionPolicy Bypass -File "F:\AIRTAC Advance\scripts\ollama_cpu_only.ps1"` (sets OLLAMA_VULKAN=0: the old AMD GPU crashes Ollama's Vulkan runner).

## Account: Google sign-in, Drive sync, Calendar (Settings)
- One-time: make your Google client (docs\GOOGLE_SETUP.md) and paste it on APIs and keys > Google sign-in.
- **Sign in with Google** opens Google's page; come back to AIRTAC. Only the first account that signed in is allowed.
- **Switch on Drive sync** → **Upload to Drive now** (encrypted vault + encrypted settings), **Restore from Drive** on a new PC (docs\NEW_PC.md).
- **Switch on Google Calendar** → **Import Google Calendar** (next 90 days, read-only, duplicates skipped). **Sign out** revokes Google's access.

## Repositories
- Menu > Repositories: paste a GitHub address and Add (needs Git). Open a repo to see its README's install steps.
- **Run** (press twice to confirm) works only for pip, npm and git-submodule steps; pip goes into the repo's own venv. Other steps say "Run it yourself".
- **Check for updates** compares with GitHub. The Free AI APIs list links each key card.

## Claude Code plugins (Brains and plugins)
- 13 plugins (Superpowers, Context7, Code reviewer, ...), each with its real source, what it may reach and how to add it. Switch on the ones you use: Dev tools lists their commands.

## Airtac Mobile and pairing (Part G)
- **Get mobile app** (Main chat header): Pair a phone shows a QR code (2 minutes, works once), the APK link with its SHA-256, and the PWA alternative.
- **Settings > Devices:** Phone access on Wi-Fi (HTTPS, port 8766; off by default, Pair switches it on), paired phones with last seen, Remove.
- The app: Chat (pick a brain; share a file from any app to attach it; messages wait offline), Today (Listen), Learn (lectures with Android's voice), Schedule (reminders on the phone), More (AIRTAC 1, Apps, Search, Full app, This phone).
- Build: GitHub Actions > Build Airtac Mobile APK, then put airtac.apk in mobile\release\. Everything else: docs\MOBILE.md.
- Files: airtac/pairing.py, web/devices.js, mobile/ (the build: mobile/ci/build_apk.yml, copied to .github/workflows/). Config: `[devices] port`, `[account] android_server_client_id`.
- Trouble: can't reach the PC = check the Wi-Fi switch and Windows firewall (Private networks); "certificate doesn't match" = pair again.

## QA crawl (Part H)
- `.venv\Scripts\python scripts\qa_crawl.py` with AIRTAC running: opens every page and tab at desktop, tablet and phone size (light and dark) and lists only what's broken. Report: data\logs\qa_report.md. Add `--links` to check external links too (goes online).
- 0 findings = good. Each finding names the size, the page and what went wrong (an error, a failed call, sideways scrolling, a slow load, a server.log error).

## When no brain can answer
- The chat says why in one go: your PC's model (for example "Ollama is running but sees no models": point OLLAMA_MODELS at F:\Ollama\models and restart Ollama) and each cloud brain with a key (busy, tried again in N min), or that no cloud brain has a key yet.
- The morning briefing retries its summaries twice, 30 minutes apart, when the brains were busy.

## Page layout on a phone (Fit to screen / Desktop layout)
- Phones show the phone layout by default. Menu > **Desktop view (scroll sideways)** shows the full PC layout at 1280 px: scroll sideways and pinch to zoom. **Phone view** goes back. Also in Settings > Page layout on this device.
- Saved per device (the phone's browser or the app). In Airtac Mobile: Full app > **Desktop view** in the top bar.
- Files: web/view.js (runs before the page is drawn), Settings panel in web/index.html.

## The greeting, in order (fixed 18 Oct)
- **Clock:** "Good morning, Ali. It's 9:20 am on Sunday 18 October."
- **Weather** (plugins/weather, Open-Meteo, no key): now, feels like, today's low and high, chance of rain ("Take an umbrella" at 50% or more). Town: Settings > Weather (default Nandyal). Fetched every 30 minutes; left out when offline, never guessed. Ask "what's the weather tomorrow?" in chat.
- **Your day:** festivals and birthdays by name, then each timed item still ahead with its time and how long until it ("Today at 11 am: workshop, in 1 hour 40 minutes"), then untimed tasks. Passed items aren't announced.
- **Top news:** India's top 3 stories from Google News (refreshed every 30 minutes), then 2 AI news items (news articles before arXiv papers). News older than 3 hours isn't read as today's.

## Chat (like ChatGPT, 18 Oct)
- Menu > **Chat**: your chats on the left (Today, Yesterday...; search, rename, delete), answers that **type out live**, full
  answers with headings, lists, tables and code. Enter sends, Shift+Enter is a new line, **Stop** (or Esc) stops.
- Under an answer: **Copy**, **Read aloud**, **Regenerate** (the newest answer), thumbs up/down (a thumbs-down asks for the right
  answer and keeps it as a lesson). On your message: **Edit** (it and everything after it are replaced). Tool steps show as chips.
- The speaker in the header reads every answer aloud. Home's Ask box keeps the short spoken style.
- Files: web/chat.js, web/chat.css; POST /api/chat/stream (newline-delimited JSON), /api/conversations; airtac/llm.py STREAM.

## Sections (Settings > Sections)
- **Add section**: a name, an icon, instructions ("You are my research assistant..."), the plugins it may use (none ticked = AIRTAC
  picks) and up to 10 documents it always reads. Starters: Research, Coding tutor, Exam prep.
- Each section appears in the menu under **Your sections** and in the Chat page's section list. Files: plugins/sections, web/sections.js.

## People and pipeline (the schedule as a CRM)
- Menu > **People and pipeline**: a **Board** (drag cards between stages; Stages renames them), **People** (role, company, email,
  phone, tags, notes) and **Follow-ups** (overdue, today, this week).
- Open a person or item: its **history** (notes, calls, emails, meetings, stage changes), **Add a note**, **Follow up** on a date.
  A follow-up goes on your Schedule (category follow-up), so the greeting tells you.
- In any chat: "note for Priya: sent my resume", "follow up with Rahul next Friday", "who should I follow up with?".
- Files: plugins/crm, web/crm.js.

## Dictation (like Wispr Flow)
- Settings > **Dictation** on. Hold **Right Ctrl** (or the key you pick) in any Windows app, speak, let go: the words are typed
  where the cursor is. Whisper runs on this PC (models\whisper), so your voice never leaves it. Tidy-up drops "um", "uh".
- Starts with AIRTAC when it's on. Needs sounddevice and keyboard (repair_airtac.cmd installs them).
- Doesn't type into apps running as administrator (Windows blocks that). Files: scripts/dictate.py, plugins/dictation.

## Connectors (Gmail, Google Calendar, Drive, GitHub, Notion)
- Menu > **Connectors** > Connect. Google ones open Google's sign-in and ask only for that app's permission (first set up
  Google sign-in: docs/GOOGLE_SETUP.md). Notion: make an internal integration, paste its secret on APIs and keys, and share
  pages with it in Notion. GitHub: the token on APIs and keys.
- Then ask in any chat: "any emails from college today?", "what's on my calendar this week?", "find my DBMS notes in Drive".
- AIRTAC only reads. A Gmail **draft** or a **calendar event** waits for your Confirm; AIRTAC never sends mail.
- "Use in chats" switches one off without disconnecting. Files: plugins/connectors, web/connectors.js.

## Themes and motion
- 11 themes now: Crimson, Day, High contrast, Arc, Gilded, **Aurora, Forest, Sunset, Ocean (light), Midnight, Sakura (light)**.
  Settings > Theme or the menu. Pages rise in gently and a soft glow drifts behind them; Windows "reduce motion" stops it all.

## Updates reach the installed app by themselves
- The app checks every file with AIRTAC each time it opens, and reloads itself when AIRTAC was updated (not in the middle of an
  answer). If it ever looks old: close and reopen it once.

## AIRTAC 1 in the brain menu
- When AIRTAC 1 runs (F:\Airtac1, start_airtac1.bat), the chat's brain menu lists **AIRTAC 1 > Automatic** and its models.
  When it doesn't, the menu says "AIRTAC 1 isn't running". AIRTAC 1 uses AIRTAC Advance's cloud brains when its own model
  can't answer, with its own token (data\airtac1.token, local only, two routes). See F:\Airtac1\docs\MANUAL.md.

## Chat: pick the model (20 Oct, Bug 1)
- The Chat page has its own **model picker** in its header (Home keeps its own). It lists Local (every model `ollama list`
  shows), AIRTAC 1 and each cloud brain, with "key needed" or "key set". The pick is remembered **per chat**, and the line
  under each answer says which model answered.
- Local empty? It says **"no models found — check Ollama"** and why (Ollama not answering, or no chat model: `ollama pull`).
- The menu answers at once: model lists come from the saved copy and update in the background once a day
  (Setup > "Update the model lists" fetches them now). Files: airtac/providers.py (menu), web/hub.js, web/chat.js.

## Chat: never frozen (20 Oct, Bug 3)
- While it works the answer says **"Thinking… 12 s"**. **Stop** (or Esc) cancels the model call at once: the stream to
  Ollama is closed, so it stops using the CPU and the next question isn't stuck behind it.
- A model that hasn't started in 45 s, or isn't done in 90 s, is stopped with a plain message, plus buttons to ask a cloud
  brain that has a key (the key stays on this PC). With Automatic and a cloud key, the cloud answers by itself.
- Ollama gets keep_alive "2m" and a 3,072-token context in chats. Change all four in config/airtac.toml [llm] chat_*.
- Files: airtac/llm.py (Guard), airtac/agent.py, airtac/main.py (/api/kill, "wait" lines), web/chat.js.

## Reverse engineering finds a repo itself (20 Oct, Bug 2)
- Research > Reverse engineering > type what you want ("a Jarvis-style voice assistant in Python") > **Find a repo and do it**.
  It searches GitHub (public, open-source licence, 50+ stars, active in 2 years, not huge), shows which repo and why (and
  2-3 others to switch to), shallow-clones it into repos\ (gitignored; a 50 MB zip when Git is missing), reads it, and
  does your task with it as the reference. Steps shown: searching, chosen, cloning, reading, writing, or "couldn't find one".
- Repo text is untrusted: it's fenced in the prompt and never followed. Nothing from the repo runs; nothing is pushed.
- In any chat, "reverse engineer <topic>" finds a repo the same way and reads it through GitHub's API (no clone).
- GitHub allows 10 searches a minute without a token: results are kept 10 minutes; add GITHUB_TOKEN for more.

## AIRTAC opening by itself (24 Oct)
- Cause 1 (seen in data\logs\server.log): AIRTAC takes ~20 s to load and the hidden launcher shows nothing, so a second
  double-click (or the sign-in shortcut, or the Start AIRTAC button) started a 2nd and 3rd server and, ~20 s later, one
  window per click. scripts\start.ps1 now lets one launcher run at a time, waits for a server that is still loading,
  and doesn't open a second window when another launcher just opened one.
- Cause 2: after Windows shut down with AIRTAC open, Edge offered to restore the window. The window now starts with
  --hide-crash-restore-bubble. If it still reopens after a restart: Settings > Accounts > Sign-in options > turn off
  "Automatically save my restartable apps". The sign-in shortcut starts only the server (no window).
- Also: the WinError 10054 traceback spam in server.log is filtered (scripts\serve.py); Dictation stops polling while
  the window is hidden (web\dictation.js).
