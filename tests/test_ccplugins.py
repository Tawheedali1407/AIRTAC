"""Part F: the Claude Code plugins catalog: every name Ali listed, a real source, switches that stick."""
from fastapi.testclient import TestClient

from airtac.main import app
from plugins import ccplugins

client = TestClient(app, base_url="http://localhost")
ASKED = {"Superpowers", "Context7", "Code reviewer", "Code simplifier", "Gstack", "OmniRoute", "claude-mem", "Headroom",
         "Claude Code setup", "Task observer", "Ralph loop", "Ponytail", "CodeRabbit"}


def test_every_asked_name_is_listed_with_a_github_source():
    rows = client.get("/api/plugins/ccplugins").json()["plugins"]
    assert {r["name"] for r in rows} == ASKED
    assert all(r["source"].startswith("https://github.com/") and r["does"] and r["how"] for r in rows)
    assert {r["id"] for r in rows if r.get("key")} == {"context7", "coderabbit"}


def test_switches():
    assert client.post("/api/plugins/ccplugins", json={"id": "superpowers", "on": True}).json()["on"] == ["superpowers"]
    assert next(r for r in ccplugins.catalog() if r["id"] == "superpowers")["on"]
    client.post("/api/plugins/ccplugins", json={"id": "superpowers", "on": False})
    assert client.post("/api/plugins/ccplugins", json={"id": "made-up", "on": True}).status_code == 404
