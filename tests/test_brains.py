"""Brains: each kind of question goes to the right model, and the model client copes with models
that lack a thinking switch or tool support."""
import json

import httpx
import pytest

from airtac import agent, brains, config, memory
from airtac.llm import LLMReply, LLMUnavailable, MockLLM, OllamaLLM


@pytest.mark.parametrize("message,role", [
    ("What are the symptoms of dengue fever?", "medical"),
    ("Is it safe to take two paracetamol tablets with a headache?", "medical"),
    ("Fix this python function that throws an exception", "coding"),
    ("Explain dynamic programming with an example in java", "coding"),
    ("Derive the equivalent resistance of two resistors in a circuit", "reasoning"),
    ("Calculate the torque on a beam of 3 m", "reasoning"),
    ("What's the capital of Kerala?", "general"),
])
def test_questions_go_to_the_right_brain(message, role):
    assert brains.route(message) == role


def test_the_app_you_are_in_decides_first():
    assert brains.route("what's the weather", "practice") == "coding"


def test_roles_use_their_own_model_or_the_general_one(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3.5:4b")
    monkeypatch.setattr(config, "BRAINS", {"medical": "hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M", "coding": ""})
    brains._cache.clear()
    assert brains.model_for("coding") == "qwen3.5:4b"
    med = brains.get("medical")
    assert isinstance(med, OllamaLLM) and med.model.startswith("hf.co/unsloth/medgemma")
    assert brains.get("reasoning").think is True and brains.get("general").think is False
    assert brains.get("general") is brains.get("general")  # one shared client per model
    brains._cache.clear()
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    assert isinstance(brains.get("medical"), MockLLM)


def _ollama(handler):
    return OllamaLLM(base_url="http://ollama.test", model="m", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ollama_drops_think_and_tools_when_a_model_cant_use_them():
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(sorted(k for k in ("think", "tools") if k in body))
        if "think" in body:
            return httpx.Response(400, json={"error": "model does not support thinking"})
        if "tools" in body:
            return httpx.Response(400, json={"error": "registry.ollama.ai/m does not support tools"})
        return httpx.Response(200, json={"message": {"content": "Hello"}})
    llm = _ollama(handler)
    tool = [{"type": "function", "function": {"name": "x", "description": "", "parameters": {}}}]
    assert llm.chat([{"role": "user", "content": "hi"}], tool).text == "Hello"
    assert sent == [["think", "tools"], ["tools"], []]
    assert llm.chat([{"role": "user", "content": "hi"}], tool).text == "Hello"  # remembered: one call
    assert sent[-1] == []


def test_ollama_tool_calls_and_friendly_errors(monkeypatch):
    monkeypatch.setattr(config, "LLM_FALLBACK", "")
    def tool_call(request):
        return httpx.Response(200, json={"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "gpus"}}}]}, "eval_count": 5})
    reply = _ollama(tool_call).chat([{"role": "user", "content": "x"}], [])
    assert reply.tool_call.name == "web_search" and reply.tool_call.args == {"query": "gpus"}
    with pytest.raises(LLMUnavailable, match="ollama pull m"):
        _ollama(lambda r: httpx.Response(404, json={"error": "model not found"})).chat([{"role": "user", "content": "x"}], [])

    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(LLMUnavailable, match="Ollama isn't running"):
        _ollama(down).chat([{"role": "user", "content": "x"}], [])


class Recorder:
    """A fake model that answers in words and keeps what it was sent."""

    def __init__(self, text="Fine."):
        self.text, self.calls = text, []

    def chat(self, messages, tools, max_tokens=600):
        self.calls.append({"messages": messages, "tools": tools})
        return LLMReply(text=self.text)


def test_a_medical_question_gets_no_tools_a_safety_prompt_and_a_note():
    llm = Recorder("Dengue causes high fever and body aches. See a doctor if it lasts.")
    reply = agent.chat("What are the symptoms of dengue fever?", llm=llm)
    assert llm.calls[0]["tools"] == []
    assert "112" in llm.calls[0]["messages"][0]["content"]
    assert reply.brain["role"] == "medical" and "not medical advice" in reply.screen


def test_lessons_from_corrections_reach_the_model():
    first = agent.chat("What's the capital of Kerala?", llm=Recorder("Kochi."))
    memory.add_feedback(first.message_id, False, "It's Thiruvananthapuram, not Kochi.")
    llm = Recorder("Thiruvananthapuram.")
    agent.chat("Tell me the capital of Kerala again", llm=llm)
    assert "Thiruvananthapuram, not Kochi" in llm.calls[0]["messages"][0]["content"]


def test_nemotron_is_the_default_brain_and_a_role_can_use_the_cloud(monkeypatch):
    from airtac.llm import OpenAICompatLLM
    assert config.RAW["llm"]["model"] == "nemotron-3-nano:4b"
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "BRAINS", {"reasoning": "nebius:nvidia/nemotron-3-super-120b-a12b"})
    brains._cache.clear()
    assert isinstance(brains.get("reasoning"), OllamaLLM)          # no key: stays on this PC
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    from airtac import providers
    routed = brains.get("reasoning")                               # the cloud Nemotron first, this PC last
    assert isinstance(routed, providers.FailoverLLM) and routed.tiers[0] == "nebius"
    assert isinstance(routed.local, OllamaLLM) and routed.local.model == config.LLM_MODEL
    cloud = routed._client("nebius")
    assert isinstance(cloud, OpenAICompatLLM) and cloud.model == "nvidia/nemotron-3-super-120b-a12b"
    assert cloud.base_url == config.NEBIUS_BASE_URL
    brains._cache.clear()


def test_a_missing_model_falls_back_to_the_backup_brain(monkeypatch):
    monkeypatch.setattr(config, "LLM_FALLBACK", "qwen3.5:4b")
    asked = []

    def handler(request):
        body = json.loads(request.content)
        asked.append(body["model"])
        if body["model"] == "m":
            return httpx.Response(404, json={"error": "model 'm' not found"})
        return httpx.Response(200, json={"message": {"content": "Hi from the backup."}})
    assert _ollama(handler).chat([{"role": "user", "content": "x"}], []).text == "Hi from the backup."
    assert asked == ["m", "qwen3.5:4b"]


def test_ollama_with_no_models_says_where_to_look():
    """Ollama runs but lists nothing (OLLAMA_MODELS not picked up after a move): say so, not "pull"."""
    def handler(r):
        if r.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404, json={"error": "model not found"})
    with pytest.raises(LLMUnavailable, match="sees no models.*OLLAMA_MODELS"):
        _ollama(handler).chat([{"role": "user", "content": "x"}], [])


def test_when_every_brain_fails_the_answer_says_why(monkeypatch):
    from airtac import providers
    monkeypatch.setattr(providers, "STATE", {})
    providers.rest("ollama", 120, "Ollama is running but sees no models.")
    monkeypatch.setattr(providers, "has_key", lambda n: n == "gemini")
    providers.rest("gemini", 120, "503")
    text = providers.all_failed_text(["gemini", "groq"])
    assert text.startswith("Ollama is running but sees no models.") and "busy (tried again in 2 min)" in text
    assert "503" not in text
    monkeypatch.setattr(providers, "has_key", lambda n: False)
    assert "add a free one" in providers.all_failed_text(["gemini", "groq"])


def test_a_busy_model_falls_back_to_another_model_of_the_same_provider(monkeypatch):
    import json as _json
    from airtac import memory, providers
    from airtac.llm import LLMReply, LLMUnavailable
    monkeypatch.setattr(providers, "STATE", {})
    memory.kv_set("brain_models", "gemini", _json.dumps({"at": 1, "ids": ["gemini-x-pro", "gemini-x-flash", "gemini-x-flash-lite"]}))
    assert providers.alternates("gemini", "gemini-x-pro") == ["gemini-x-flash-lite", "gemini-x-flash"]

    class Fake:
        def __init__(self, model):
            self.model = model

        def chat(self, *a, **k):
            if self.model == "gemini-x-pro":
                raise LLMUnavailable("The model server answered with an error (503).")
            return LLMReply(text=f"hi from {self.model}")
    monkeypatch.setattr(providers, "build", lambda name, model="": Fake(model or "gemini-x-pro"))
    f = providers.FailoverLLM(local=None, tiers=["gemini"], models={"gemini": "gemini-x-pro"})
    monkeypatch.setattr(providers.config, "FAILOVER", {"local_first": False})
    assert f.chat([{"role": "user", "content": "x"}], []).text == "hi from gemini-x-flash-lite"
