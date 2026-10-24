"""Repositories (Part F): GitHub projects on this PC, in "F:\\AIRTAC Advance\\repos\\".

Add by URL: only https://github.com/<owner>/<repo>. AIRTAC runs a fixed command (git clone --depth 1),
reads the README and lists its install steps. README text is untrusted: AIRTAC never runs it as it is.
It recognises a few safe shapes and runs them only after you press Confirm, inside the repo's folder:
  pip install ... / python -m pip install ...   -> into the repo's OWN venv (repos\\<name>\\.venv), never AIRTAC's
  npm install / npm ci                           -> in the repo folder
  git submodule update --init                    -> in the repo folder
Anything else (a shell script, curl | sh, sudo, docker...) is shown with "run it yourself if you trust it".
Status: not cloned, cloned, installed, update available (git fetch, then compare with the remote).
Everything here works only on this PC.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from airtac import config, memory
from airtac.plugin_api import Tool, ToolResult, local_only, plugin, schema

p = plugin(__name__)
URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")

# Checked 17 Oct 2026 (each URL exists; notes from each project's README).
CATALOG = [
    {"url": "https://github.com/ollama/ollama", "about": "Runs AIRTAC's local models (you have the app; this is its source)."},
    {"url": "https://github.com/huggingface/transformers", "about": "Hugging Face's model library (Python)."},
    {"url": "https://github.com/vllm-project/vllm", "about": "Fast model serving.", "note": "Needs an NVIDIA GPU: for the new laptop."},
    {"url": "https://github.com/cheahjs/free-llm-api-resources", "about": "A maintained list of free LLM APIs and their limits."},
    {"url": "https://github.com/usestrix/strix", "about": "AI security testing (Dev tools runs it against AIRTAC)."},
    {"url": "https://github.com/thedotmack/claude-mem", "about": "Persistent memory for Claude Code."},
    {"url": "https://github.com/Panniantong/agent-reach", "about": "Lets agents read X, Reddit, YouTube and the web."},
    {"url": "https://github.com/anthropics/claude-plugins-official", "about": "Anthropic's Claude Code plugins, incl. claude-code-setup."},
]
SAFE = [
    (re.compile(r"^(python3? -m )?pip3? install( [A-Za-z0-9_.\-\[\]=<>~!,+/@:]+)+$"), "pip"),
    (re.compile(r"^npm (install|ci)( --[a-z-]+)*$"), "npm"),
    (re.compile(r"^git submodule update --init( --recursive)?$"), "git"),
]
LIVE: dict = {"repo": "", "lines": [], "done": True, "ok": None}
_lock = threading.Lock()


def root() -> Path:
    path = config.ROOT / "repos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse(url: str) -> tuple[str, str]:
    m = URL_RE.match((url or "").strip())
    if not m:
        raise ValueError("Give a GitHub address like https://github.com/owner/repo")
    return m.group(1), m.group(2)


def folder(owner: str, name: str) -> Path:
    return root() / f"{owner}__{name}"


def _git() -> str:
    exe = shutil.which("git")
    if not exe:
        raise ValueError("Git isn't installed. Get it from https://git-scm.com/download/win, then try again.")
    return exe


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout,  # noqa: S603 (list, no shell)
                          errors="replace", env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def _added() -> list[str]:
    try:
        return json.loads(memory.kv_get("repos", "added") or "[]")
    except ValueError:
        return []


def clone(url: str) -> dict:
    owner, name = parse(url)
    dest = folder(owner, name)
    if not dest.exists():
        r = _run([_git(), "clone", "--depth", "1", f"https://github.com/{owner}/{name}.git", str(dest)])
        if r.returncode != 0:
            raise ValueError("Git couldn't clone it. Is the address right and the repository public?")
    remember(owner, name)
    return info(owner, name)


def has_git() -> bool:
    return bool(shutil.which("git"))


def remember(owner: str, name: str) -> None:
    """List it on the Repositories page (also used when Reverse engineering downloaded it as a zip, without Git)."""
    added = _added()
    if f"{owner}/{name}" not in added:
        memory.kv_set("repos", "added", json.dumps(added + [f"{owner}/{name}"]))


def readme(dest: Path) -> str:
    for n in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        f = dest / n
        if f.is_file():
            return f.read_text(encoding="utf-8", errors="replace")[:200_000]
    return ""


def install_steps(text: str) -> list[dict]:
    """Commands from the README's code blocks under install/setup headings: [{cmd, runnable, kind}]."""
    out, heading, seen = [], "", set()
    blocks = re.split(r"(^#{1,4} .*$)", text, flags=re.M)
    for part in blocks:
        if re.match(r"^#{1,4} ", part):
            heading = part.lower()
            continue
        if not any(w in heading for w in ("install", "setup", "set up", "getting started", "quick start", "quickstart", "usage")):
            continue
        for code in re.findall(r"```[a-zA-Z]*\n(.*?)```", part, re.S):
            for line in code.splitlines():
                line = line.strip().lstrip("$> ").strip()
                if not line or line.startswith("#") or line in seen or len(line) > 300:
                    continue
                seen.add(line)
                kind = next((k for rx, k in SAFE if rx.match(line)), "")
                out.append({"cmd": line, "runnable": bool(kind), "kind": kind})
    return out[:25]


