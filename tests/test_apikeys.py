"""APIs and keys (Part D): the catalog, the vault gate, Show/Copy, each Test call (faked), custom keys,
and the request signing checked against the official SDKs' output."""
import base64
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from airtac import config, connections
from plugins.passwords import store

PASS = "correct horse battery"
COPIED: list = []


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    from plugins.passwords import clipboard
    monkeypatch.setattr(store, "ARGON", {"m": 1024, "p": 1})
    monkeypatch.setattr(store, "TARGET_SECONDS", 0.001)
    monkeypatch.setattr(store, "_reveals", __import__("collections").deque())
    COPIED.clear()
    monkeypatch.setattr(clipboard, "copy", lambda text, clear_after=20: COPIED.append(text) or True)
    store.lock()
    store._state.update(service=None, weak=False)
    store.setup(PASS)                     # setting up leaves it unlocked
    yield
    store.lock()
    store._state.update(service=None)


def _client():
    from airtac.main import app
    return TestClient(app, base_url="http://localhost")


def _cards():
    return {c["id"]: c for c in connections.all_connectors()}


def _test(cid, handler, monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    return connections.run_test(cid, httpx.Client(transport=httpx.MockTransport(handler)))


# ---------------------------------------------------------------- the catalog

def test_catalog_has_every_card_in_its_group():
    cards = _cards()
    want = {"brave_search": "Search and dev tools", "serpapi": "Search and dev tools", "context7": "Search and dev tools",
            "coderabbit": "Search and dev tools", "tavily": "Search and dev tools", "google_cse": "Search and dev tools",
            "github": "Search and dev tools", "nebius_storage": "Hackathon and cloud", "sap_ai_core": "Hackathon and cloud",
            "sap_btp": "Hackathon and cloud", "aws_s3": "Hackathon and cloud", "cloudflare_r2": "Hackathon and cloud",
            "gcs": "Hackathon and cloud", "azure_blob": "Hackathon and cloud", "dropbox": "Hackathon and cloud",
            "supabase": "Hackathon and cloud", "google_drive": "Hackathon and cloud", "together": "Brains", "nebius": "Brains"}
    for cid, group in want.items():
        assert cards[cid]["group"] == group, cid
    assert cards["google_drive"]["status"] == "info" and not cards["google_drive"]["fields"]
    assert cards["sap_ai_core"]["fields"][0]["multiline"] is True
    assert all(c["docs"].startswith("https://") for c in cards.values() if c["plugin"] == "apikeys" and c["can_test"] and not c["custom"])


# ---------------------------------------------------------------- the vault gate

def test_locked_vault_hides_the_page_and_refuses_changes():
    client = _client()
    client.post("/api/connections/brave_search", json={"values": {"BRAVE_API_KEY": "BSA-fake-key-1234567890"}})
    assert _cards()["brave_search"]["fields"][0]["shown"] == "••••7890"      # unlocked: the last 4
    store.lock()
    assert client.get("/api/connections").status_code == 423
    assert client.post("/api/connections/brave_search", json={"values": {"BRAVE_API_KEY": "x" * 20}}).status_code == 423
    assert client.post("/api/connections/brave_search/disconnect").status_code == 423
    assert client.post("/api/connections/brave_search/reveal", json={"key": "BRAVE_API_KEY"}).status_code == 423
    assert _cards()["brave_search"]["fields"][0]["shown"] == "••••••••"      # locked: nothing at all
    assert config.secret("BRAVE_API_KEY") == "BSA-fake-key-1234567890"       # but the key still works for calls


def test_phone_can_never_see_the_keys():
    from airtac.main import app
    phone = TestClient(app, base_url="http://testserver")
    assert phone.get("/api/connections").status_code in (401, 403)


def test_show_and_copy_are_audited_and_rate_limited():
    client = _client()
    client.post("/api/connections/serpapi", json={"values": {"SERPAPI_API_KEY": "serp-secret-abcdef123456"}})
    r = client.post("/api/connections/serpapi/reveal", json={"key": "SERPAPI_API_KEY"})
    assert r.json() == {"value": "serp-secret-abcdef123456", "hide_after": 10}
    assert r.headers["cache-control"] == "no-store"
    assert client.post("/api/connections/serpapi/copy", json={"key": "SERPAPI_API_KEY"}).json()["clears_in"] == 20
    assert COPIED == ["serp-secret-abcdef123456"]
    audit = (config.DB_PATH.parent / "logs" / "audit.log").read_text()
    assert "revealed SERPAPI_API_KEY (serpapi)" in audit and "copied SERPAPI_API_KEY" in audit
    assert "serp-secret" not in audit
    codes = [client.post("/api/connections/serpapi/reveal", json={"key": "SERPAPI_API_KEY"}).status_code for _ in range(25)]
    assert 429 in codes
    assert client.post("/api/connections/serpapi/reveal", json={"key": "GROQ_API_KEY"}).status_code == 400


# ---------------------------------------------------------------- Test buttons (fake services)

def _save(cid, values):
    connections.save(cid, values)


def test_brave_and_serpapi_and_context7_and_coderabbit(monkeypatch):
    _save("brave_search", {"BRAVE_API_KEY": "BSA-fake-1234567890"})
    seen = {}

    def brave(req):
        seen["token"] = req.headers.get("x-subscription-token")
        return httpx.Response(200, json={"web": {"results": []}})
    assert _test("brave_search", brave, monkeypatch)["ok"] and seen["token"] == "BSA-fake-1234567890"

    _save("serpapi", {"SERPAPI_API_KEY": "serp-fake-1234567890"})
    r = _test("serpapi", lambda req: httpx.Response(200, json={"total_searches_left": 87}), monkeypatch)
    assert r["message"] == "Connected. 87 searches left this month."
    r = _test("serpapi", lambda req: httpx.Response(401, json={"error": "Invalid API key serp-fake-1234567890"}), monkeypatch)
    assert not r["ok"] and "refused" in r["message"]

    _save("context7", {"CONTEXT7_API_KEY": "ctx7sk-fake-1234567890"})
    r = _test("context7", lambda req: httpx.Response(200 if req.headers["authorization"] == "Bearer ctx7sk-fake-1234567890" else 401,
                                                     text="docs"), monkeypatch)
    assert r["ok"]
    _save("coderabbit", {"CODERABBIT_API_KEY": "cr-fake-1234567890"})
    r = _test("coderabbit", lambda req: httpx.Response(403, json={"error": "enterprise only"}), monkeypatch)
    assert r["ok"] and "Enterprise" in r["message"]


def test_s3_style_storage_signs_and_reads_errors(monkeypatch):
    _save("nebius_storage", {"NEBIUS_S3_KEY_ID": "NBKEYID1234567", "NEBIUS_S3_SECRET": "nebius-secret-abcdefgh"})
    seen = {}

    def ok(req):
        seen.update(url=str(req.url), auth=req.headers["authorization"])
        return httpx.Response(200, text="<ListAllMyBucketsResult><Buckets><Bucket><Name>a</Name></Bucket>"
                                        "<Bucket><Name>b</Name></Bucket></Buckets></ListAllMyBucketsResult>")
    r = _test("nebius_storage", ok, monkeypatch)
    assert r["message"] == "Connected. 2 buckets found."
    assert seen["url"] == "https://storage.eu-north1.nebius.cloud/"
    assert "Credential=NBKEYID1234567/" in seen["auth"] and "/eu-north1/s3/aws4_request" in seen["auth"]
    assert "nebius-secret" not in seen["auth"]
    bad = lambda req: httpx.Response(403, text="<Error><Code>SignatureDoesNotMatch</Code></Error>")
    assert not _test("nebius_storage", bad, monkeypatch)["ok"]
    denied = lambda req: httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>")
    assert _test("nebius_storage", denied, monkeypatch)["ok"]

    _save("cloudflare_r2", {"R2_ACCOUNT_ID": "not-an-id", "R2_ACCESS_KEY_ID": "r2id12345678", "R2_SECRET_ACCESS_KEY": "r2secret12345678"})
    assert "32-character" in _test("cloudflare_r2", ok, monkeypatch)["message"]
    _save("cloudflare_r2", {"R2_ACCOUNT_ID": "0123456789abcdef0123456789abcdef"})
    _test("cloudflare_r2", ok, monkeypatch)
    assert seen["url"] == "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com/" and "/auto/s3/" in seen["auth"]


def test_signing_matches_the_official_sdks():
    """Expected values were produced by botocore 1.43 (S3SigV4Auth) and azure-storage-blob 12.31 (SharedKeyCredentialPolicy)."""
    from plugins.apikeys import signing
    now = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
    aws = signing.sigv4("GET", "s3.eu-west-1.amazonaws.com", "/", {}, "eu-west-1", "AKIDEXAMPLE",
                        "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", now=now)
    assert aws["Authorization"].endswith("Signature=1be0e0f30a5a174c3d43460f8a5aa1d4aa52372d114be6e157cb2660e515afbd")
    az = signing.azure_shared_key("myacct", base64.b64encode(b"k" * 64).decode(), {"comp": "list"}, now=now)
    assert az["Authorization"] == "SharedKey myacct:Ohr48g2Ia/2a+7jDg+0X9PE4GE2kO7Fig97HmsGouQM="


def test_google_cloud_storage_service_account(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    info = {"type": "service_account", "project_id": "airtac-demo", "private_key_id": "k1", "private_key": pem,
            "client_email": "bot@airtac-demo.iam.gserviceaccount.com", "token_uri": "https://evil.example/token"}
    _save("gcs", {"GCS_SERVICE_ACCOUNT_JSON": json.dumps(info, indent=2)})       # a pasted, multi-line file
    hosts = []

    def google(req):
        hosts.append(req.url.host)
        if req.url.host == "oauth2.googleapis.com":
            assert b"jwt-bearer" in req.content
            return httpx.Response(200, json={"access_token": "ya29.fake"})
        assert req.headers["authorization"] == "Bearer ya29.fake" and req.url.params["project"] == "airtac-demo"
        return httpx.Response(200, json={"items": [{"name": "x"}]})
    r = _test("gcs", google, monkeypatch)
    assert r["message"] == "Connected. 1 buckets in airtac-demo."
    assert hosts == ["oauth2.googleapis.com", "storage.googleapis.com"]          # never the token_uri in the file


def test_azure_dropbox_supabase(monkeypatch):
    _save("azure_blob", {"AZURE_STORAGE_ACCOUNT": "airtacfiles", "AZURE_STORAGE_KEY": base64.b64encode(b"z" * 64).decode()})
    r = _test("azure_blob", lambda req: httpx.Response(200, text="<Containers><Container><Name>a</Name></Container><Container><Name>b</Name></Container></Containers>"), monkeypatch)
    assert r["message"] == "Connected. 2 containers found."
    _save("dropbox", {"DROPBOX_ACCESS_TOKEN": "sl.fake-token-1234567890"})
    r = _test("dropbox", lambda req: httpx.Response(200, json={"name": {"display_name": "Ali"}}), monkeypatch)
    assert r["message"] == "Connected as Ali."
    _save("supabase", {"SUPABASE_URL": "https://evil.example.com", "SUPABASE_KEY": "sb_publishable_fake123456"})
    assert not _test("supabase", lambda req: httpx.Response(200), monkeypatch)["ok"]
    _save("supabase", {"SUPABASE_URL": "https://abcdefgh.supabase.co"})
    seen = {}
    r = _test("supabase", lambda req: seen.update(url=str(req.url), key=req.headers["apikey"]) or httpx.Response(200, json={}), monkeypatch)
    assert r["ok"] and seen == {"url": "https://abcdefgh.supabase.co/auth/v1/settings", "key": "sb_publishable_fake123456"}


def test_sap_ai_core(monkeypatch):
    key = {"clientid": "sb-client", "clientsecret": "sap-secret-123456", "url": "https://sub.authentication.eu10.hana.ondemand.com",
           "serviceurls": {"AI_API_URL": "https://api.ai.prod.eu-central-1.aws.ml.hana.ondemand.com"}}
    _save("sap_ai_core", {"SAP_AICORE_SERVICE_KEY": json.dumps(key)})
    calls = []

    def sap(req):
        calls.append(str(req.url))
        if req.url.path == "/oauth/token":
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok"})
        assert req.headers["ai-resource-group"] == "default" and req.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"count": 3, "resources": []})
    r = _test("sap_ai_core", sap, monkeypatch)
    assert r["message"] == "Connected. 3 scenarios in resource group default."
    assert calls[1] == "https://api.ai.prod.eu-central-1.aws.ml.hana.ondemand.com/v2/lm/scenarios"
    r = _test("sap_ai_core", lambda req: httpx.Response(401), monkeypatch)
    assert not r["ok"] and "refused" in r["message"]


