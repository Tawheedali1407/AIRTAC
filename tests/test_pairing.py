"""Part G: pairing a phone. One-time codes, device tokens, revoke, the Wi-Fi host rules, the Google account match,
and a real HTTPS round trip from a test client that pins the certificate's fingerprint."""
import hashlib
import socket
import ssl
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from airtac import config, main, pairing
from airtac.main import app

pc = TestClient(app, base_url="http://localhost")


def phone(token: str = ""):
    """A request as it arrives on the Wi-Fi listener (https, port 8766, the PC's LAN address)."""
    c = TestClient(app, base_url=f"https://192.168.1.20:{pairing.PORT}")
    if token:
        c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.fixture(autouse=True)
def _no_listener(monkeypatch):
    monkeypatch.setattr(pairing, "start_listener", lambda app=None: None)


def _pair(name="Redmi Note 9 Pro Max") -> dict:
    info = pc.post("/api/pair/start").json()
    r = phone().post("/api/pair/finish", json={"code": info["code"], "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_qr_holds_what_the_app_needs_and_never_a_secret():
    info = pc.post("/api/pair/start").json()
    assert info["port"] == pairing.PORT and len(info["fp"]) == 64 and info["expires_in"] == 120
    assert info["lan"]["on"] is True                        # pairing switches phone access on
    assert '"code"' in info["qr_text"] and info["qr_svg"].startswith("<svg")
    key = (config.DB_PATH.parent / "tls" / "airtac.key").read_text()
    assert "PRIVATE KEY" in key and key not in info["qr_text"]


def test_pairing_gives_a_token_once_and_the_token_works():
    first = pc.post("/api/pair/start").json()
    done = phone().post("/api/pair/finish", json={"code": first["code"], "name": "Phone"}).json()
    again = phone().post("/api/pair/finish", json={"code": first["code"], "name": "Phone"})
    assert again.status_code == 403 and "expired" in again.json()["detail"]             # a code works once
    assert phone().get("/api/history").status_code == 401                              # no token: refused
    assert phone("x" * 43).get("/api/history").status_code == 401                      # wrong token: refused
    me = phone(done["token"]).get("/api/pair/me").json()
    assert me["device"]["name"] == "Phone" and "digest" not in me["device"]
    assert phone(done["token"]).get("/api/history").status_code == 200


def test_expired_and_guessed_codes(monkeypatch):
    info = pc.post("/api/pair/start").json()
    later = time.time() + 121
    with pytest.raises(pairing.PairError, match="expired"):
        pairing.finish(info["code"], "x", "10.0.0.9", now=later)
    for _ in range(5):
        with pytest.raises(pairing.PairError):
            pairing.finish("wrong-code-123", "x", "10.0.0.8")
    fresh = pc.post("/api/pair/start").json()
    with pytest.raises(pairing.PairError, match="Too many"):
        pairing.finish(fresh["code"], "x", "10.0.0.8")                        # locked out even with a good code


def test_only_this_pc_can_start_list_or_revoke():
    assert phone().post("/api/pair/start").status_code in (401, 403)
    dev = _pair()
    assert phone(dev["token"]).post("/api/pair/start").status_code == 403         # a paired phone can't pair others
    assert phone(dev["token"]).get("/api/devices").status_code == 403
    listed = pc.get("/api/devices").json()["devices"]
    assert [d["name"] for d in listed] == ["Redmi Note 9 Pro Max"] and "digest" not in listed[0]
    assert pc.post(f"/api/devices/{dev['device_id']}/revoke").json()["ok"]
    assert phone(dev["token"]).get("/api/history").status_code == 401             # revoked: refused at once


def test_vault_and_dev_tools_stay_on_the_pc():
    dev = _pair()
    assert phone(dev["token"]).get("/api/connections").status_code == 403
    assert phone(dev["token"]).post("/api/devices/lan", json={"on": False}).status_code == 403


def test_wifi_listener_refuses_strange_host_names():
    assert pairing.lan_host_ok("192.168.1.20") and pairing.lan_host_ok("100.101.102.103")
    assert not pairing.lan_host_ok("evil.example.com") and not pairing.lan_host_ok("8.8.8.8")
    bad = TestClient(app, base_url=f"https://evil.example.com:{pairing.PORT}")
    assert bad.get("/api/health").status_code == 403                             # DNS rebinding refused
    plain = TestClient(app, base_url="http://192.168.1.20:8765")
    assert plain.get("/api/health").status_code == 403                           # the main port stays localhost-only


def test_web_view_gets_cookies_from_the_bearer_once():
    dev = _pair()
    r = phone(dev["token"]).get("/api/pair/web", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers.get_list("set-cookie")
    assert any(c.startswith("airtac_device=") and "HttpOnly" in c and "Secure" in c and "SameSite=strict" in c for c in cookie)
    assert phone().get("/api/pair/web").status_code == 401


def test_google_account_must_match_when_the_pc_is_signed_in(monkeypatch):
    monkeypatch.setattr(pairing, "google_sub", lambda: "sub-ali")
    info = pairing.start()
    assert info["google"] is True
    with pytest.raises(pairing.PairError, match="same Google account"):
        pairing.finish(info["code"], "p", "10.0.0.1")
    info = pairing.start()
    with pytest.raises(pairing.PairError, match="isn't the one"):
        pairing.finish(info["code"], "p", "10.0.0.1", "tok", verify_google=lambda t, n: {"sub": "sub-someone-else"})
    info = pairing.start()
    seen = {}

    def ok(t, nonce):
        seen["nonce"] = nonce
        return {"sub": "sub-ali"}
    assert pairing.finish(info["code"], "p", "10.0.0.1", "tok", verify_google=ok)["token"]
    assert seen["nonce"] == pairing.nonce_for(info["code"]) == info["nonce"]


def test_apk_download_and_its_checksum(monkeypatch, tmp_path):
    apk = tmp_path / "airtac.apk"
    monkeypatch.setattr(main, "APK", apk)
    assert phone().get("/download/airtac.apk").status_code == 404
    apk.write_bytes(b"PK fake apk for the test")
    info = pc.get("/api/devices").json()["apk"]
    assert info["sha256"] == hashlib.sha256(b"PK fake apk for the test").hexdigest()
    r = phone().get("/download/airtac.apk")                                       # open before pairing, nothing secret
    assert r.status_code == 200 and r.content.startswith(b"PK")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_https_with_a_pinned_certificate(monkeypatch):
    """The Wi-Fi listener for real (on loopback): a client that trusts only the fingerprint from the QR code gets in;
    one pinned to another fingerprint refuses the connection."""
    import uvicorn
    port = _free_port()
    monkeypatch.setattr(pairing, "PORT", port)
    cert, key, fp = pairing.ensure_cert()
    monkeypatch.setattr(config, "TRUSTED_HOSTS", config.TRUSTED_HOSTS + ["127.0.0.1"])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, ssl_certfile=str(cert),
                                           ssl_keyfile=str(key), log_config=None, lifespan="off"))
    import threading
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        def pinned_get(path: str, pin: str, **kw):
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # trust comes from the pin below, not a CA
            with socket.create_connection(("127.0.0.1", port)) as raw, ctx.wrap_socket(raw) as tls:
                got = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
            if got != pin:
                raise ssl.SSLError("certificate doesn't match the pin")
            return httpx.Client(verify=ctx).get(f"https://127.0.0.1:{port}{path}", **kw)

        info = pc.post("/api/pair/start").json()
        assert info["fp"] == fp
        with httpx.Client(verify=False) as c:      # noqa: S501  # nosec B501 (pin checked by pinned_get above)
            pinned_get("/api/health", fp)
            done = c.post(f"https://127.0.0.1:{port}/api/pair/finish", json={"code": info["code"], "name": "Pinned"}).json()
        r = pinned_get("/api/pair/me", fp, headers={"Authorization": f"Bearer {done['token']}"})
        assert r.status_code == 200 and r.json()["device"]["name"] == "Pinned"
        with pytest.raises(ssl.SSLError):
            pinned_get("/api/health", "0" * 64)
    finally:
        server.should_exit = True
        t.join(5)
