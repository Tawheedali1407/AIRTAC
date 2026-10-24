"""The tokens plugin: counts per brain per day, daily limits that skip a cloud brain, answer length."""
import json

import httpx
from fastapi.testclient import TestClient

from airtac import agent, config, memory, plugins, providers
from airtac.llm import LLMReply, OllamaLLM, OpenAICompatLLM
from airtac.main import app

here = TestClient(app, base_url="http://localhost")
phone = TestClient(app, base_url="http://localhost", client=("100.64.0.7", 50000))
tokens = plugins.get("tokens")


def summary():
    return here.get("/api/plugins/tokens/summary").json()


def test_cloud_and_local_answers_are_counted():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if request.url.path.endswith("/api/chat"):   # Ollama's own API
            return httpx.Response(200, json={"message": {"content": "hi"}, "prompt_eval_count": 40, "eval_count": 10})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 300, "completion_tokens": 50}})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    OpenAICompatLLM(base_url="https://api.groq.com/openai/v1", model="m", api_key="k", http_client=http, tier="groq").chat(
        [{"role": "user", "content": "hello"}], [])
    OllamaLLM(model="nemotron-3-nano:4b", http_client=http).chat([{"role": "user", "content": "hello"}], [])
    s = summary()
    by = {t["tier"]: t for t in s["tiers"]}
    assert by["groq"]["used"] == 350 and by["groq"]["calls"] == 1 and by["ollama"]["used"] == 50
    assert s["today"] == 400 and s["cloud_today"] == 350 and s["week"][-1]["tokens"] == 400
    assert seen[0]["max_tokens"] == 600 and seen[1]["options"]["num_predict"] == 600   # Normal length: unchanged


def test_a_cloud_brain_over_its_limit_is_skipped_until_midnight(monkeypatch):
    monkeypatch.setattr(config, "FAILOVER", {"enabled": True, "order": ["groq", "nvidia"], "cooldown_seconds": 600})
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(providers, "has_key", lambda name: True)
    providers.STATE.clear()
    assert here.post("/api/plugins/tokens/settings", json={"limits": {"groq": 1000}}).status_code == 200
    tokens.hooks["usage"]("groq", "m", 900, 50)
    assert providers.state("groq").resting_until == 0

    class Tier:
        def __init__(self, text):
            self.text, self.calls = text, 0

        def chat(self, messages, tools, max_tokens=600):
            self.calls += 1
            return LLMReply(text=self.text)
    groq, nvidia = Tier("from groq"), Tier("from nvidia")
    router = providers.FailoverLLM(local=Tier("from the PC"), tiers=["groq", "nvidia"])
    router._clients = {"groq": groq, "nvidia": nvidia}
    assert router.chat([{"role": "user", "content": "x"}], []).text == "from groq"        # 950 of 1,000: still fine
    tokens.hooks["usage"]("groq", "m", 60, 0)                                            # 1,010: over
    assert router.chat([{"role": "user", "content": "x"}], []).text == "from nvidia"
    assert groq.calls == 1
    s = summary()
    g = next(t for t in s["tiers"] if t["tier"] == "groq")
    assert g["paused"] and g["left"] == 0 and g["limit"] == 1000
    titles = [n["title"] for n in memory.notifications_after(0)]
    assert any("80%" in t for t in titles) and any("reached today's token limit" in t for t in titles)
    # Your PC is never skipped, and 0 means no limit.
    assert plugins.brain_gate("ollama", 600) == 600
    here.post("/api/plugins/tokens/settings", json={"limits": {"groq": 0, "nebius": 0}})
    assert plugins.brain_gate("groq", 600) == 600 and plugins.brain_gate("nebius", 600) == 600


def test_answer_length_and_settings_only_from_this_pc():
    assert summary()["length"] == "normal"
    assert here.post("/api/plugins/tokens/settings", json={"length": "short"}).json()["length"] == "short"
    assert plugins.brain_gate("groq", 600) == 300 and plugins.brain_gate("groq", 200) == 200
    here.post("/api/plugins/tokens/settings", json={"length": "long"})
    assert plugins.brain_gate("groq", 600) == 1400
    assert here.post("/api/plugins/tokens/settings", json={"length": "huge"}).status_code == 400
    assert here.post("/api/plugins/tokens/settings", json={"limits": {"groq": -5}}).status_code == 400
    assert phone.post("/api/plugins/tokens/settings", json={"length": "short"}).status_code in (401, 403)
    assert summary()["length"] == "long"


def test_ask_by_chat():
    tokens.hooks["usage"]("nebius", "nvidia/nemotron", 1200, 300)
    r = agent.chat("How many tokens did I use today?")
    assert r.steps[0].tool == "token_usage" and "1,500" in r.spoken
    assert "limit 100,000" in r.screen     # Nebius is paid credits: a daily limit out of the box