def test_test_messages_never_echo_a_key(monkeypatch):
    _save("brave_search", {"BRAVE_API_KEY": "BSA-echo-1234567890abc"})
    from plugins.apikeys import p as plug
    card = connections.REGISTRY["brave_search"]
    monkeypatch.setattr(card, "test", lambda http: (False, f"bad key BSA-echo-1234567890abc for {plug.id}"))
    r = _test("brave_search", lambda req: httpx.Response(200), monkeypatch)
    assert "BSA-echo" not in r["message"] and "[hidden]" in r["message"]


# ---------------------------------------------------------------- custom keys

def test_custom_key_lifecycle(monkeypatch):
    client = _client()
    d = client.post("/api/plugins/apikeys/custom", json={
        "name": "Hackathon judge API", "url": "https://judge.example.org", "test_url": "https://api.judge.example.org/me",
        "header": "X-Api-Key", "notes": "Given at check-in", "fields": [{"label": "Key"}, {"label": "Team ID", "secret": False}]}).json()
    assert d["id"] == "custom_hackathon_judge_api" and [f["key"] for f in d["fields"]] == ["CUSTOM_HACKATHON_JUDGE_API_1",
                                                                                        "CUSTOM_HACKATHON_JUDGE_API_2"]
    client.post(f"/api/connections/{d['id']}", json={"values": {"CUSTOM_HACKATHON_JUDGE_API_1": "judge-key-1234567890",
                                                                 "CUSTOM_HACKATHON_JUDGE_API_2": "team-42"}})
    card = _cards()[d["id"]]
    assert card["group"] == "Custom" and card["custom"] and card["fields"][1]["shown"] == "team-42"
    from airtac import websearch
    monkeypatch.setattr(websearch, "check_public", lambda url: url)
    seen = {}
    r = _test(d["id"], lambda req: seen.update(h=req.headers.get("x-api-key")) or httpx.Response(200), monkeypatch)
    assert r["ok"] and seen["h"] == "judge-key-1234567890"

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    assert client.post(f"/api/plugins/apikeys/custom/{d['id']}/logo", content=png).status_code == 200
    assert _cards()[d["id"]]["logo"] == f"/api/plugins/apikeys/logo/{d['id']}"
    assert client.get(f"/api/plugins/apikeys/logo/{d['id']}").content == png
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    assert client.post(f"/api/plugins/apikeys/custom/{d['id']}/logo", content=svg).status_code == 400

    assert client.post(f"/api/plugins/apikeys/custom/{d['id']}/delete").status_code == 200
    assert d["id"] not in _cards() and config.secret("CUSTOM_HACKATHON_JUDGE_API_1") == ""


def test_custom_key_rules():
    client = _client()
    assert client.post("/api/plugins/apikeys/custom", json={"name": ""}).status_code == 400
    assert client.post("/api/plugins/apikeys/custom", json={"name": "x", "test_url": "http://plain.example"}).status_code == 400
    assert client.post("/api/plugins/apikeys/custom", json={"name": "x", "header": "Bad Header:"}).status_code == 400
    assert client.post("/api/plugins/apikeys/custom", json={"name": "x", "fields": [{}] * 5}).status_code == 400
    store.lock()
    assert client.post("/api/plugins/apikeys/custom", json={"name": "Locked one"}).status_code == 423


def test_custom_test_refuses_private_addresses(monkeypatch):
    client = _client()
    d = client.post("/api/plugins/apikeys/custom", json={"name": "Router", "test_url": "https://192.168.1.1/api"}).json()
    connections.save(d["id"], {d["fields"][0]["key"]: "router-key-123456"})
    r = _test(d["id"], lambda req: httpx.Response(200), monkeypatch)
    assert not r["ok"]
