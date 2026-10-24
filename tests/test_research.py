"""The Research workspace: notes saved as you type, and the co-pilot (with and without a model)."""
from fastapi.testclient import TestClient

from airtac import config
from airtac.llm import LLMReply
from airtac.main import app

client = TestClient(app, base_url="http://localhost")
NOTES = """# Free GPUs for students

Kaggle gives 30 hours of GPU time a week for notebooks.
Google Colab's free tier has a T4 GPU but sessions stop after about 12 hours.
Lightning AI offers 22 free GPU hours a month on its studio plan.
Which one allows background training?
"""


def test_notes_are_created_saved_and_deleted():
    first = client.get("/api/plugins/research/notes").json()["notes"]
    assert len(first) == 1 and first[0]["title"] == "My research notes"      # a notebook to start in
    nid = client.post("/api/plugins/research/notes", json={"title": "GPUs"}).json()["id"]
    assert client.put(f"/api/plugins/research/notes/{nid}", json={"body": NOTES}).json()["ok"]
    assert client.get(f"/api/plugins/research/notes/{nid}").json()["body"] == NOTES
    assert client.put(f"/api/plugins/research/notes/{nid}", json={"title": "Free GPUs"}).status_code == 200
    assert client.get(f"/api/plugins/research/notes/{nid}").json()["body"] == NOTES   # a rename keeps the text
    assert client.delete(f"/api/plugins/research/notes/{nid}").status_code == 200
    assert client.get(f"/api/plugins/research/notes/{nid}").status_code == 404


def test_copilot_without_a_model_still_helps_and_says_so():
    for action in ("summarise", "outline", "analyse"):
        md = client.post("/api/plugins/research/copilot", json={"action": action, "text": NOTES, "title": "GPUs"}).json()
        assert md["model"] == "keyword picks" and "without a model" in md["markdown"]
    s = client.post("/api/plugins/research/copilot", json={"action": "summarise", "text": NOTES}).json()["markdown"]
    assert "Kaggle gives 30 hours" in s
    a = client.post("/api/plugins/research/copilot", json={"action": "analyse", "text": NOTES}).json()["markdown"]
    assert "Which one allows background training?" in a
    q = client.post("/api/plugins/research/copilot", json={"action": "ask", "text": NOTES,
                                                         "question": "How many hours does Kaggle give?"}).json()
    assert "Kaggle" in q["markdown"]
    src = client.post("/api/plugins/research/copilot", json={"action": "sources", "question": "free GPU"}).json()
    assert src["markdown"].startswith("### Sources to read: free GPU (sample results)") and "](https://" in src["markdown"]
    assert client.post("/api/plugins/research/copilot", json={"action": "delete everything"}).status_code == 400


def test_copilot_with_a_model_sends_the_notes(monkeypatch):
    import plugins.research as research
    seen = {}

    class Brain:
        model = "nemotron-3-nano:4b"

        def chat(self, messages, tools, max_tokens=600):
            seen["messages"] = messages
            return LLMReply(text="- Kaggle: 30 h a week\n- Colab: T4, about 12 h sessions")

    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(research.llm, "get_llm", lambda role="general": Brain())
    r = client.post("/api/plugins/research/copilot", json={"action": "summarise", "text": NOTES, "title": "GPUs"}).json()
    assert r["markdown"].startswith("- Kaggle: 30 h a week")
    assert "Lightning AI" in seen["messages"][1]["content"] and "Summarise" in seen["messages"][0]["content"]
