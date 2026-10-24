"""AIRTAC doctor: checks everything AIRTAC needs and says what to fix, in plain words.
    .venv\\Scripts\\python.exe scripts\\doctor.py          (text, for a terminal)
    .venv\\Scripts\\python.exe scripts\\doctor.py --json   (for the Dev tools page)

Checks: Python, config/airtac.toml, the server and its port, the database, every plugin's manifest,
Ollama and the models, which API keys are set, disk and memory, and the last error in the server log.
It only reads. Keys are reported as "set" or "not set": their values are never printed.
Exit code 1 when any check fails (warnings don't count).
"""
import json
import os
import sqlite3
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AIRTAC_NO_SCHEDULER", "1")

CHECKS: list[dict] = []


def add(group: str, name: str, state: str, detail: str = "", fix: str = "") -> None:
    """state: ok, warn or fail."""
    CHECKS.append({"group": group, "name": name, "state": state, "detail": detail, "fix": fix if state != "ok" else ""})


def check_python() -> None:
    v = sys.version_info
    add("PC", "Python", "ok" if v >= (3, 11) else "fail", sys.version.split()[0], "Install Python 3.11 or newer.")
    in_venv = sys.prefix != sys.base_prefix
    add("PC", "Virtual environment", "ok" if in_venv else "warn", sys.prefix,
        "Run with .venv\\Scripts\\python.exe so AIRTAC's packages are used.")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from airtac import deps
        gone = deps.missing()
    except Exception as e:   # noqa: BLE001
        gone = [f"(couldn't check: {type(e).__name__})"]
    add("PC", "Packages", "ok" if not gone else "fail", "all installed" if not gone else "missing: " + ", ".join(gone),
        "Double-click repair_airtac.cmd (repairs pip, then installs what's missing), then restart AIRTAC.")


def check_config() -> int:
    path = ROOT / "config" / "airtac.toml"
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        add("Config", "config/airtac.toml", "warn", "missing: AIRTAC uses its defaults", "Copy it back from git.")
        return 8765
    except tomllib.TOMLDecodeError as e:
        # The server can't start with a broken file, so this is the first thing to look at.
        add("Config", "config/airtac.toml", "fail", f"not valid TOML: {e}", "Fix the line named here, then start AIRTAC again.")
        return 8765
    try:
        port = int(raw.get("server", {}).get("port", 8765))
    except (TypeError, ValueError):
        add("Config", "[server] port", "fail", "not a number", "Set port = 8765 under [server].")
        return 8765
    add("Config", "config/airtac.toml", "ok", f"valid; port {port}")
    return port


def _port_owner(port: int) -> str:
    try:
        import psutil
        for c in psutil.net_connections(kind="inet"):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
                try:
                    p = psutil.Process(c.pid)
                    return f"{p.name()} (process {c.pid}): {' '.join(p.cmdline())[:120]}"
                except (psutil.Error, TypeError):
                    return f"process {c.pid}"
    except (ImportError, OSError, Exception):  # psutil can be refused on locked-down PCs
        return "unknown"
    return ""


def check_server(port: int) -> None:
    import httpx
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=5, headers={"host": "localhost"})
        h = r.json()
        brain = h.get("brain", {})
        add("Server", f"AIRTAC on port {port}", "ok" if h.get("status") == "ok" else "fail",
            f"version {h.get('version')}, brain {brain.get('model')} ({brain.get('provider')})"
            + ("" if brain.get("ready", True) else f": not ready, {brain.get('reason', '')}"))
        if brain.get("ready") is False:
            add("Server", "Brain ready", "warn", brain.get("reason", ""), "See the Ollama checks below.")
    except (httpx.HTTPError, ValueError) as e:
        owner = _port_owner(port)
        if owner:
            add("Server", f"AIRTAC on port {port}", "fail", f"the port is held by {owner}, but /api/health doesn't answer ({type(e).__name__})",
                "Run start_airtac.bat: it stops a hung AIRTAC. If it's another program, close it or change [server] port.")
        else:
            add("Server", f"AIRTAC on port {port}", "fail", f"not running; port {port} is free", "Double-click start_airtac.bat.")


def check_database() -> None:
    db = ROOT / "data" / "airtac.db"
    if not db.exists():
        add("Data", "Database", "warn", "data/airtac.db doesn't exist yet", "It's created the first time AIRTAC starts.")
        return
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
        result = con.execute("PRAGMA quick_check").fetchone()[0]
        tables = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        con.close()
    except sqlite3.Error as e:
        add("Data", "Database", "fail", f"can't be read: {e}", "Restore data/ from the newest folder in backups.")
        return
    size = db.stat().st_size / 2**20
    add("Data", "Database", "ok" if result == "ok" else "fail", f"{tables} tables, {size:.1f} MB, integrity {result}",
        "Restore data/ from the newest backup.")


