"""Dev tools: fixed setup commands, PC-only routes, and the MCP link command."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from plugins import devtools as dt


def test_status_lists_the_setup_tools_with_fixed_commands():
    st = dt.status()
    ids = {t["id"] for t in st["tools"]}
    assert {"claude-code", "claude-mem", "agent-reach", "mcp-link"} <= ids
    cc = next(t for t in st["tools"] if t["id"] == "claude-code")
    assert "@anthropic-ai/claude-code" in cc["command"]
    link = next(t for t in st["tools"] if t["id"] == "mcp-link")
    assert "mcp" in link["command"] and "add" in link["command"] and "mcp_server.py" in link["command"]


def test_setup_only_runs_on_this_pc_and_only_known_tools(monkeypatch):
    from airtac.main import app
    ran = []
    monkeypatch.setattr(dt, "_present", lambda need: True)

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    monkeypatch.setattr(dt.subprocess, "run", fake_run)
    monkeypatch.setattr(dt, "_cmd", lambda n: "/usr/bin/" + n)
    here = TestClient(app, base_url="http://localhost")
    r = here.post("/api/plugins/devtools/setup", json={"tool": "claude-code"})
    assert r.json()["ok"] and ran and ran[0][0].endswith("npm")
    assert here.post("/api/plugins/devtools/setup", json={"tool": "rm -rf"}).status_code == 422   # not a known id
    assert here.post("/api/plugins/devtools/setup", json={"tool": "unknown"}).json()["ok"] is False
    # Another device can't set anything up, and can't even see the page.
    away = TestClient(app)
    assert away.post("/api/plugins/devtools/setup", json={"tool": "claude-code"}).status_code in (401, 403)
    assert away.get("/api/plugins/devtools/status").json().get("here") in (False, None)


def test_a_failed_step_stops_and_reports(monkeypatch):
    monkeypatch.setattr(dt, "_present", lambda need: True)
    monkeypatch.setattr(dt, "_cmd", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(dt.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"))
    out = dt.run("agent-reach")
    assert out["ok"] is False and "boom" in out["output"]


def test_the_command_is_never_taken_from_the_request(monkeypatch):
    # The request only names a tool id; the command comes from _tools() in code.
    from airtac.main import app
    monkeypatch.setattr(dt, "_present", lambda need: True)
    seen = {}

    def grab(cmd, **kw):
        seen.setdefault("cmd", cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")
    monkeypatch.setattr(dt.subprocess, "run", grab)
    monkeypatch.setattr(dt, "_cmd", lambda n: n)
    TestClient(app, base_url="http://localhost").post("/api/plugins/devtools/setup", json={"tool": "claude-mem"})
    assert "claude-mem" in seen["cmd"] and "install" in seen["cmd"]


def test_the_mcp_bridge_only_talks_to_loopback_and_sanitises_the_port(monkeypatch):
    import importlib.util
    from pathlib import Path
    monkeypatch.setenv("AIRTAC_PORT", "8765; rm -rf /")
    spec = importlib.util.spec_from_file_location("airtac_mcp", Path(__file__).resolve().parent.parent / "scripts" / "mcp_server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.PORT == "8765" and m.BASE == "http://127.0.0.1:8765"
    assert [t["name"] for t in m.TOOLS] == ["ask_airtac", "airtac_read"]
    import pytest
    with pytest.raises(ValueError):
        m.BASE = "http://evil.com"
        m._post("/api/chat", {})
