"""Bug 1 (Ali, 20 Oct): the chat's model picker was empty. The menu lists every provider and its models, says which
need a key and whether it's set, never shows a blank Local group, answers fast (no network in the GET), and the
chosen model is the one that answers."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from airtac import agent, brains, config, llm, memory, providers
from airtac.main import app

client = TestClient(app, base_url="http://localhost")
ROOT = config.ROOT


@pytest.fixture
def ollama_mode(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "FAILOVER", {"order": ["groq"], "local_first": True})
    monkeypatch.setattr(providers, "airtac1_up", lambda timeout=1.0: False)
    monkeypatch.setattr(providers, "refresh_lists_soon", lambda: None)
    brains._cache.clear()
    yield
    brains._cache.clear()
    providers.ASKED.clear()
    providers.LAST.clear()
    for s in providers.STATE.values():
        s.resting_until = 0


def test_picker_lists_every_provider_with_key_state(ollama_mode, monkeypatch):
    monkeypatch.setattr(brains, "installed_models", lambda: ["nemotron-3-nano:4b", "qwen3:1.7b", "nomic-embed-text:latest"])
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_fake_123456789012"})
    r = client.get("/api/brain_choices")
    assert r.status_code == 200
    groups = {g["id"]: g for g in r.json()["groups"]}
    assert [i["id"] for i in groups["local"]["items"]] == ["ollama:nemotron-3-nano:4b", "ollama:qwen3:1.7b"]
    assert groups["local"]["note"] == "" and groups["local"]["reachable"] is True
    assert groups["groq"]["key"] == "set" and groups["groq"]["items"][0]["id"] == "groq"
    for needed in ("gemini", "xai", "openrouter", "nebius", "huggingface"):
        assert groups[needed]["key"] == "needed" and groups[needed]["key_env"]
    assert "gsk_fake" not in r.text


@pytest.mark.parametrize("installed, why", [(None, "isn't answering"), ([], "ollama pull"), (["nomic-embed-text:latest"], "ollama pull")])
def test_empty_local_group_says_check_ollama(ollama_mode, monkeypatch, installed, why):
    monkeypatch.setattr(brains, "installed_models", lambda: installed)
    local = providers.menu()[0]
    assert local["id"] == "local" and local["items"] == []         # never a model Ollama doesn't have
    assert local["note"].startswith("no models found — check Ollama") and why in local["note"]


def test_menu_never_waits_on_the_network(ollama_mode, monkeypatch):
    """The empty picker: the GET used to fetch each provider's /models (10 s each) while the page gave up at 10 s."""
    import httpx
    monkeypatch.setattr(brains, "installed_models", lambda: ["qwen3:1.7b"])
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_fake_123456789012", "OPENROUTER_API_KEY": "sk-or-fake-12345678"})
    memory.kv_set("brain_models", "groq", json.dumps({"at": 0, "ids": ["qwen/qwen3-32b"]}))   # a stale list
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in the menu")))
    groups = {g["id"]: g for g in providers.menu()}
    assert [i["id"] for i in groups["groq"]["items"]] == ["groq", "groq:qwen/qwen3-32b"]   # the saved copy


def test_stale_lists_refresh_in_the_background(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_fake_123456789012"})
    asked = []
    monkeypatch.setattr(providers, "live_models", lambda name, refresh=False: asked.append((name, refresh)) or [])
    providers.refresh_lists_soon()
    for _ in range(50):
        if asked:
            break
        time.sleep(0.02)
    assert ("groq", True) in asked
    memory.kv_set("brain_models", "groq", json.dumps({"at": time.time(), "ids": []}))
    asked.clear()
    providers.refresh_lists_soon()      # fresh now: nothing to do
    time.sleep(0.05)
    assert asked == []


class FakeOllama:
    """Stands in for one Ollama model; records which model answered."""
    answered: list = []

    def __init__(self, model=None, think=None, **_):
        self.model = model

    def chat(self, messages, tools, max_tokens=600):
        FakeOllama.answered.append(self.model)
        return llm.LLMReply(text=f"Hi from {self.model}.")


def test_picked_local_model_answers_the_chat(ollama_mode, monkeypatch):
    monkeypatch.setattr(brains, "installed_models", lambda: ["nemotron-3-nano:4b", "qwen3:1.7b"])
    monkeypatch.setattr(llm, "OllamaLLM", FakeOllama)
    FakeOllama.answered = []
    r = client.post("/api/chat", json={"message": "hello there", "session_id": "pick-1", "mode": "chat", "brain": "ollama:qwen3:1.7b"})
    assert r.status_code == 200, r.text
    assert FakeOllama.answered == ["qwen3:1.7b"]
    assert r.json()["brain"]["model"] == "qwen3:1.7b"


def test_picked_local_model_is_trusted_when_ollama_list_is_slow(ollama_mode, monkeypatch):
    monkeypatch.setattr(brains, "installed_models", lambda: None)    # /api/tags didn't answer in time
    monkeypatch.setattr(llm, "OllamaLLM", FakeOllama)
    assert brains.get("general", prefer="ollama:qwen3:1.7b").model == "qwen3:1.7b"


def test_picked_cloud_model_answers_the_chat(ollama_mode, monkeypatch):
    monkeypatch.setattr(brains, "installed_models", lambda: ["nemotron-3-nano:4b"])
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_fake_123456789012"})
    memory.kv_set("brain_models", "groq", json.dumps({"at": 9e12, "ids": ["qwen/qwen3-32b"]}))
    monkeypatch.setattr(llm, "OllamaLLM", FakeOllama)
    used = []

    class Cloud:
        def chat(self, messages, tools, max_tokens=600):
            return llm.LLMReply(text="Hi from Groq.")
    monkeypatch.setattr(providers, "build", lambda name, model="": used.append((name, model)) or Cloud())
    FakeOllama.answered = []
    r = client.post("/api/chat", json={"message": "hello there", "session_id": "pick-2", "mode": "chat", "brain": "groq:qwen/qwen3-32b"})
    assert r.status_code == 200, r.text
    assert used == [("groq", "qwen/qwen3-32b")] and FakeOllama.answered == []   # the pick goes first, not the PC model
    assert r.json()["brain"]["tier"] == "groq" and r.json()["brain"]["model"] == "qwen/qwen3-32b"


def test_chat_page_has_its_own_picker_remembered_per_chat():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "chat.js").read_text(encoding="utf-8")
    hub = (ROOT / "web" / "hub.js").read_text(encoding="utf-8")
    head = html[html.index('<header class="cp-head">'):html.index("</header>", html.index('<header class="cp-head">'))]
    assert 'id="cp-brain"' in head                                   # in the Chat page's header, not behind Setup
    assert "airtac-brain:${state.sid}" in js and "brain: brain() || undefined" in js
    assert "no models found — check Ollama" in hub and "key set" in hub and "Couldn't load the models" in hub