def check_plugins() -> None:
    from airtac import plugins
    plugins.load_all()
    states: dict[str, int] = {}
    for s in sorted(plugins.STATUS.values(), key=lambda s: s.get("order", 0)):
        states[s["state"]] = states.get(s["state"], 0) + 1
        if s["state"] == "error":
            add("Plugins", s["name"], "fail", s["reason"], "Fix its manifest.toml or __init__.py.")
        elif s["state"] == "off" and s.get("reason"):
            add("Plugins", s["name"], "warn", s["reason"])
    add("Plugins", "Plugin folder", "fail" if states.get("error") else "ok",
        ", ".join(f"{n} {k}" for k, n in sorted(states.items())))


def check_brains() -> None:
    from airtac import brains, config
    if config.LLM_PROVIDER != "ollama":
        add("Brain", "Brain", "ok", f"{config.LLM_PROVIDER}: {config.LLM_MODEL}")
        return
    st = brains.status()
    if not st["ollama_up"]:
        add("Brain", "Ollama", "fail", f"not answering at {config.LLM_BASE_URL}",
            "Open Ollama from the Start menu (or run: ollama serve).")
        return
    add("Brain", "Ollama", "ok", config.LLM_BASE_URL)
    for r in st["roles"]:
        if r["installed"] is None or not r["own_model"] and r["role"] != "general":
            continue
        add("Brain", f"{r['label']}: {r['model']}", "ok" if r["installed"] else "warn" if r["role"] == "embed" else "fail",
            "downloaded" if r["installed"] else "not downloaded", f"ollama pull {r['model']}")


def check_keys() -> None:
    from airtac import connections
    try:
        connections.load_saved()     # keys saved on the Connections page; read into memory, never printed
    except Exception as e:
        add("Keys", "Saved keys", "warn", f"couldn't read Windows Credential Manager: {type(e).__name__}",
            "Keys in .env still work.")
    store = connections.store_status()
    add("Keys", "Key store", "ok" if store["vault"] else "warn", store["detail"])
    rows = connections.all_connectors()
    on = [c["name"] for c in rows if c["status"] != "off" and c["fields"]]
    off = [c["name"] for c in rows if c["status"] == "off" and c["fields"]]
    for c in rows:
        if c["status"] != "off" and c["fields"]:
            where = {"app": "saved in AIRTAC", "env": "from .env"}.get(c["fields"][0]["source"], "set")
            add("Keys", c["name"], "ok", f"set ({where})")
    add("Keys", "Not set", "ok", ", ".join(off) or "none", "")
    if not on:
        add("Keys", "Cloud brains", "warn", "no key set: AIRTAC answers with the model on this PC only",
            "Optional. Add a free key (Groq, OpenRouter) on the Connections page.")


def check_pc() -> None:
    try:
        import psutil
    except ImportError:
        add("PC", "psutil", "warn", "not installed", "Run Install-AIRTAC.cmd again.")
        return
    mem = psutil.virtual_memory()
    free_gb = mem.available / 2**30
    add("PC", "Memory", "ok" if free_gb >= 1.5 else "warn", f"{free_gb:.1f} GB free of {mem.total / 2**30:.0f} GB",
        "Close browser tabs or apps: a 4B model needs about 3 GB free to answer quickly.")
    for drive in {ROOT.anchor, "C:\\"}:
        try:
            free = psutil.disk_usage(drive).free / 2**30
        except OSError:
            continue
        add("PC", f"Free space on {drive}", "ok" if free >= 5 else "warn", f"{free:.0f} GB", "Free some space: models and logs need room.")


def check_log() -> None:
    log = ROOT / "data" / "logs" / "server.log"
    if not log.exists():
        add("Server", "Server log", "ok", "no log yet (it starts with start_airtac.bat)")
        return
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    errors = [ln for ln in lines if " ERROR " in ln]
    if errors:
        add("Server", "Last error in the log", "warn", errors[-1][:300], f"Full log: {log}")
    else:
        add("Server", "Server log", "ok", f"no errors in the last {len(lines)} lines")


def main() -> int:
    as_json = "--json" in sys.argv
    check_python()
    port = check_config()
    check_server(port)
    check_log()
    for fn in (check_database, check_plugins, check_brains, check_keys, check_pc):
        try:
            fn()
        except Exception as e:   # one broken check shouldn't hide the others
            add("Doctor", fn.__name__.replace("check_", "").title(), "fail", f"the check itself failed: {type(e).__name__}: {e}")
    failed = sum(c["state"] == "fail" for c in CHECKS)
    warned = sum(c["state"] == "warn" for c in CHECKS)
    if as_json:
        print(json.dumps({"ok": failed == 0, "failed": failed, "warnings": warned, "checks": CHECKS}))
    else:
        mark = {"ok": "  OK  ", "warn": "  WARN", "fail": "  FAIL"}
        group = ""
        for c in CHECKS:
            if c["group"] != group:
                group = c["group"]
                print(f"\n{group}")
            print(f"{mark[c['state']]}  {c['name']}: {c['detail']}" + (f"\n          -> {c['fix']}" if c["fix"] else ""))
        print(f"\n{'All good.' if not failed else f'{failed} to fix.'}" + (f" {warned} warning(s)." if warned else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
