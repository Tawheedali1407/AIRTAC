# Dev tools: Claude Code, claude-mem, Agent Reach, Strix, and the MCP link

The Dev tools page (menu → Dev tools) sets up developer tools on the PC running AIRTAC. Each step shows
its **exact command** and runs only when you press **Run**; the command is fixed in AIRTAC's code and
chosen by a name, so nothing a web page or the model says can turn into a command on your machine. These
routes work **only on this PC**, never from a phone.

## What you can set up

| Tool | What it is | Command AIRTAC runs | Needs |
|---|---|---|---|
| **Claude Code** | Anthropic's coding agent for your terminal | `npm install -g @anthropic-ai/claude-code` | Node + npm |
| **claude-mem** | Memory across sessions *for Claude Code* (AIRTAC has its own session memory built in) | `npx -y claude-mem install` | Claude Code |
| **Agent Reach** | Deeper reads for the Agent Reach plugin (X search, YouTube subtitles, Reddit) | `pip install …agent-reach…` then `agent-reach install --env=auto` | — |
| **Strix** | An open-source AI agent that pen-tests a web app | `pipx install strix-agent` | Docker |
| **Link AIRTAC to Claude Code** | Adds AIRTAC as an MCP server | `claude mcp add --scope user --transport stdio airtac -- <python> scripts/mcp_server.py` | Claude Code |

## The AIRTAC ↔ Claude Code link (MCP)

`scripts/mcp_server.py` is a tiny MCP server (stdio, no extra package) that forwards to AIRTAC's local
API at `http://127.0.0.1:<port>` and exposes two safe, read-only tools:

- `ask_airtac(question)` — ask AIRTAC, which uses its own brains, plugins and your knowledge library;
- `airtac_read(url)` — read a public page/tweet/thread/video as clean text.

It never exposes anything that changes your machine, your keys or the vault, and it only ever talks to
`127.0.0.1`. Keep AIRTAC running for the link to work.

## Scanning AIRTAC with Strix

Strix needs **Docker** running and an **LLM key**. In a terminal:

```
set STRIX_LLM=openrouter/z-ai/glm-4.6
set LLM_API_KEY=<your key>
python scripts/strix_scan.py --passcode <your AIRTAC passcode, if you set one>
```

It points Strix at AIRTAC on `http://localhost:8765` and saves a report under `strix_runs/`. **Only scan
AIRTAC on your own PC.** Read the report, then fix what it finds (AIRTAC can help).
