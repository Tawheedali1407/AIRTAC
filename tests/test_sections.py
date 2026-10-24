"""Settings > Sections (Ali, 18 Oct): make your own sections like Research, with a name, icon, instructions, and
which plugins and documents it uses; the Chat page sends section=<id>."""
import json

from fastapi.testclient import TestClient

from airtac import agent
from airtac.main import app
from plugins import attachments, sections

client = TestClient(app, base_url="http://localhost")


def test_add_change_and_delete_a_section():
    data = client.get("/api/plugins/sections").json()
    assert data["sections"] == [] and "steps" in data["icons"] and data["starters"][0]["name"] == "Research"
    made = client.post("/api/plugins/sections", json={"name": "  Research  ", "icon": "steps", "prompt": "Cite sources.",
                                                       "plugins": ["web", "not-a-plugin"]}).json()
    assert made["id"] == "research" and made["name"] == "Research" and made["plugins"] == ["web"]
    again = client.post("/api/plugins/sections", json={"name": "Research", "icon": "bogus"}).json()
    assert again["id"] == "research-2" and again["icon"] == "spark"
    changed = client.post("/api/plugins/sections/research", json={"name": "Deep research", "prompt": "Be thorough."}).json()
    assert changed["name"] == "Deep research" and changed["id"] == "research"
    assert client.post("/api/plugins/sections/nope", json={"name": "x y"}).status_code == 404
    assert client.post("/api/plugins/sections", json={"name": " a "}).status_code == 422
    assert client.post("/api/plugins/sections/research-2/delete").json() == {"deleted": "research-2"}
    assert [s["id"] for s in client.get("/api/plugins/sections").json()["sections"]] == ["research"]


def test_a_section_s_instructions_plugins_and_documents_reach_the_brain(monkeypatch):
    up = attachments.save("sections", "notes.txt", b"The DBMS exam covers normal forms and B-trees.")
    attachments.read_now(up["id"])
    client.post("/api/plugins/sections", json={"name": "Exam prep", "prompt": "Quiz me at the end.", "plugins": ["study"],
                                              "docs": [up["id"], 999999]})
    assert sections.get_section("exam-prep")["docs"] == [up["id"]]
    seen = {}
    real = agent.chat

    def spy(message, session_id="default", **kw):
        seen.update(kw)
        return real(message, session_id, **kw)

    monkeypatch.setattr(agent, "chat", spy)
    r = client.post("/api/chat/stream", json={"message": "what's in my exam?", "session_id": "s1", "section": "exam-prep"})
    done = json.loads(r.text.strip().splitlines()[-1])
    assert done["type"] == "done"
    assert seen["instructions"] == "Quiz me at the end." and seen["plugins"] == ["study"] and seen["attachments"] == [up["id"]]
    assert client.post("/api/chat/stream", json={"message": "x", "session_id": "s1", "section": "gone"}).status_code == 404
    ctx, _ = agent._build_context("s9", "hi", "general", None, "chat", "Quiz me at the end.")
    assert "Instructions for this section" in ctx[0]["content"] and "Quiz me at the end." in ctx[0]["content"]
