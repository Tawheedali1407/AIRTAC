"""Part E3: a lecture from an attached file: grounded JSON, saved, in Knowledge, playable, deletable."""
import json

import pytest
from fastapi.testclient import TestClient

from airtac import config, db
from airtac.main import app
from plugins import lectures

client = TestClient(app, base_url="http://localhost")
SOURCE = "[p. 1]\nA hash table maps keys to buckets with a hash function. " * 10 + "\n\n[p. 2]\nCollisions use chaining. " * 10
FAKE = {"summary": "Hash tables map keys to buckets.", "concepts": [{"term": "Hash function", "meaning": "maps a key", "where": "p. 1"}],
        "script": [{"speaker": "Teacher", "text": "Imagine a wall of lockers..."}, {"speaker": "Teacher", "text": "Recap: ..."}],
        "quiz": [{"q": f"Q{i}", "answer": "A", "where": "p. 2"} for i in range(5)], "missing": ""}


@pytest.fixture
def upload():
    with db.connect() as conn:
        return conn.execute("INSERT INTO uploads (section, name, stored, kind, status, text) VALUES "
                            "('study', 'hashing.pdf', 'x.pdf', 'pdf', 'ready', ?)", (SOURCE,)).lastrowid


def test_make_a_lecture_from_an_attached_file(upload, monkeypatch):
    sent = {}
    monkeypatch.setattr(lectures, "ask_brain", lambda prompt: sent.setdefault("p", prompt) and "Sure!\n" + json.dumps(FAKE))
    r = client.post("/api/plugins/lectures", json={"upload_id": upload, "mode": "conversation"}).json()
    assert r["title"] == "hashing" and r["summary"] and len(r["quiz"]) == 5
    assert "Use ONLY the SOURCE" in sent["p"] and "[p. 2]" in sent["p"] and "Student" in sent["p"]
    md = list((config.KNOWLEDGE_DIR / "lectures").glob("*.md"))
    assert md and "Hash function" in md[0].read_text()
    assert client.get("/api/plugins/lectures").json()["items"][0]["id"] == r["id"]
    assert client.get(f"/api/plugins/lectures/{r['id']}").json()["script"][0]["text"].startswith("Imagine")
    assert client.post(f"/api/plugins/lectures/{r['id']}/audio").status_code == 400      # no Piper: says so
    assert client.post(f"/api/plugins/lectures/{r['id']}/delete").json()["ok"]
    assert not list((config.KNOWLEDGE_DIR / "lectures").glob("*.md"))


def test_bad_answers_and_inputs_say_so(upload, monkeypatch):
    monkeypatch.setattr(lectures, "ask_brain", lambda prompt: "I can't do that")
    assert "didn't return a lecture" in client.post("/api/plugins/lectures", json={"upload_id": upload}).json()["detail"]
    assert client.post("/api/plugins/lectures", json={"text": "too short"}).status_code == 400
    assert client.get("/api/plugins/lectures/../../etc").status_code == 404
