"""AIRTAC 1 (F:\\Airtac1, port 8780) and AIRTAC Advance: the live entry in the brain menu, picks routed to AIRTAC 1's
OpenAI-compatible API, and the shared brains: AIRTAC 1's own token, accepted only from this PC on two routes."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from airtac import auth, brains, config, providers
from airtac.llm import LLMReply
from airtac.main import app

client = TestClient(app, base_url="http://localhost")


@pytest.fixture
def tokens_on(monkeypatch):
    monkeypatch.delenv("AIRTAC_NO_INSTALL_TOKEN", raising=False)
    yield auth.service_token()


def test_service_token_only_on_its_routes_and_only_from_this_pc(tokens_on, monkeypatch):
    token = tokens_on
    assert (config.DB_PATH.parent / "airtac1.token").read_text().strip() == token and len(token) >= 32
    head = {"X-AIRTAC1-Token": token}
    assert client.get("/api/health", headers=head).status_code == 200
    for path in ("/api/plugins/passwords/status", "/api/connections", "/api/devices", "/api/plugins"):
        assert client.get(path, headers=head).status_code == 403, path
    assert client.post("/api/brain/answer", json={"messages": [{"role": "user", "content": "hi"}]},
                       headers={"X-AIRTAC1-Token": "wrong" * 10}).status_code == 403
    far = {**head, "X-Forwarded-For": "192.168.1.7"}
    assert client.post("/api/brain/answer", json={"messages": [{"role": "user", "content": "hi"}]}, headers=far).status_code in (401, 403)
    assert client.post("/api/brain/answer", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 403


def test_shared_brains_answer_with_a_cloud_tier(tokens_on, monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "SAVED", {"GEMINI_API_KEY": "AIza" + "x" * 35})
    monkeypatch.setattr(config, "FAILOVER", {"order": ["gemini"], "enabled": True})

    class Fake:
        def chat(self, messages, tools, max_tokens=600):
            assert tools == [] and messages[-1]["content"] == "What is RAG?"
            return LLMReply(text="Retrieval-augmented generation.")
    monkeypatch.setattr(providers, "build", lambda name, model="": Fake())
    r = client.post("/api/brain/answer", json={"messages": [{"role": "user", "content": "What is RAG?"}]},
                    headers={"X-AIRTAC1-Token": tokens_on})
    assert r.status_code == 200 and r.json()["text"] == "Retrieval-augmented generation." and r.json()["tier"] == "gemini"
    assert "AIza" not in r.text
    monkeypatch.setattr(config, "FAILOVER", {"order": [], "enabled": False})
    r = client.post("/api/brain/answer", json={"messages": [{"role": "user", "content": "x"}]}, headers={"X-AIRTAC1-Token": tokens_on})
    assert r.status_code == 503


def test_menu_lists_airtac1_models_when_it_runs(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)

    def fake_get(url, **kw):
        req = httpx.Request("GET", url)
        if url.endswith("/brain/health"):
            return httpx.Response(200, json={"status": "ok"}, request=req)
        return httpx.Response(200, json={"data": [{"id": "auto"}, {"id": "nemotron-3-nano:4b"}]}, request=req)
    monkeypatch.setattr(httpx, "get", fake_get)
    g = providers.airtac1_group()
    assert [i["id"] for i in g["items"]] == ["airtac1:auto", "airtac1:nemotron-3-nano:4b"] and g["note"] == ""
    assert g["items"][0]["label"] == "Automatic (chat or coder)"


def test_a_pick_goes_to_airtac1_first(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(brains, "installed_models", lambda: ["nemotron-3-nano:4b"])
    brains._cache.clear()
    llm = brains.get("general", prefer="airtac1:auto")
    assert llm.tiers[0] == "airtac1" and providers.ASKED["provider"] == "airtac1"
    client_obj = providers.build("airtac1")
    assert client_obj.base_url == "http://127.0.0.1:8780/v1" and client_obj.api_key == ""
    assert "airtac1" not in providers.wanted()      # never a failover tier by itself
    brains._cache.clear()
    providers.ASKED.clear()
    assert json.dumps(providers.PROVIDERS["airtac1"].label) == '"AIRTAC 1"'
