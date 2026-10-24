"""The brain failover: free cloud tiers in order, a tier rests after hitting its limit, the local model last."""
import httpx
import pytest

from airtac import brains, config, providers
from airtac.llm import LLMReply, LLMUnavailable, OpenAICompatLLM, QuotaExceeded


class FakeLocal:
    model = "nemotron-3-nano:4b"

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def chat(self, messages, tools, max_tokens=600):
        self.calls += 1
        if self.fail:
            raise LLMUnavailable("Ollama isn't running.")
        return LLMReply(text="from the PC")

    def status(self):
        return {"ready": True, "reason": ""}


class FakeTier:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    def chat(self, messages, tools, max_tokens=600):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return LLMReply(text=self.outcome)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    providers.STATE.clear()
    providers.LAST.clear()
    monkeypatch.setattr(config, "FAILOVER", {"enabled": True, "order": ["groq", "nvidia", "openrouter"],
                                             "cooldown_seconds": 600})
    yield
    providers.STATE.clear()
    providers.LAST.clear()


def router(tiers: dict, local=None):
    r = providers.FailoverLLM(local=local or FakeLocal(), tiers=list(tiers))
    r._clients.update(tiers)
    return r


def test_first_tier_answers():
    r = router({"groq": FakeTier("from groq"), "nvidia": FakeTier("from nvidia")})
    assert r.chat([], []).text == "from groq"
    assert providers.LAST == {"tier": "groq", "model": "openai/gpt-oss-120b"}


def test_rate_limit_moves_to_the_next_tier_and_rests_the_first():
    groq, nvidia = FakeTier(QuotaExceeded("429", retry_after=30)), FakeTier("from nvidia")
    r = router({"groq": groq, "nvidia": nvidia})
    assert r.chat([], []).text == "from nvidia"
    assert 25 <= providers.STATE["groq"].resting_until - __import__("time").time() <= 31   # Retry-After wins
    r.chat([], [])
    assert groq.calls == 1 and nvidia.calls == 2       # the resting tier isn't asked again


def test_everything_out_falls_back_to_the_pc():
    local = FakeLocal()
    r = router({"groq": FakeTier(QuotaExceeded("402")), "nvidia": FakeTier(LLMUnavailable("down"))}, local)
    assert r.chat([], []).text == "from the PC" and local.calls == 1
    assert providers.LAST["tier"] == "ollama"
    rows = {row["tier"]: row for row in providers.status()}
    assert rows["groq"]["resting_seconds"] > 500 and rows["nvidia"]["resting_seconds"] > 60


def test_no_internet_and_no_ollama_says_so():
    r = router({"groq": FakeTier(LLMUnavailable("can't reach"))}, FakeLocal(fail=True))
    with pytest.raises(LLMUnavailable, match="Ollama"):
        r.chat([], [])


def test_only_tiers_with_keys_and_never_offline(monkeypatch):
    assert providers.order() == []                        # no keys in tests: nothing leaves the PC
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    monkeypatch.setattr(config, "OFFLINE", False)
    assert providers.order() == ["nvidia"]
    monkeypatch.setattr(config, "OFFLINE", True)
    assert providers.order() == []
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "FAILOVER", {"enabled": False})
    assert providers.order() == []


def test_brains_use_the_router_only_when_a_tier_is_set_up(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    brains._cache.clear()
    assert not isinstance(brains.get("general"), providers.FailoverLLM)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "OFFLINE", False)
    got = brains.get("coding")
    assert isinstance(got, providers.FailoverLLM) and got.tiers == ["groq"]
    assert got.local.model == brains.model_for("coding")   # the role's own local model stays last
    brains._cache.clear()


def test_openai_compatible_client_reports_quota_errors():
    def handler(request):
        if "quota" in request.url.path:
            return httpx.Response(429, headers={"retry-after": "12"}, json={"error": "rate limit"})
        if "credits" in request.url.path:
            return httpx.Response(402, json={"error": "insufficient credits"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(QuotaExceeded) as e:
        OpenAICompatLLM("https://x/quota", "m", "k", client, max_429_retries=0).chat([], [])
    assert e.value.retry_after == 12
    with pytest.raises(QuotaExceeded):
        OpenAICompatLLM("https://x/credits", "m", "k", client).chat([], [])
    assert OpenAICompatLLM("https://x/ok", "m", "k", client).chat([], []).text == "hi"


def test_a_plugin_can_add_a_brain_without_touching_the_core(monkeypatch):
    from airtac import plugin_api
    p = plugin_api.LOADED["coach"]
    p.brain_provider("tinyllm", lambda model: FakeTier(f"tiny {model}"), label="Tiny", model="t1")
    try:
        monkeypatch.setattr(config, "FAILOVER", {"order": ["tinyllm"]})
        monkeypatch.setattr(config, "OFFLINE", False)
        assert providers.order() == ["tinyllm"]                # no key needed for this one
        r = providers.FailoverLLM(local=FakeLocal(), tiers=providers.order())
        assert r.chat([], []).text == "tiny t1"
    finally:
        providers.PROVIDERS.pop("tinyllm", None)


def test_brains_page_lists_the_tiers(monkeypatch):
    from fastapi.testclient import TestClient
    from airtac.main import app
    rows = TestClient(app, base_url="http://localhost").get("/api/brains").json()["tiers"]
    assert [r["tier"] for r in rows] == ["groq", "nvidia", "openrouter", "ollama"]
    assert not rows[0]["key"] and rows[-1]["note"].startswith("Private")
    assert all(isinstance(r["key"], bool) for r in rows)   # only "is a key set", never the key itself