def info(owner: str, name: str) -> dict:
    dest = folder(owner, name)
    cat = next((c for c in CATALOG if c["url"].lower() == f"https://github.com/{owner}/{name}".lower()), {})
    state = "not cloned" if not dest.exists() else "installed" if (dest / ".airtac-installed").exists() else "cloned"
    return {"id": f"{owner}/{name}", "url": f"https://github.com/{owner}/{name}", "about": cat.get("about", ""),
            "note": cat.get("note", ""), "state": state, "folder": str(dest),
            "steps": install_steps(readme(dest)) if dest.exists() else [], "update": memory.kv_get("repos", f"update:{owner}/{name}") == "1"}


def all_repos() -> list[dict]:
    ids = [parse(c["url"]) for c in CATALOG] + [tuple(x.split("/", 1)) for x in _added()]
    seen, out = set(), []
    for owner, name in ids:
        if f"{owner}/{name}".lower() not in seen:
            seen.add(f"{owner}/{name}".lower())
            out.append(info(owner, name))
    return out


def check_update(owner: str, name: str) -> bool:
    dest = folder(owner, name)
    if not dest.exists():
        return False
    git = _git()
    if _run([git, "fetch", "--depth", "1", "origin"], dest).returncode != 0:
        raise ValueError("Couldn't reach GitHub to check for updates.")
    local = _run([git, "rev-parse", "HEAD"], dest).stdout.strip()
    remote = _run([git, "rev-parse", "FETCH_HEAD"], dest).stdout.strip()
    newer = bool(local and remote and local != remote)
    memory.kv_set("repos", f"update:{owner}/{name}", "1" if newer else "0")
    return newer


def _command(dest: Path, line: str, kind: str) -> list[list[str]]:
    """The exact argv list(s) for one recognised step (no shell)."""
    args = shlex.split(line)
    if kind == "pip":
        venv = dest / ".venv"
        py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        first = [] if py.exists() else [[sys.executable, "-m", "venv", str(venv)]]
        rest = args[args.index("install") + 1:]
        return first + [[str(py), "-m", "pip", "install", "--no-cache-dir", *rest]]
    if kind == "npm":
        return [[shutil.which("npm") or "npm", *args[1:]]]
    if kind == "git":
        return [[_git(), *args[1:]]]
    raise ValueError("AIRTAC doesn't run that step. Run it yourself if you trust it.")


