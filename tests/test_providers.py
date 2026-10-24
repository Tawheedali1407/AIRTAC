"""The AI brains you can add with an API key: Claude, OpenAI, DeepSeek, Mistral, xAI, Hugging Face,
plus Perplexity as a tool. All offline: the HTTP calls go to a fake server."""
import json

import httpx
from fastapi.testclient import TestClient

from airtac import agent, brains, config, providers
from airtac.llm import OpenAICompatLLM


def test_every_new_provider_is_listed_with_its_key_page():
    for name in ("anthropic", "openai", "deepseek", "mistral", "xai", "huggingface"):
        p = providers.PROVIDERS[name]
        assert p.base_url.startswith("https://") and p.key_env.endswith(("_API_KEY", "_TOKEN")) and p.get_key.startswith("https://")


def test_a_saved_key_is_enough_but_training_tiers_wait(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "FAILOVER", {"order": ["groq", "nebius"]})
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "g" * 20, "ANTHROPIC_API_KEY": "a" * 20,
                                         "HF_TOKEN": "h" * 20, "MISTRAL_API_KEY": "m" * 20})
    order = providers.order()
    assert order[0] == "groq"                                   # the configured order comes first
    assert order.index("huggingface") < order.index("anthropic")   # free before paid
    assert "mistral" not in order                               # may train on your data: only when you list it
    monkeypatch.setattr(config, "FAILOVER", {"order": ["groq"], "skip": ["anthropic"]})
    assert "anthropic" not in providers.order()


def _fake(seen):
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_claude_gets_no_temperature_and_openai_gets_its_token_field():
    seen = []
    a = providers.PROVIDERS["anthropic"]
    OpenAICompatLLM(base_url=a.base_url, model="m", api_key="k", http_client=_fake(seen), tier="anthropic",
                    temperature=a.temperature).chat([{"role": "user", "content": "x"}], [])
    assert "temperature" not in seen[0]                         # newer Claude models refuse values below 1
    o = providers.PROVIDERS["openai"]
    OpenAICompatLLM(base_url=o.base_url, model="m", api_key="k", http_client=_fake(seen), tier="openai",
                    temperature=o.temperature, token_param=o.token_param, token_scale=o.token_scale,
                    extra=o.extra).chat([{"role": "user", "content": "x"}], [], max_tokens=600)
    assert seen[1]["max_completion_tokens"] == 1800 and "max_tokens" not in seen[1] and seen[1]["reasoning_effort"] == "low"


def test_the_chat_can_ask_a_picked_brain_first(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "FAILOVER", {"order": ["groq"]})
    monkeypatch.setattr(config, "SAVED", {"GROQ_API_KEY": "g" * 20, "MISTRAL_API_KEY": "m" * 20})
    brains._cache.clear()
    assert brains.get("general").tiers == ["groq"]
    assert brains.get("general", prefer="mistral").tiers == ["mistral", "groq"]   # picked in the chat header
    assert brains.get("general", prefer="xai").tiers == ["groq"]                  # no key: ignored
    brains._cache.clear()


def test_paid_brains_start_with_a_daily_limit():
    from plugins.tokens import settings
    limits = settings()["limits"]
    assert all(limits[n] > 0 for n in ("anthropic", "openai", "deepseek", "xai", "perplexity"))


def test_brain_menu_lists_only_names_never_keys(monkeypatch):
    monkeypatch.setattr(config, "SAVED", {"DEEPSEEK_API_KEY": "sk-secret-123456789"})
    data = TestClient(__import__("airtac.main", fromlist=["app"]).app, base_url="http://localhost").get("/api/brain_choices").json()
    assert [c["id"] for c in data["choices"]] == ["deepseek"] and data["choices"][0]["logo"] == "logos/deepseek.svg"
    assert "sk-secret" not in json.dumps(data)


def test_the_reply_carries_this_session_s_tokens():
    from airtac.llm import _report
    first = agent.chat("hello there", "tok-session")
    assert first.usage["session"]["total"] == 0                 # keyword rules use no tokens
    _report("groq", "m", 100, 20)                                # as if a brain answered this message
    usage = agent._session_usage("tok-session")
    assert usage["turn"] == {"input": 100, "output": 20, "total": 120} and usage["session"]["total"] == 120
    _report("groq", "m", 10, 5)
    assert agent._session_usage("tok-session")["session"] == {"input": 110, "output": 25, "total": 135}


def test_perplexity_is_a_tool_with_sources_and_needs_its_key():
    from airtac import tools
    t = tools.REGISTRY["ask_perplexity"]
    assert t.needs == ("PERPLEXITY_API_KEY",) and t.risk == "read"
    r = tools.pick(t)("What's new in Python 3.14?")             # offline tests: the recorded answer
    assert "Sources:" in r.screen and "https://docs.python.org" in r.screen and "https" not in r.spoken
