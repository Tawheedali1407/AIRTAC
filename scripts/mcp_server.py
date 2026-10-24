"""A tiny MCP server so Claude Code (or any MCP host) can ask AIRTAC on this PC.

It speaks MCP over stdio as plain JSON-RPC (no extra package), and forwards to AIRTAC's local API at
http://127.0.0.1:<port>. It exposes only safe, read-style tools:

    ask_airtac(question)   ask AIRTAC a question (it uses its own brains, plugins and your knowledge)
    airtac_read(url)       read a public page/tweet/thread/video as clean text (Agent Reach / Jina)

It never exposes anything that changes your machine, your keys or the vault. AIRTAC must be running
(AIRTAC-Advance.cmd, or the installer's shortcut).

Add it to Claude Code (the Dev tools page shows this command):
    claude mcp add --transport stdio airtac -- <python> scripts/mcp_server.py
"""
import json
import os
import sys
import urllib.request

_PORT = os.environ.get("AIRTAC_PORT", "8765")
PORT = _PORT if _PORT.isdigit() and 1 <= int(_PORT) <= 65535 else "8765"   # digits only: nothing injectable into the URL
BASE = f"http://127.0.0.1:{PORT}"
TOOLS = [
    {"name": "ask_airtac", "description": "Ask AIRTAC (the user's personal assistant on this PC) a question. "
     "It answers using its own models, plugins and the user's knowledge library.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "airtac_read", "description": "Read a public web page, tweet, Reddit thread or YouTube video as clean text.",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
]


def _install_token() -> str:
    """AIRTAC's per-install API token (data/.install_token, readable only by you)."""
    from pathlib import Path
    try:
        return (Path(__file__).resolve().parents[1] / "data" / ".install_token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _post(path: str, body: dict) -> dict:
    url = BASE + path
    if not url.startswith("http://127.0.0.1:"):   # only ever this PC's loopback
        raise ValueError("refusing a non-loopback address")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Host": "localhost",
                                          "X-AIRTAC-Token": _install_token()})
    with urllib.request.urlopen(req, timeout=120) as r:   # noqa: S310  # nosec B310 (fixed localhost address)
        return json.loads(r.read().decode())


def _call(name: str, args: dict) -> str:
    if name == "ask_airtac":
        data = _post("/api/chat", {"message": str(args.get("question", ""))[:2000], "session_id": "mcp"})
        return (data.get("spoken", "") + ("\n\n" + data["screen"] if data.get("screen") else "")).strip()
    if name == "airtac_read":
        data = _post("/api/chat", {"message": "Read this: " + str(args.get("url", ""))[:400], "session_id": "mcp",
                                   "plugins": ["agentreach"]})
        return (data.get("screen") or data.get("spoken") or "").strip()
    raise ValueError(f"Unknown tool {name}")


def _reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = {"code": -32000, "message": error}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            _reply(mid, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                         "serverInfo": {"name": "airtac", "version": "0.1.0"}})
        elif method == "tools/list":
            _reply(mid, {"tools": TOOLS})
        elif method == "tools/call":
            try:
                text = _call(params.get("name", ""), params.get("arguments") or {})
                _reply(mid, {"content": [{"type": "text", "text": text or "(no answer)"}]})
            except Exception as e:  # noqa: BLE001
                _reply(mid, error=f"{type(e).__name__}: {e}")
        elif method and method.startswith("notifications/"):
            continue                 # notifications need no reply
        elif mid is not None:
            _reply(mid, error=f"Unknown method {method}")


if __name__ == "__main__":
    main()