def start_install(owner: str, name: str, step: str) -> dict:
    dest = folder(owner, name)
    found = next((s for s in info(owner, name)["steps"] if s["cmd"] == step), None)
    if found is None or not found["runnable"]:
        raise ValueError("AIRTAC only runs recognised install steps from the README (pip, npm, git submodule).")
    cmds = _command(dest, found["cmd"], found["kind"])
    with _lock:
        if not LIVE["done"]:
            raise RuntimeError(f"{LIVE['repo']} is still installing. Wait for it to finish.")
        LIVE.update(repo=f"{owner}/{name}", lines=[], done=False, ok=None)

    def work():
        ok = True
        for cmd in cmds:
            LIVE["lines"].append("$ " + " ".join(cmd))
            try:
                proc = subprocess.Popen(cmd, cwd=str(dest), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,  # noqa: S603 (list, no shell)
                                        bufsize=1, errors="replace", env={**os.environ, "PIP_CACHE_DIR": str(config.ROOT / "tmp" / "pip")})
                for line in proc.stdout:
                    LIVE["lines"].append(line.rstrip()[:400])
                    del LIVE["lines"][:-2000]
                code = proc.wait(timeout=1800)
            except (OSError, subprocess.SubprocessError) as e:
                LIVE["lines"].append(f"Couldn't run it: {type(e).__name__}.")
                ok = False
                break
            if code != 0:
                LIVE["lines"].append(f"(stopped: exit code {code})")
                ok = False
                break
        if ok:
            (dest / ".airtac-installed").write_text("ok", encoding="utf-8")
        LIVE.update(ok=ok, done=True)
    threading.Thread(target=work, daemon=True, name="repos-install").start()
    return live(0)


def live(after: int = 0) -> dict:
    return {"repo": LIVE["repo"], "lines": LIVE["lines"][after:], "next": len(LIVE["lines"]), "done": LIVE["done"], "ok": LIVE["ok"]}


def free_apis() -> list[dict]:
    """The free-tier brains (their key cards are on APIs and keys). Limits as each provider's note says."""
    from airtac import providers
    rows = []
    for n in ("gemini", "groq", "openrouter", "huggingface", "nebius"):
        pr = providers.PROVIDERS[n]
        rows.append({"id": n, "name": pr.label, "note": pr.note, "get_key": pr.get_key, "key": providers.has_key(n),
                     "trains_on_data": pr.trains_on_data, "paid": pr.paid})
    return rows


def repos_list() -> ToolResult:
    items = [r for r in all_repos() if r["state"] != "not cloned"]
    if not items:
        return ToolResult("No repositories on this PC yet. Add one on the Repositories page.")
    return ToolResult(f"{len(items)} repositories on this PC. Details are on screen.",
                      "\n".join(f"- {r['id']}: {r['state']}{', update available' if r['update'] else ''}" for r in items))


p.add(Tool("repos_list", "List the GitHub repositories cloned on this PC and their install state.", schema(), "read", repos_list))
p.mock_rule(0.7, ("my repositories", "repos on this pc"), "repos_list")


@p.dashboard
def page(pg: dict) -> None:
    items = all_repos()
    pg["stats"] = [{"label": "Cloned", "value": sum(r["state"] != "not cloned" for r in items)},
                   {"label": "Git", "value": "ready" if shutil.which("git") else "not installed"}]
    pg["repos"] = {"items": items, "free": free_apis(), "git": bool(shutil.which("git")), "folder": str(root())}


@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Request
    from pydantic import BaseModel, Field

    class Add(BaseModel):
        url: str = Field(max_length=200)

    class Step(BaseModel):
        repo: str = Field(max_length=150, pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
        step: str = Field(default="", max_length=300)

    def ids(repo: str) -> tuple[str, str]:
        owner, name = repo.split("/", 1)
        return parse(f"https://github.com/{owner}/{name}")

    @router.get("")
    def list_(request: Request):
        local_only(request)
        pg: dict = {}
        page(pg)
        return pg["repos"]

    @router.post("/add")
    def add(body: Add, request: Request):
        local_only(request)
        try:
            return clone(body.url)
        except (ValueError, subprocess.SubprocessError) as e:
            raise HTTPException(400, str(e) if isinstance(e, ValueError) else "Git took too long.")

    @router.post("/update")
    def update(body: Step, request: Request):
        local_only(request)
        try:
            return {"update": check_update(*ids(body.repo))}
        except (ValueError, subprocess.SubprocessError) as e:
            raise HTTPException(400, str(e) if isinstance(e, ValueError) else "Git took too long.")

    @router.post("/install")
    def install(body: Step, request: Request):
        local_only(request)
        try:
            return start_install(*ids(body.repo), body.step)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @router.get("/install")
    def install_log(request: Request, after: int = 0):
        local_only(request)
        return live(max(0, after))
