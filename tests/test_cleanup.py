"""History and Memory and cache clean-up: only what you pick goes, and nothing of it lingers elsewhere."""
from fastapi.testclient import TestClient

from airtac import cleanup, db, memory, plugin_api
from airtac.main import app

client = TestClient(app, base_url="http://localhost")


def _chat(session, *pairs):
    ids = []
    for q, a in pairs:
        ids.append(memory.add_message(session, "user", q))
        memory.add_message(session, "assistant", a)
    return ids


def _count(sql, args=()):
    with db.connect() as conn:
        return conn.execute(sql, args).fetchone()[0]


def test_delete_one_question_takes_its_answer_only():
    ids = _chat("s1", ("When is my DBMS exam?", "On 12 Nov."), ("Explain AVL trees", "An AVL tree is..."))
    memory.add_feedback(ids[0] + 1, True, "")
    rows = client.get("/api/history").json()
    assert {r["id"] for r in rows} == set(ids)
    r = client.post("/api/history/delete", json={"questions": [ids[0]]})
    assert r.json()["deleted"] == 2
    left = [m["content"] for m in memory.session_messages("s1")]
    assert left == ["Explain AVL trees", "An AVL tree is..."]
    assert _count("SELECT COUNT(*) FROM feedback") == 0


def test_delete_whole_chats_and_their_traces():
    _chat("s1", ("a question", "an answer"), ("another", "reply"))
    _chat("s2", ("keep me", "kept"))
    memory.kv_set("session_tokens", "s1", '{"input": 5, "output": 7}')
    chats = {c["id"]: c for c in client.get("/api/history/chats").json()["chats"]}
    assert chats["s1"]["questions"] == 2 and chats["s1"]["title"] == "a question"
    client.post("/api/history/delete", json={"chats": ["s1"]})
    assert _count("SELECT COUNT(*) FROM messages WHERE session_id = 's1'") == 0
    assert _count("SELECT COUNT(*) FROM messages WHERE session_id = 's2'") == 2
    assert memory.kv_get("session_tokens", "s1") in (None, "")


def test_clear_all_needs_the_word():
    _chat("s1", ("x", "y"))
    assert client.post("/api/history/delete", json={"everything": "yes"}).status_code == 400
    assert client.post("/api/history/delete", json={"everything": "DELETE"}).json()["deleted"] == 2
    assert _count("SELECT COUNT(*) FROM messages") == 0


def test_memory_panel_lists_and_deletes_facts():
    memory.save_fact("my exam is on 12 Nov")
    memory.save_fact("I like dark mode")
    kinds = {k["id"]: k for k in client.get("/api/memory_cache").json()["kinds"]}
    assert kinds["facts"]["count"] == 2 and kinds["facts"]["items"]
    items = client.get("/api/memory_cache/facts").json()["items"]
    gone = next(i for i in items if "exam" in i["text"])
    assert client.post("/api/memory_cache/facts/delete", json={"id": gone["id"]}).json()["deleted"] == 1
    assert memory.recall("exam") == []                 # the search index forgot it too
    assert memory.recall("dark") == ["I like dark mode"]


def test_clear_caches_and_refuse_unknown_kinds():
    plugin_api._cache["GET x"] = (0.0, {"a": 1})
    memory.kv_set("brain_models", "groq", '{"at": 1, "ids": []}')
    assert client.post("/api/memory_cache/web_cache/delete", json={}).json()["deleted"] >= 1
    assert not plugin_api._cache
    assert client.post("/api/memory_cache/model_lists/delete", json={}).json()["deleted"] == 1
    assert client.post("/api/memory_cache/lessons/delete", json={}).status_code == 400   # lessons have their own page
    assert client.get("/api/memory_cache/tool_runs").status_code == 400


def test_summaries_follow_the_chat():
    _chat("s9", ("Plan my DSA week", "Here's a plan"), ("Add graphs on Friday", "Added"))
    from plugins import recall
    recall.update("s9")
    assert any(i["id"] == "s9" for i in cleanup.items("summaries"))
    cleanup.delete_chats(["s9"])
    assert not any(i["id"] == "s9" for i in cleanup.items("summaries"))
