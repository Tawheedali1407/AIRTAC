"""Dev tools: set up Claude Code and friends on this PC, and link them to AIRTAC.

What you can set up from the Dev tools page (each is optional, and only on the PC running AIRTAC):
- Claude Code: Anthropic's terminal coding agent.
- claude-mem: gives Claude Code memory across sessions (the installer registers its hooks).
- Agent Reach: the deeper reads for the Agent Reach plugin (X search, YouTube subtitles, Reddit...).
- Link AIRTAC to Claude Code: adds AIRTAC as an MCP server, so inside Claude Code you can ask AIRTAC
  (ask_airtac) and have it read pages (airtac_read). Uses scripts/mcp_server.py.

How it runs safely:
- These commands install software and reach the internet, so they only run on the PC itself (local_only),
  and the page shows you the exact command and asks you to press Run (your click is the confirm).
- The command for each tool is fixed here in code: the page only names which tool, never the command,
  so nothing a web page or the model says can turn into a command on your machine.
"""
import os
import shutil
import subprocess
import threading
import sys
from pathlib import Path

from airtac import config
from airtac.plugin_api import plugin

p = plugin(__name__)
WIN = sys.platform == "win32"


def _cmd(name: str) -> str | None:
    return shutil.which(name) or (shutil.which(name + ".cmd") if WIN else None)


def python_exe() -> str:
    return sys.executable


# Each tool: the exact command (a fixed argument list), how to check it's there, and a note.
def _tools() -> dict:
    py = python_exe()
    mcp_script = str((config.ROOT / "scripts" / "mcp_server.py").resolve())
    claude = _cmd("claude")
    return {
        "claude-code": {
            "name": "Claude Code", "need": ["node", "npm"],
            "about": "Anthropic's coding agent in your terminal.",
            "cmd": [_cmd("npm") or "npm", "install", "-g", "@anthropic-ai/claude-code"],
            "check": lambda: bool(_cmd("claude")), "get": "https://claude.com/claude-code",
            "after": "Then run `claude` in a terminal and sign in once."},
        "claude-mem": {
            "name": "claude-mem", "need": ["claude-code"],
            "about": "Memory for Claude Code: it remembers earlier sessions. (This is for Claude Code; AIRTAC has its own session memory built in.)",
            "cmd": [_cmd("npx") or "npx", "-y", "claude-mem", "install"],
            "check": lambda: (Path.home() / ".claude-mem").exists(), "get": "https://docs.claude-mem.ai",
            "after": "Start a new Claude Code session; it loads context automatically."},
        "agent-reach": {
            "name": "Agent Reach", "need": [],
            "about": "Deeper reads for the Agent Reach plugin: X search, YouTube subtitles, Reddit and more.",
            "cmd": [py, "-m", "pip", "install", "--upgrade", "https://github.com/Panniantong/agent-reach/archive/main.zip"],
            "then": [[str((Path(py).parent / ("agent-reach.exe" if WIN else "agent-reach"))), "install", "--env=auto"]],
            "check": lambda: bool(_cmd("agent-reach") or (Path(py).parent / ("agent-reach.exe" if WIN else "agent-reach")).exists()),
            "get": "https://github.com/Panniantong/agent-reach",
            "after": "Run Doctor to see which channels are ready; some need a sign-in cookie."},
        "strix": {
            "name": "Strix (security scan)", "need": ["docker"],
            "about": "An open-source AI agent that pen-tests a web app. Use it to scan AIRTAC's own address on this PC "
                     "and find weaknesses. Needs Docker running and an LLM key (set STRIX_LLM and LLM_API_KEY).",
            "cmd": [_cmd("pipx"), "install", "strix-agent"] if _cmd("pipx") else [py, "-m", "pip", "install", "--upgrade", "strix-agent"],
            "check": lambda: bool(_cmd("strix")), "get": "https://github.com/usestrix/strix",
            "after": "In a terminal: set STRIX_LLM and LLM_API_KEY, then run  python scripts/strix_scan.py  to scan AIRTAC on this PC. It saves a report under strix_runs/."},
        "mcp-link": {
            "name": "Link AIRTAC to Claude Code", "need": ["claude-code"],
            "about": "Adds AIRTAC as an MCP server, so inside Claude Code you can ask AIRTAC and have it read pages.",
            "cmd": [claude or "claude", "mcp", "add", "--scope", "user", "--transport", "stdio", "airtac",
                    "--", py, mcp_script],
            "check": lambda: _mcp_linked(), "get": "",
            "after": "In Claude Code, the tools ask_airtac and airtac_read now work (keep AIRTAC running)."},
    }


def _mcp_linked() -> bool:
    cfg = Path.home() / ".claude.json"
    try:
        return cfg.exists() and '"airtac"' in cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _present(need: list[str]) -> bool:
    for n in need:
        if n == "node" and not _cmd("node"):
            return False
        if n == "npm" and not _cmd("npm"):
            return False
        if n == "claude-code" and not _cmd("claude"):
            return False
    return True


def shown_cmd(cmd: list[str]) -> str:
    """The command as the user would type it (for the page). Paths with spaces are quoted."""
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def status() -> dict:
    env = {k: bool(_cmd(k)) for k in ("node", "npm", "git", "docker", "claude")}
    env["python"] = True
    tools = []
    for tid, t in _tools().items():
        try:
            installed = bool(t["check"]())
        except Exception:
            installed = False
        tools.append({"id": tid, "name": t["name"], "about": t["about"], "installed": installed,
                      "ready": _present(t["need"]), "needs": t["need"], "get": t["get"], "after": t["after"],
                      "command": shown_cmd(t["cmd"]) + ("".join("  &&  " + shown_cmd(c) for c in t.get("then", [])))})
    return {"env": env, "tools": tools}


