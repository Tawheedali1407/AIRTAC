# AIRTAC Advance: user manual

## Start here
- **Open AIRTAC:** the AIRTAC Advance icon on the desktop or Start menu. The server starts by itself when you sign in to Windows (Startup shortcut), so the app is always ready.
- **If a page says AIRTAC isn't answering:** double-click `restart_airtac.bat` in `F:\AIRTAC Advance`, or Settings > AIRTAC server > Restart AIRTAC.
- **If something is missing or broken:** double-click `repair_airtac.cmd`. It repairs pip, installs what's missing and runs the doctor (a full check, ending with what to fix).
- **Keys:** press `/` to type, `Esc` to stop, `1`-`9` (with the menu open) for the first menu items.
- **Updates:** after an update, restart AIRTAC once. The installed app reloads itself with the new version.

## Assistant
### Main chat (Home)
- Ask anything in the box. Answers **type out live** like ChatGPT, with headings, lists, tables and code. The **Listen** button (or Settings > Voice) reads the first sentences aloud.
- **Mic:** click to talk, or hold and let go. **Globe:** search the web for what you typed. **Paperclip:** attach PDFs, Word, slides, sheets, photos (read by OCR) or audio (transcribed on this PC).
- **The header under the box:** pick the brain (Automatic, your PC's model, AIRTAC 1, Gemini, Groq...), the plugins the chat may use, and see the tokens used.
- **Was this right?** Thumbs down asks what the right answer was and keeps it as a lesson for next time.
- Anything that changes something outside AIRTAC (an email draft, a GitHub issue, a calendar event) waits for your **Confirm**.

### Chats
- Menu > Chats: every conversation on the left (search, rename, delete), like ChatGPT. **Regenerate** the newest answer, **Edit** any message you sent, **Stop** with Esc.
- Pick a **section** (Research, Coding tutor...) in the header: its instructions, plugins and documents go with every message.

### Work mode
- Give a goal ("research free GPU options for students and write me a PDF"). AIRTAC plans the steps, runs them, asks before anything risky, and saves the result in Files.

## Research and learning
### Research
- A writing desk with your notes on the left, AI helpers (summarise, find sources, outline) and Focus mode for distraction-free writing.

### Reverse engineer anything
- Menu > Research and learning > **Reverse engineer anything** (Research > Reverse engineering). Paste a **GitHub address** (or pick a project already cloned) and press Analyse. AIRTAC clones it on this PC, only reads it (nothing is run) and shows: languages and lines, AI libraries and frameworks, entry points, dependencies, tests and the **licence** (what you may reuse), then a lean plan and a ready prompt to build your own. Tick **Save as notes** to keep it in Research.
- In any chat: "reverse engineer https://github.com/ollama/ollama" or "how is https://site.com built?" (websites too).
- Compiled apps (.exe, .apk): REA (github.com/morluto/rea) does that, but it needs macOS, Node 22 and a paid Hopper licence, so not on this PC. Only study software you own or whose licence allows it.

### Web
- Search with your favourite engine and read pages inside AIRTAC ("Summarise this page").

### Your sections
- Settings > **Sections** > Add section: a name, an icon, instructions, the plugins it may use and up to 10 documents it always reads. Starters: Research, Coding tutor, Exam prep. Each appears in this menu group.

## Plan and people
### Schedule
- Add anything ("remember my exam is on 12 Nov", "remind me to pay the fee tomorrow at 6 pm") or just mention a date in a chat. Repeating items (birthdays) are added once. Import a calendar file or Google Calendar.
- The **morning greeting** says the time, the weather, your day in time order ("Today at 11 am: workshop, in 1 hour 40 minutes"), and top news.

### People and pipeline
- **Board:** applications, projects or anything with stages; drag cards between stages (Stages renames them).
- **People:** contacts with role, company, email, phone, tags and notes. Open one to see its **history** and add a note, call, meeting or email.
- **Follow-ups:** set a date on a person or item; it goes on your Schedule, so the greeting reminds you.
- In chat: "note for Priya: sent my resume", "follow up with Rahul next Friday", "who should I follow up with?"

## Apps and files
### AI apps, Apps, Social, Tabs
- Your AI tools and apps with their logos. Sites that allow it open as tabs inside AIRTAC; the others open in your browser.

### Files and History
- **Files:** everything AIRTAC made (PDFs, slides, Word, diagrams). **History:** every question, by date or by chat; delete single questions or whole chats.

## Connect
### Connectors
- Gmail, Google Calendar, Google Drive, GitHub and Notion. Press **Connect**; Google ones need the one-time Google setup (docs\GOOGLE_SETUP.md: your own client, then enable the Gmail, Calendar and Drive APIs).
- Then ask: "any emails from college today?", "what's on my calendar this week?", "find my DBMS notes in Drive", "find my project plan in Notion". AIRTAC never sends mail: it saves drafts for you to send.

### APIs and keys, Brains and plugins
- **APIs and keys:** paste free keys (Groq, Gemini, OpenRouter...) to give AIRTAC fast cloud brains; they're kept in your vault.
- **Brains and plugins:** which brain answers which kind of question, and switch plugins on or off.

## Safety and system
### Passwords (the vault)
- One **super password** protects every login and key (Argon2id + AES-256). Keep the **recovery key** somewhere safe. The vault only opens on this PC.

### Settings
- **About you** (every chat knows it), **Sections**, **Theme** (11 themes), **Dictation**, **Voice**, **Weather** town, **Chat** limits, **Account** (Google sign-in, Drive sync), **Restart AIRTAC**.

### Dictation (like Wispr Flow)
- Settings > Dictation on. Hold **Right Ctrl** in any Windows app, speak, let go: the words are typed where the cursor is. Whisper runs on this PC, so your voice never leaves it.

### Dev tools
- Install and check developer tools with fixed, safe commands (PC only).

## Your phone
### Get AIRTAC on the phone
- Settings > **Get AIRTAC on your phone** > **Turn on phone access**, then scan the **QR code** with the phone's camera (same Wi-Fi). Chrome warns about the certificate once: Advanced > Proceed.
- Until the Android app is built, the code opens the web app: menu (three dots) > **Add to Home screen**.
- **Airtac Mobile (Android):** push the project to GitHub and run the "Build APK" workflow (docs\MOBILE.md), put `airtac.apk` in `mobile\release\`, and the same QR code downloads it. Then **Pair with Airtac Mobile** and scan the pairing code in the app. The app has the **scanner** (documents and photos straight into AIRTAC), Share to AIRTAC, chat and your day.

## AIRTAC 1 (your own AI)
### Start and use it
- In `F:\Airtac1`: `setup.bat` once, then `start_airtac1.bat` (or `scripts\enable_autostart.cmd` to start it at sign-in).
- In the brain menu pick **AIRTAC 1 > Automatic**. It runs your PC's model with the AIRTAC persona; when it can't (Ollama off, low memory) AIRTAC Advance's cloud brains answer and the reply says so.
- Next steps (RAG over your documents, Tool Bridge, Model Lab): docs\AIRTAC1_TWO_SESSIONS.md.

## When something goes wrong
### Quick fixes
- **No answers:** open Ollama (or check `ollama list` shows nemotron-3-nano:4b); add a cloud key on APIs and keys so a cloud brain can answer when the PC's model can't.
- **Vault won't open / "package missing":** `repair_airtac.cmd`, then Restart AIRTAC.
- **Everything else:** `repair_airtac.cmd` ends with the doctor's report; the log is `data\logs\server.log`.
