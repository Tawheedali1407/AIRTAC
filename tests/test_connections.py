"""The Connections page: keys saved from the app into the vault, never shown back, only from this PC."""
import json

import httpx
import keyring
import pytest
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend

from airtac import config, connections, providers, vault
from airtac.main import app

here = TestClient(app, base_url="http://localhost")
phone = TestClient(app, base_url="http://localhost", client=("100.64.0.7", 50000))
KEY = "gsk_test_" + "a" * 30 + "WXYZ"


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def get_password(self, service, username):
        return self.items.get((service, username))

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


@pytest.fixture
def store():
    old = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(old)


def cards():
    return {c["id"]: c for c in here.get("/api/connections").json()["connections"]}


def test_every_service_has_a_card_and_starts_off(store, monkeypatch):
    # The note is about gemini NOT being in [failover] order; Ali's own config lists it, so the test sets its own order.
    from airtac import config, connections
    monkeypatch.setattr(config, "FAILOVER", {**config.FAILOVER, "order": ["groq", "nebius", "nvidia", "openrouter"]})
    monkeypatch.delitem(connections.REGISTRY, "gemini", raising=False)   # its card is made again with this order
    c = cards()
    for cid in ("groq", "nebius", "nvidia", "openrouter", "gemini", "tavily", "github", "ollama", "phone"):
        assert cid in c, cid
    assert all(c[i]["status"] == "off" for i in ("groq", "nebius", "tavily", "github", "phone"))
    assert c["groq"]["group"] == "Brains" and c["tavily"]["plugin"] == "web" and c["github"]["plugin"] == "github"
    assert "train" in c["gemini"]["about"] and "[failover] order" in c["gemini"]["note"]   # not used until you choose to
    order = [x["id"] for x in here.get("/api/connections").json()["connections"]]
    assert order.index("groq") < order.index("nebius") < order.index("tavily") < order.index("ollama")


def test_save_keeps_the_key_in_the_vault_and_never_shows_it(store, monkeypatch):
    r = here.post("/api/connections/groq", json={"values": {"GROQ_API_KEY": KEY}})
    assert r.status_code == 200 and r.json()["saved_in"] == "vault"
    assert KEY not in r.text and KEY not in here.get("/api/connections").text
    card = cards()["groq"]
    assert card["status"] == "saved" and card["fields"][0]["shown"] == "••••WXYZ" and card["fields"][0]["source"] == "app"
    assert vault.get("keys", "GROQ_API_KEY", "key") == KEY and config.secret("GROQ_API_KEY") == KEY
    assert not (config.ROOT / ".env").exists() or KEY not in (config.ROOT / ".env").read_text()
    monkeypatch.setattr(config, "OFFLINE", False)
    assert providers.order()[0] == "groq"                  # the failover router uses it on the next question
    # An empty box keeps what's saved; unknown names are ignored.
    here.post("/api/connections/groq", json={"values": {"GROQ_API_KEY": "", "OTHER": "x"}})
    assert config.secret("GROQ_API_KEY") == KEY and not config.secret("OTHER")


def test_a_restart_reads_saved_keys_back(store):
    connections.save("tavily", {"TAVILY_API_KEY": "tvly-12345678901234"})
    config.SAVED.clear()
    assert config.secret("TAVILY_API_KEY") == ""
    assert connections.load_saved() == 1 and config.secret("TAVILY_API_KEY") == "tvly-12345678901234"


def test_only_this_pc_can_change_or_test_keys(store, monkeypatch):
    monkeypatch.setattr(config, "PASSCODE", "phone-pass-1")
    assert phone.post("/api/login", json={"passcode": "phone-pass-1"}).status_code == 200    # a signed-in phone
    assert phone.get("/api/connections").status_code == 403         # keys are seen on the PC only (Part G), not even listed
    for path in ("/api/connections/groq", "/api/connections/groq/test", "/api/connections/groq/disconnect"):
        r = phone.post(path, json={"values": {"GROQ_API_KEY": "evil-key-from-phone"}})
        assert r.status_code == 403, path
    assert config.secret("GROQ_API_KEY") == ""
    assert here.post("/api/connections/nope", json={"values": {}}).status_code == 404


def _client(status: int, body: dict | None = None):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body or {"choices": [{"message": {"content": "OK"}}]})
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_the_test_button_makes_one_real_call(store, monkeypatch):
    connections.save("nebius", {"NEBIUS_API_KEY": "nb-key-000011112222"})
    assert connections.run_test("nebius")["ok"] is False                               # offline mode: says so, no call
    monkeypatch.setattr(config, "OFFLINE", False)
    http, seen = _client(200)
    assert connections.run_test("nebius", http)["ok"] and cards()["nebius"]["status"] == "connected"
    assert seen[0].url.host == "api.tokenfactory.nebius.com" and seen[0].headers["authorization"] == "Bearer nb-key-000011112222"
    assert json.loads(seen[0].content)["max_tokens"] == 16      # a short reply: a few tokens
    http, _ = _client(401)
    result = connections.run_test("nebius", http)
    assert not result["ok"] and "refused" in result["message"] and cards()["nebius"]["status"] == "error"
    http, _ = _client(429)
    assert "out of free requests" in connections.run_test("nebius", http)["message"]
    assert connections.run_test("github")["message"].startswith("Fill in")              # nothing saved yet


def test_disconnect_and_keys_typed_into_env(store, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-from-env-file-9999")
    assert cards()["nvidia"]["fields"][0]["source"] == "env" and cards()["nvidia"]["status"] == "saved"
    connections.save("nvidia", {"NVIDIA_API_KEY": "nvapi-saved-in-app-1234"})
    assert config.secret("NVIDIA_API_KEY").endswith("1234")                             # the app wins over .env
    r = here.post("/api/connections/nvidia/disconnect").json()
    assert r["still_in_env"] == ["NVIDIA_API_KEY"] and vault.get("keys", "NVIDIA_API_KEY", "key") == ""
    assert config.secret("NVIDIA_API_KEY") == "nvapi-from-env-file-9999"


def test_phone_passcode(store):
    r = here.post("/api/connections/phone", json={"values": {"AIRTAC_PASSCODE": "short"}})
    assert r.status_code == 400 and "8 characters" in r.json()["detail"]
    here.post("/api/connections/phone", json={"values": {"AIRTAC_PASSCODE": "my-long-passcode-42"}})
    assert config.PASSCODE == "my-long-passcode-42"
    assert cards()["phone"]["fields"][0]["shown"] == "••••••••"                            # not even the last 4
    assert phone.post("/api/login", json={"passcode": "my-long-passcode-42"}).status_code == 200
    here.post("/api/connections/phone/disconnect")
    assert config.PASSCODE == ""


def test_no_safe_store_falls_back_to_the_env_file(tmp_path, monkeypatch):
    from keyring.backends import fail
    old = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    try:
        assert here.get("/api/connections").json()["store"]["vault"] is False
        assert connections.save("tavily", {"TAVILY_API_KEY": "tvly-abcdefgh12345"}) == ".env"
        assert "TAVILY_API_KEY=tvly-abcdefgh12345" in (tmp_path / ".env").read_text()
        assert config.secret("TAVILY_API_KEY") == "tvly-abcdefgh12345"
        connections.disconnect("tavily")
        assert "TAVILY_API_KEY" not in (tmp_path / ".env").read_text() and config.secret("TAVILY_API_KEY") == ""
    finally:
        keyring.set_keyring(old)


def test_line_breaks_are_refused(store):
    with pytest.raises(ValueError):
        connections.save("groq", {"GROQ_API_KEY": "abc\nEVIL=1"})
