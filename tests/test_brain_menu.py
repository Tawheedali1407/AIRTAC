"""The chat's brain menu (Part D): groups with key status, "provider:model" picks, and the fallback notice."""
import json

import pytest
from pydantic import ValidationError

from airtac import agent, brains, config, memory, providers
from airtac.models import ChatRequest


@pytest.fixture
def ollama_mode(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(brains, "installed_models", lambda: ["nemotron-3-nano:4b", "nomic-embed-text:latest", "qwen3:1.7b"])
    brains._cache.clear()
    yield
    brains._cache.clear()
    providers.ASKED.clear()
    providers.LAST.clear()


def test_menu_groups_and_key_status(ollama_mode, monkeypatch):
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_fake_123456789012"})
    memory.kv_set("brain_models", "groq", json.dumps({"at": 9e12, "ids": ["openai/gpt-oss-120b", "qwen/qwen3-32b"]}))
    groups = {g["id"]: g for g in providers.menu()}
    assert list(groups)[:8] == ["local", "airtac1", "gemini", "groq", "xai", "openrouter", "nebius", "huggingface"]
    assert [i["id"] for i in groups["local"]["items"]] == ["ollama:nemotron-3-nano:4b", "ollama:qwen3:1.7b"]   # no embedder
    assert groups["airtac1"]["note"].startswith("AIRTAC 1 isn't running") and not groups["airtac1"]["items"]
    assert groups["groq"]["key"] == "set" and groups["gemini"]["key"] == "needed"
    assert [i["id"] for i in groups["groq"]["items"]] == ["groq", "groq:qwen/qwen3-32b"]
    assert "together" not in groups                       # other brains show up once they have a key
    assert "gsk_fake" not in json.dumps(groups)


def test_pick_a_cloud_model(ollama_mode, monkeypatch):
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "gsk_fake_123456789012"})
    memory.kv_set("brain_models", "groq", json.dumps({"at": 9e12, "ids": ["qwen/qwen3-32b"]}))
    llm = brains.get("general", prefer="groq:qwen/qwen3-32b")
    assert llm.tiers[0] == "groq" and llm.models["groq"] == "qwen/qwen3-32b"
    assert providers.ASKED == {"provider": "groq", "model": "qwen/qwen3-32b"}
    llm = brains.get("general", prefer="groq:made-up-model")      # not on Groq's list: its default instead
    assert llm.tiers[0] == "groq" and "groq" not in llm.models


def test_pick_a_local_model(ollama_mode):
    llm = brains.get("general", prefer="ollama:qwen3:1.7b")
    assert llm.model == "qwen3:1.7b" and providers.ASKED["provider"] == "ollama"
    assert getattr(brains.get("general", prefer="ollama:not-downloaded:7b"), "model", "") != "not-downloaded:7b"


def test_fallback_notice_when_the_pick_is_busy(ollama_mode, monkeypatch):
    providers.ASKED.update(provider="groq", model="openai/gpt-oss-120b")
    providers.LAST.update(tier="nvidia", model="nvidia/nemotron-3-super-120b-a12b")
    providers.state("groq").last_error = "HTTP 429: rate limit reached"
    info = agent._brain_info("general")
    assert info["asked"] == "groq" and info["tier"] == "nvidia"
    assert info["fallback"] == "Groq (free tier) couldn't answer (out of free requests), so NVIDIA build (NIM) did."
    providers.LAST.update(tier="groq")
    assert "fallback" not in agent._brain_info("general")


def test_chat_request_accepts_only_menu_shaped_picks():
    assert ChatRequest(message="hi", brain="groq:openai/gpt-oss-120b").brain == "groq:openai/gpt-oss-120b"
    assert ChatRequest(message="hi", brain="ollama:qwen3:1.7b").brain
    for bad in ("groq; rm -rf", "GROQ", "groq:<script>", "a" * 200):
        with pytest.raises(ValidationError):
            ChatRequest(message="hi", brain=bad)


def test_live_list_is_cached_and_filtered(monkeypatch):
    import httpx
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "SAVED", {"OPENROUTER_API_KEY": "sk-or-fake-1234567890"})
    rows = {"data": [{"id": "nvidia/nemotron-3-super-120b-a12b:free"}, {"id": "openai/gpt-5"}, {"id": "x/embed-1:free"}]}
    calls = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: calls.append(url) or httpx.Response(200, json=rows, request=httpx.Request("GET", url)))
    assert providers.live_models("openrouter") == ["nvidia/nemotron-3-super-120b-a12b:free"]
    assert providers.live_models("openrouter") == ["nvidia/nemotron-3-super-120b-a12b:free"] and len(calls) == 1   # kept a day


def test_local_first_falls_back_to_the_cloud_and_rests(monkeypatch):
    from airtac.llm import LLMUnavailable
    monkeypatch.setattr(config, "FAILOVER", {"order": ["gemini"], "local_first": True})
    calls = []

    class Local:
        model = "nemotron-3-nano:4b"

        def chat(self, *a):
            calls.append("local")
            raise LLMUnavailable("Ollama couldn't load nemotron-3-nano:4b: only 1.4 GB of memory is free")

    class Cloud:
        def chat(self, *a):
            calls.append("gemini")
            return {"content": "Four."}
    monkeypatch.setattr(providers, "build", lambda name, model="": Cloud())
    monkeypatch.setattr(providers, "has_key", lambda name: True)
    providers.state("ollama").resting_until = 0
    providers.ASKED.clear()
    llm = providers.FailoverLLM(local=Local(), tiers=["gemini"])
    assert llm.chat([], [])["content"] == "Four." and calls == ["local", "gemini"]
    assert providers.LAST["tier"] == "gemini"
    llm.chat([], [])
    assert calls == ["local", "gemini", "gemini"]          # resting: no second 17-second wait for Ollama
    providers.state("ollama").resting_until = 0
