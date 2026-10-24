"""Bug 3 (Ali, 20 Oct): the chat sometimes froze like the Ollama model issue. Every model call from a chat has a
deadline for its first word and for the whole answer, Stop really cancels it (the stream is closed, so Ollama stops
generating), a stuck answer becomes a readable message with the cloud brains to try, and Ollama gets keep_alive 2m and
a modest context. No real model or network: Ollama is an httpx MockTransport that is slow on purpose."""
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from airtac import agent, brains, config, llm, memory, providers
from airtac.main import app

client = TestClient(app, base_url="http://localhost")


def _slow_ollama(first_after: float = 0.0, between: float = 0.0, pieces: int = 3, seen: dict | None = None):
    """An Ollama /api/chat that waits `first_after` s before its headers and `between` s between streamed lines."""
    produced = {"lines": 0}

    def handler(request):
        if seen is not None:
            seen["body"] = json.loads(request.content)
        time.sleep(first_after)

        def lines():
            for i in range(pieces):
                if between:
                    time.sleep(between)
                produced["lines"] += 1
                yield (json.dumps({"message": {"content": f"w{i} "}}) + "\n").encode()
            yield (json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode()
        return httpx.Response(200, content=lines())
    return httpx.Client(transport=httpx.MockTransport(handler)), produced


@pytest.fixture
def quick_guard(monkeypatch):
    monkeypatch.setattr(config, "CHAT_FIRST_TOKEN_SECONDS", 0.6)
    monkeypatch.setattr(config, "CHAT_TOTAL_SECONDS", 2.0)


def _with_guard(fn, guard=None):
    guard = guard or llm.new_guard()
    token = llm.GUARD.set(guard)
    try:
        return fn()
    finally:
        llm.GUARD.reset(token)


def test_chat_call_sends_keep_alive_2m_modest_context_and_streams(quick_guard):
    seen = {}
    http, _ = _slow_ollama(seen=seen)
    model = llm.OllamaLLM("http://ollama", "m", http_client=http)
    reply = _with_guard(lambda: model.chat([{"role": "user", "content": "hi"}], []))
    assert reply.text == "w0 w1 w2"
    body = seen["body"]
    assert body["keep_alive"] == "2m" and body["options"]["num_ctx"] == config.CHAT_NUM_CTX < config.LLM_NUM_CTX + 1
    assert body["stream"] is True                       # streamed even with no page watching (Home's /api/chat)
    with pytest.raises(ValueError):   # outside a chat (Work mode): not streamed (this fake always streams), old settings
        model.chat([{"role": "user", "content": "hi"}], [])
    assert seen["body"]["keep_alive"] == "10m" and seen["body"]["options"]["num_ctx"] == config.LLM_NUM_CTX


def test_model_that_never_starts_times_out_instead_of_hanging(quick_guard):
    http, _ = _slow_ollama(first_after=5.0)            # Ollama still loading the model
    model = llm.OllamaLLM("http://ollama", "m", http_client=http)
    began = time.monotonic()
    with pytest.raises(llm.TooSlow) as e:
        _with_guard(lambda: model.chat([{"role": "user", "content": "hi"}], []))
    assert time.monotonic() - began < 1.5               # the 0.6 s first-word deadline, not the 5 s wait
    assert "didn't start answering" in str(e.value)


def test_slow_answer_hits_the_total_deadline(quick_guard):
    http, produced = _slow_ollama(between=0.4, pieces=50)
    model = llm.OllamaLLM("http://ollama", "m", http_client=http)
    began = time.monotonic()
    with pytest.raises(llm.TooSlow) as e:
        _with_guard(lambda: model.chat([{"role": "user", "content": "hi"}], []))
    assert 1.8 < time.monotonic() - began < 3.0 and "longer than 2 seconds" in str(e.value)
    time.sleep(0.6)
    assert produced["lines"] < 10                        # the stream was closed: the model stopped generating


def test_stop_cancels_the_call_at_once(quick_guard):
    http, produced = _slow_ollama(between=0.2, pieces=100)
    model = llm.OllamaLLM("http://ollama", "m", http_client=http)
    guard = llm.new_guard()
    guard.total = 60
    threading.Timer(0.5, guard.cancel.set).start()
    began = time.monotonic()
    with pytest.raises(llm.Cancelled):
        _with_guard(lambda: model.chat([{"role": "user", "content": "hi"}], []), guard)
    assert time.monotonic() - began < 1.0
    time.sleep(0.5)
    assert produced["lines"] < 8


@pytest.fixture
def ollama_mode(monkeypatch, quick_guard):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(providers, "airtac1_up", lambda timeout=1.0: False)
    monkeypatch.setattr(brains, "installed_models", lambda: ["nemotron-3-nano:4b", "qwen3:1.7b"])
    brains._cache.clear()
    for s in providers.STATE.values():
        s.resting_until = 0
    yield
    brains._cache.clear()
    providers.ASKED.clear()
    providers.LAST.clear()
    for s in providers.STATE.values():
        s.resting_until = 0


def _local_models(monkeypatch, http):
    real = llm.OllamaLLM
    monkeypatch.setattr(llm, "OllamaLLM", lambda model=None, think=None, **k: real("http://ollama", model, http_client=http, think=think))


class Cloud:
    def chat(self, messages, tools, max_tokens=600):
        return llm.LLMReply(text="Hi from Groq.")


def test_stuck_local_model_falls_back_to_the_cloud(ollama_mode, monkeypatch):
    """Automatic, with a cloud key and local_first: Ollama hangs, so the cloud answers within seconds."""
    monkeypatch.setattr(config, "FAILOVER", {"order": ["groq"], "local_first": True})
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_fake_123456789012"})
    monkeypatch.setattr(providers, "build", lambda name, model="": Cloud())
    _local_models(monkeypatch, _slow_ollama(first_after=5.0)[0])
    began = time.monotonic()
    r = client.post("/api/chat", json={"message": "hello there", "session_id": "g-1", "mode": "chat"})
    assert r.status_code == 200 and time.monotonic() - began < 3
    assert r.json()["brain"]["tier"] == "groq" and "Groq" in r.json()["text"]


def test_stuck_picked_model_gives_a_message_and_offers_the_cloud(ollama_mode, monkeypatch):
    """The PC model picked in the menu (no failover): a readable message and the cloud brains to retry on."""
    monkeypatch.setattr(config, "FAILOVER", {"order": ["groq"], "local_first": True})
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_fake_123456789012"})
    _local_models(monkeypatch, _slow_ollama(first_after=5.0)[0])
    began = time.monotonic()
    r = client.post("/api/chat", json={"message": "hello there", "session_id": "g-2", "mode": "chat", "brain": "ollama:qwen3:1.7b"})
    d = r.json()
    assert r.status_code == 200 and time.monotonic() - began < 3
    assert d["problem"] == "timeout" and "didn't start answering" in d["text"]
    assert d["fallback_brains"] == [{"id": "groq", "label": "Groq (free tier)"}]
    assert "gsk_fake" not in r.text
    # Retrying on the cloud brain (the page's button sends brain="groq") answers there.
    monkeypatch.setattr(providers, "build", lambda name, model="": Cloud())
    r = client.post("/api/chat", json={"message": "hello there", "session_id": "g-2", "mode": "chat", "brain": "groq"})
    assert r.json()["brain"]["tier"] == "groq" and r.json()["problem"] == ""


def test_no_cloud_key_still_gives_a_message_not_a_hang(ollama_mode, monkeypatch):
    monkeypatch.setattr(config, "FAILOVER", {"order": ["groq"], "local_first": True})
    _local_models(monkeypatch, _slow_ollama(first_after=5.0)[0])
    d = client.post("/api/chat", json={"message": "hello there", "session_id": "g-3", "mode": "chat"}).json()
    assert d["problem"] == "timeout" and d["fallback_brains"] == [] and d["text"]


def test_stop_button_cancels_the_chat(ollama_mode, monkeypatch):
    """/api/kill with the chat's id (the Stop button) ends the answer now, while Ollama is mid-stream."""
    monkeypatch.setattr(config, "CHAT_TOTAL_SECONDS", 60)
    http, produced = _slow_ollama(between=0.2, pieces=200)
    _local_models(monkeypatch, http)
    out = {}
    t = threading.Thread(target=lambda: out.update(r=agent.chat("hello there", "g-stop", brain="ollama:qwen3:1.7b", mode="chat")))
    t.start()
    time.sleep(0.6)
    assert client.post("/api/kill", json={"session_id": "g-stop"}).status_code == 200
    t.join(2.0)
    assert not t.is_alive() and out["r"].problem == "stopped" and out["r"].fallback_brains == []
    time.sleep(0.5)
    assert produced["lines"] < 10


def test_stream_shows_thinking_while_waiting(ollama_mode, monkeypatch):
    monkeypatch.setattr(config, "CHAT_FIRST_TOKEN_SECONDS", 5)
    _local_models(monkeypatch, _slow_ollama(first_after=1.3)[0])
    r = client.post("/api/chat/stream", json={"message": "hello there", "session_id": "g-4", "brain": "ollama:qwen3:1.7b"})
    events = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    kinds = [e["type"] for e in events]
    assert "wait" in kinds and kinds.index("wait") < kinds.index("token") and kinds[-1] == "done"
    assert events[-1]["reply"]["text"] == "w0 w1 w2"


def test_chat_page_has_stop_thinking_and_fallback():
    js = (config.ROOT / "web" / "chat.js").read_text(encoding="utf-8")
    assert '"/api/kill", { session_id: state.sid }' in js and "Thinking…" in js
    assert 'ev.type === "wait"' in js and "fallbackBox" in js and "regenerate: true, brain: b.id" in js
    memory.kv_get("x", "y")   # the temp database is set up (conftest)


def test_a_stuck_model_isnt_asked_again_in_the_same_answer(quick_guard):
    """Seen in the browser check: after a stuck answer a plugin (the chat summary) asked Ollama again and waited out
    a second deadline. Now the rest of that turn's calls to the stuck brain fail at once."""
    http, _ = _slow_ollama(first_after=5.0)
    model = llm.OllamaLLM("http://ollama", "m", http_client=http)
    guard = llm.new_guard()
    with pytest.raises(llm.TooSlow):
        _with_guard(lambda: model.chat([{"role": "user", "content": "hi"}], []), guard)
    began = time.monotonic()
    with pytest.raises(llm.TooSlow) as e:
        _with_guard(lambda: llm.OllamaLLM("http://ollama", "other", http_client=http).chat([{"role": "user", "content": "x"}], []), guard)
    assert time.monotonic() - began < 0.2 and "stuck a moment ago" in str(e.value)
