"""Part F: Google sign-in with a mocked Google (PKCE, state, nonce, ID token checks), the allowed account,
the refresh token kept only in the vault, sign-out, encrypted Drive sync and Calendar import."""
import base64
import json
import logging
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from airtac import config
from plugins import account
from plugins.passwords import store

CID = "1234-test.apps.googleusercontent.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwk() -> dict:
    n = KEY.public_key().public_numbers()
    return {"kid": "k1", "kty": "RSA", "alg": "RS256", "n": b64(n.n.to_bytes(256, "big")), "e": b64(n.e.to_bytes(3, "big"))}


def id_token(nonce: str, sub="111", email="ali@example.com", aud=CID, exp=None, key=KEY, iss="https://accounts.google.com") -> str:
    head = b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    body = b64(json.dumps({"iss": iss, "aud": aud, "sub": sub, "email": email, "email_verified": True, "nonce": nonce,
                           "exp": exp or time.time() + 3600, "name": "Ali"}).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64(sig)}"


class Google:
    """A fake Google: token endpoint, discovery, keys, revoke, Drive app-data, Calendar."""

    def __init__(self):
        self.sub, self.calls, self.drive, self.refresh_ok = "111", [], {}, True

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        self.calls.append(url)
        if url == account.DISCOVERY:
            return httpx.Response(200, json={"jwks_uri": "https://www.googleapis.com/oauth2/v3/certs"})
        if "oauth2/v3/certs" in url:
            return httpx.Response(200, json={"keys": [jwk()]})
        if url == account.TOKEN_URL:
            form = parse_qs(req.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"][0] and form["redirect_uri"][0].startswith("http://127.0.0.1:")
                return httpx.Response(200, json={"id_token": id_token(account._pending["nonce"], self.sub), "access_token": "at",
                                                 "refresh_token": "1//refresh-secret-token", "scope": "openid email profile "
                                                 + account.SCOPES["drive"] + " " + account.SCOPES["calendar"]})
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "at2"})
        if url.startswith(account.REVOKE_URL):
            return httpx.Response(200)
        if url.startswith(account.DRIVE_UPLOAD):
            meta = json.loads(req.content.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
            data = req.content.split(b"\r\n\r\n", 2)[2].rsplit(b"\r\n--", 1)[0]
            fid = url.split("/files/")[1].split("?")[0] if "/files/" in url else f"id-{meta['name']}"
            self.drive[meta.get("name") or fid] = {"id": fid, "name": meta.get("name"), "data": data, "appProperties": meta["appProperties"]}
            return httpx.Response(200, json={"id": fid})
        if url.startswith(account.DRIVE) and "alt=media" in url:
            fid = url.split("/files/")[1].split("?")[0]
            return httpx.Response(200, content=next(f["data"] for f in self.drive.values() if f["id"] == fid))
        if url.startswith(account.DRIVE):
            return httpx.Response(200, json={"files": [{k: v for k, v in f.items() if k != "data"} for f in self.drive.values()]})
        if url.startswith(account.CALENDAR):
            return httpx.Response(200, json={"items": [{"summary": "DBMS lab exam", "start": {"date": "2026-11-12"}},
                                                       {"summary": "Mentor call", "start": {"dateTime": "2026-11-13T10:30:00Z"}}]})
        return httpx.Response(404)


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setattr(config, "SAVED", {"GOOGLE_CLIENT_ID": CID, "GOOGLE_CLIENT_SECRET": "GOCSPX-test"})
    monkeypatch.setattr(store, "ARGON", {"m": 1024, "p": 1})
    monkeypatch.setattr(store, "TARGET_SECONDS", 0.001)
    account._jwks.update(at=0.0, keys=[])
    store.lock()
    store._state.update(service=None)
    store.setup("correct horse battery")
    g = Google()
    yield g
    account._pending.clear()
    store.lock()


def sign_in(g, features=None):
    url = account.start(features, http=httpx.Client(transport=httpx.MockTransport(g)))
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"].startswith("http://127.0.0.1:") and q["nonce"]
    return account.finish("auth-code", q["state"])


def test_no_vault_no_sign_in(google, monkeypatch):
    monkeypatch.setattr(store, "exists", lambda: False)
    with pytest.raises(account.AccountError, match="Create your vault first"):
        account.start()


def test_not_set_up_says_so(monkeypatch):
    monkeypatch.setattr(config, "SAVED", {})
    with pytest.raises(account.AccountError, match="Set up Google first"):
        account.start()


def test_sign_in_keeps_the_token_only_in_the_vault(google, caplog):
    with caplog.at_level(logging.DEBUG):
        st = sign_in(google, ["drive"])
    assert st["signed_in"] and st["email"] == "ali@example.com"
    assert "refresh-secret-token" not in caplog.text and "auth-code" not in caplog.text
    assert "refresh-secret-token" not in store.path().read_text()                 # encrypted in the vault
    from airtac import memory
    assert "refresh" not in (memory.kv_get("account", "profile") or "")
    assert account._refresh_token() == "1//refresh-secret-token"


def test_state_is_single_use_and_checked(google):
    url = account.start(http=httpx.Client(transport=httpx.MockTransport(google)))
    with pytest.raises(account.AccountError, match="wasn't started here"):
        account.finish("code", "forged-state")
    state = parse_qs(urlparse(url).query)["state"][0]
    account._pending["state"] = state
    account.finish("code", state)
    with pytest.raises(account.AccountError):
        account.finish("code", state)                                               # replay


def test_forged_expired_or_foreign_tokens_are_refused(google):
    keys = [jwk()]
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(account.AccountError, match="bad signature"):
        account.verify_id_token(id_token("n", key=other), CID, "n", keys)
    with pytest.raises(account.AccountError, match="expired"):
        account.verify_id_token(id_token("n", exp=time.time() - 3600), CID, "n", keys)
    with pytest.raises(account.AccountError, match="wasn't meant for AIRTAC"):
        account.verify_id_token(id_token("n", aud="someone-else"), CID, "n", keys)
    with pytest.raises(account.AccountError, match="didn't match"):
        account.verify_id_token(id_token("other-nonce"), CID, "n", keys)
    assert account.verify_id_token(id_token("n", iss="accounts.google.com"), CID, "n", keys)["sub"] == "111"


def test_only_the_first_account_may_sign_in(google):
    sign_in(google)
    google.sub = "999"
    with pytest.raises(account.AccountError, match="isn't the account this AIRTAC belongs to"):
        sign_in(google)


def test_expired_refresh_means_sign_in_again_not_a_crash(google):
    sign_in(google)
    google.refresh_ok = False
    with pytest.raises(account.SignInAgain):
        account.access_token(httpx.Client(transport=httpx.MockTransport(google)))
    assert account.status()["sign_in_again"]


def test_sign_out_revokes_and_forgets(google):
    sign_in(google)
    account.sign_out(httpx.Client(transport=httpx.MockTransport(google)))
    assert any(c.startswith(account.REVOKE_URL) for c in google.calls)
    assert not account.status()["signed_in"] and account._refresh_token() == ""


def test_sync_uploads_only_ciphertext_and_restores(google, tmp_path, monkeypatch):
    here = {"data/settings.json": tmp_path / "s.json", "plans/learn_ai.json": tmp_path / "plan.json"}
    here["data/settings.json"].write_text('{"theme": "crimson"}')
    here["plans/learn_ai.json"].write_text('{"name": "Become an AI expert"}')
    monkeypatch.setattr(account, "settings_paths", lambda: here)
    store.save({"site": "Instagram", "username": "ali", "password": "Hunter2-secret!!"})
    sign_in(google, ["drive"])
    http = httpx.Client(transport=httpx.MockTransport(google))
    r = account.push(http)
    assert r["version"] == 1 and set(google.drive) == {"vault.bin", "settings.airtac"}
    for f in google.drive.values():
        assert b"Hunter2" not in f["data"] and b"crimson" not in f["data"] and b"[failover]" not in f["data"]
    google.drive["vault.bin"]["appProperties"] = {"airtac_version": "5"}       # another PC uploaded later
    with pytest.raises(account.AccountError, match="newer copy"):
        account.push(http)
    out = account.pull(http)
    assert "vault.bin" in out["restored"] and (tmp_path / "data" / "backups").exists()
    store.unlock("correct horse battery")
    here["data/settings.json"].write_text('{"theme": "day"}')                    # changed since the upload
    assert sorted(account.apply_settings()) == ["data/settings.json", "plans/learn_ai.json"]
    assert "crimson" in here["data/settings.json"].read_text()


def test_calendar_import(google):
    sign_in(google, ["calendar"])
    items = account.calendar_items(httpx.Client(transport=httpx.MockTransport(google)))
    assert items[0] == {"title": "DBMS lab exam", "date": "2026-11-12", "time": "", "category": "exam", "notes": "",
                        "source": "Google Calendar"}
    assert items[1]["time"]                                                         # shown in this PC's time
