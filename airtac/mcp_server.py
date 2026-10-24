"""AIRTAC Advance's tools as an MCP server, so any MCP client (Hermes Agent, Claude Desktop, an IDE)
can use them.

MCP (Model Context Protocol) over stdio is just JSON-RPC 2.0, one JSON message per line on
stdin/stdout. That is small enough to write by hand, so no extra library is needed.

Run it (the client starts it for you from its config):
    .venv\\Scripts\\python.exe -m airtac.mcp_server

What is shared: AIRTAC's tools and its SQLite memory, so a fact saved from Telegram (through
Hermes) is the same fact the web page recalls.
What is NOT exposed:
- write and device tools (create an issue, open an app): they need AIRTAC's own Confirm button.
Every text argument still goes through safety.check_request first.
"""
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make `import airtac` work
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airtac import plugins, safety  # noqa: E402
from airtac.tools import REGISTRY, run_and_log  # noqa: E402

# stdout carries the protocol, so logs must go to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="airtac-mcp %(levelname)s %(message)s")
log = logging.getLogger("airtac.mcp")

SERVER_INFO = {"name": "airtac-advance", "title": "AIRTAC Advance", "version": "0.1.0"}
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = ("AIRTAC Advance tools for a CS student: schedule, memory, GitHub, LeetCode, papers, "
                "practice problems, study questions, diagrams and documents. "
                "Each result starts with a short spoken-style line, then details. "
                "Never use these tools for live contest problems.")


def exposed_tools() -> list:
    plugins.load_all()
    return [t for t in REGISTRY.values() if t.risk not in ("write", "device")]


def _tool_entry(tool) -> dict:
    return {"name": tool.name, "description": tool.description, "inputSchema": tool.parameters,
            "annotations": {"readOnlyHint": tool.risk == "read", "openWorldHint": tool.live is not None}}


def _call(params: dict) -> dict:
    name = params.get("name", "")
    args = params.get("arguments") or {}
    tool = REGISTRY.get(name)
    if tool is None or tool not in exposed_tools():
        raise LookupError(f"Unknown tool: {name}")
    text_args = " ".join(str(v) for v in args.values() if isinstance(v, str))
    refusal = safety.check_request(text_args)
    if refusal:
        return {"content": [{"type": "text", "text": refusal}], "isError": True}
    result = run_and_log(name, args)
    text = result.spoken + (f"\n\n{result.screen}" if result.screen else "")
    return {"content": [{"type": "text", "text": text}], "isError": not result.ok}


def handle(message: dict) -> dict | None:
    """Answer one JSON-RPC message. Returns None for notifications (they get no reply)."""
    method, msg_id = message.get("method"), message.get("id")
    if msg_id is None:  # notifications such as notifications/initialized
        return None
    try:
        if method == "initialize":
            asked = (message.get("params") or {}).get("protocolVersion", "")
            result = {"protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [_tool_entry(t) for t in exposed_tools()]}
        elif method == "tools/call":
            result = _call(message.get("params") or {})
        else:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    except LookupError as e:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": str(e)}}
    except Exception:  # a bug must not kill the server
        log.exception("request failed")
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": "Internal error"}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(stdin=None, stdout=None) -> None:
    # Bytes in, bytes out: avoids Windows code-page and \r\n surprises.
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(message) if isinstance(message, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply).encode("ascii") + b"\n")  # ensure_ascii: safe on any console
            stdout.flush()


if __name__ == "__main__":
    serve()