def run(tool_id: str) -> dict:
    """Run one tool's fixed setup command(s) on this PC. Returns the exit code and the tail of the output."""
    t = _tools().get(tool_id)
    if t is None:
        return {"ok": False, "output": "No such setup step."}
    if not _present(t["need"]):
        return {"ok": False, "output": "Set up what it needs first: " + ", ".join(t["need"]) + "."}
    steps = [t["cmd"], *t.get("then", [])]
    out = []
    for cmd in steps:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,  # noqa: S603 (fixed command list, no shell)
                               cwd=str(config.ROOT), env={**os.environ, "PIP_BREAK_SYSTEM_PACKAGES": "1"})
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "output": "\n".join(out) + f"\nCouldn't run it: {type(e).__name__}. Is the tool it needs installed?"}
        out.append(f"$ {shown_cmd(cmd)}\n{(r.stdout or '')[-1500:]}{(r.stderr or '')[-1500:]}")
        if r.returncode != 0:
            return {"ok": False, "output": "\n\n".join(out)[-4000:]}
    try:
        installed = bool(t["check"]())
    except Exception:
        installed = False
    return {"ok": True, "installed": installed, "output": "\n\n".join(out)[-4000:], "after": t["after"]}


# ---------------------------------------------------------------- live output

# One run at a time; its output lines are kept here so the page can show them as they come (it asks for
# the lines after the last one it has, every second).
LIVE = {"tool": "", "lines": [], "done": True, "ok": None, "installed": None, "after": ""}
_live_lock = threading.Lock()


def start_live(tool_id: str) -> dict:
    """Start a tool's fixed setup command(s) in the background, with the output streamed to LIVE."""
    t = _tools().get(tool_id)
    if t is None:
        raise ValueError("No such setup step.")
    if not _present(t["need"]):
        raise ValueError("Set up what it needs first: " + ", ".join(t["need"]) + ".")
    with _live_lock:
        if not LIVE["done"]:
            raise RuntimeError(f"{LIVE['tool']} is still running. Wait for it to finish.")
        LIVE.update(tool=tool_id, lines=[], done=False, ok=None, installed=None, after="")

    def work():
        ok = True
        for cmd in [t["cmd"], *t.get("then", [])]:
            LIVE["lines"].append(f"$ {shown_cmd(cmd)}")
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,  # noqa: S603 (fixed list, no shell)
                                        cwd=str(config.ROOT), env={**os.environ, "PIP_BREAK_SYSTEM_PACKAGES": "1"},
                                        errors="replace")
                for line in proc.stdout:
                    LIVE["lines"].append(line.rstrip()[:400])
                    del LIVE["lines"][:-2000]                 # keep the last 2,000 lines
                code = proc.wait(timeout=900)
            except (OSError, subprocess.SubprocessError) as e:
                LIVE["lines"].append(f"Couldn't run it: {type(e).__name__}. Is the tool it needs installed?")
                ok = False
                break
            if code != 0:
                LIVE["lines"].append(f"(stopped: exit code {code})")
                ok = False
                break
        try:
            installed = bool(t["check"]()) if ok else False
        except Exception:
            installed = False
        LIVE.update(ok=ok, installed=installed, after=t["after"] if ok else "", done=True)
    threading.Thread(target=work, daemon=True, name="devtools-run").start()
    return live(0)


def live(after: int = 0) -> dict:
    return {"tool": LIVE["tool"], "lines": LIVE["lines"][after:], "next": len(LIVE["lines"]), "done": LIVE["done"],
            "ok": LIVE["ok"], "installed": LIVE["installed"], "after": LIVE["after"]}


def doctor() -> dict:
    """Run scripts/doctor.py (a fixed command, read-only checks) and return its report."""
    import json
    try:
        r = subprocess.run([python_exe(), str(config.ROOT / "scripts" / "doctor.py"), "--json"],  # noqa: S603
                           capture_output=True, text=True, timeout=180, cwd=str(config.ROOT))
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "checks": [], "error": f"The doctor couldn't run: {type(e).__name__}."}
    lines = (r.stdout or "").strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError):
        return {"ok": False, "checks": [], "error": "The doctor stopped early: " + (r.stderr or "no output")[-800:]}


@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Request
    from pydantic import BaseModel, Field

    from airtac.plugin_api import local_only
    from plugins import agentreach

    class Setup(BaseModel):
        tool: str = Field(max_length=40, pattern=r"^[a-z0-9-]+$")

    @router.get("/status")
    def get_status(request: Request):
        from airtac import auth
        if not auth.is_local_request(request):
            return {"here": False, "message": "Dev tools set up software on the PC running AIRTAC, so this page only opens there."}
        st = status()
        st["here"] = True
        st["reach"] = agentreach.doctor()
        return st

    @router.post("/setup")
    def setup(body: Setup, request: Request):
        local_only(request)
        return run(body.tool)

    @router.post("/run")
    def run_live(body: Setup, request: Request):
        """Start a setup step with live output (the page reads it from GET /run)."""
        local_only(request)
        try:
            return start_live(body.tool)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @router.get("/run")
    def run_output(request: Request, after: int = 0):
        local_only(request)
        return live(max(0, after))

    @router.post("/doctor")
    def run_doctor(request: Request):
        local_only(request)
        return doctor()

    @router.post("/reach/doctor")
    def reach_doctor(request: Request):
        local_only(request)
        return agentreach.doctor()
