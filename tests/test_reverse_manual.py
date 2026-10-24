"""Reverse engineer anything (Research and learning), the User manual page and its phone QR code (18 Oct)."""
from fastapi.testclient import TestClient

from airtac import config, llm
from airtac.main import app
from plugins import reverse

client = TestClient(app, base_url="http://localhost")


def test_repo_facts_then_a_plan(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    calls = []

    def fake(url, **kw):
        calls.append(url)
        if url.endswith("/repos/ollama/ollama"):
            return {"full_name": "ollama/ollama", "html_url": "https://github.com/ollama/ollama", "description": "Run LLMs locally",
                    "license": {"spdx_id": "MIT"}, "stargazers_count": 1, "default_branch": "main"}
        if url.endswith("/languages"):
            return {"Go": 1, "C": 1}
        if "/git/trees/" in url:
            return {"tree": [{"type": "blob", "path": "go.mod"}, {"type": "blob", "path": "server/routes.go"}]}
        if url.endswith("/readme"):
            return "# Ollama\nGet up and running with large language models."
        if "/contents/go.mod" in url:
            return "module github.com/ollama/ollama"
        return None
    monkeypatch.setattr(reverse, "fetch", fake)
    facts = reverse.repo_facts("ollama", "ollama")
    assert "Licence: MIT" in facts and "Languages: Go, C" in facts and "module github.com/ollama/ollama" in facts
    r = reverse.analyse("https://github.com/ollama/ollama")       # no brain in tests: the facts are shown
    assert r["ok"] and "Licence: MIT" in r["text"]
    monkeypatch.setattr(llm, "brain_on", lambda: True)
    monkeypatch.setattr(llm, "ask_text", lambda system, content, **kw: "## What it is\nA local LLM runner." if "Licence: MIT" in content else "")
    assert client.post("/api/plugins/reverse/analyse", json={"target": "https://github.com/ollama/ollama"}).json()["text"].startswith("## What it is")


def test_bad_targets_and_offline_say_so():
    assert not reverse.analyse("ollama")["ok"]
    assert "offline" in reverse.analyse("https://github.com/a/b")["text"]


def test_manual_file_and_qr():
    md = (config.ROOT / "docs" / "USER_MANUAL.md").read_text(encoding="utf-8")
    for heading in ("## Assistant", "## Research and learning", "### Reverse engineer anything", "## Your phone", "### Dictation"):
        assert heading in md
    r = client.get("/api/devices/qr").json()
    assert r["ready"] is False and "lan" in r                          # phone access is off in tests


def test_qr_points_at_the_wifi_address(monkeypatch):
    from airtac import pairing
    monkeypatch.setattr(pairing, "state", lambda: {"on": True, "running": True, "port": 8766, "ips": ["192.168.1.5"], "error": "", "fingerprint": "ab"})
    r = client.get("/api/devices/qr").json()
    assert r["ready"] and r["url"] == "https://192.168.1.5:8766/" and r["kind"] == "web" and r["qr_svg"].lstrip().startswith("<svg")


def test_a_cloned_project_is_taken_apart_and_saved_as_notes(tmp_path, monkeypatch):
    from plugins import repos, research
    proj = tmp_path / "acme__bot"
    (proj / "src").mkdir(parents=True)
    (proj / "app.py").write_text("import fastapi\nprint(1)\n")
    (proj / "src" / "util.py").write_text("x = 1\n")
    (proj / "tests").mkdir()
    (proj / "tests" / "test_app.py").write_text("def test(): pass\n")
    (proj / "requirements.txt").write_text("fastapi==0.1\ntransformers>=4\n# c\n")
    (proj / "LICENSE").write_text("MIT License\n")
    (proj / "node_modules").mkdir()
    (proj / "node_modules" / "x.js").write_text("ignored\n")
    monkeypatch.setattr(repos, "folder", lambda owner, name: proj if (owner, name) == ("acme", "bot") else tmp_path / "none")
    r = client.post("/api/plugins/reverse/analyze", json={"repo": "acme/bot", "save": True}).json()
    a = r["analysis"]
    assert a["languages"][0]["name"] == "Python" and a["languages"][0]["files"] == 3 and a["files"] == 5
    assert a["ai"] == ["fastapi", "transformers"] and "app.py" in a["entry_points"] and a["tests"] == 1
    assert a["licence"].startswith("MIT") and a["dependencies"]["requirements.txt"] == ["fastapi", "transformers"]
    assert r["note_id"] and research.get_note(r["note_id"])["title"] == "Reverse engineering: acme/bot"
    assert client.post("/api/plugins/reverse/analyze", json={"repo": "no/such"}).status_code == 422


def test_security_panel_checks():
    rows = {c["name"]: c for c in client.get("/api/security").json()["checks"]}
    assert rows["Who can reach AIRTAC"]["ok"] is True and "Only this PC" in rows["Who can reach AIRTAC"]["detail"]
    assert "Password vault" in rows and "Keys in .env" in rows
    assert client.get("/api/security", headers={"X-Forwarded-For": "192.168.1.9"}).status_code in (401, 403)
