"""Pairing the phone app (Airtac Mobile) with this PC (Part G).

How it fits together:
- Settings -> Devices -> "Pair a phone" turns on a second listener, HTTPS on the Wi-Fi (port 8766), and shows a
  QR code valid for 2 minutes: this PC's Wi-Fi addresses, the port, the certificate's SHA-256 fingerprint and a
  one-time code. The app scans it, pins the fingerprint (it trusts only this certificate, never a lookalike) and
  swaps the code for a device token, which it keeps in Android Keystore.
- Every later call sends `Authorization: Bearer <device token>`. Only the token's SHA-256 is stored here, so a
  copy of the database lets no one in. Devices can be revoked one by one.
- The certificate is self-signed and made once (data/tls/, readable by you only). Its fingerprint is the trust
  anchor, so it doesn't matter that no public authority signed it, or that the Wi-Fi address changes.
- The Wi-Fi listener runs only while "Phone access on Wi-Fi" is on. Away from home, use Tailscale (the same
  listener answers on the tailnet address); never open a port on your router.
- When this PC is signed in to Google (Settings -> Account), pairing also needs the phone's Google sign-in, and its
  account (`sub`) must be the same one.
The vault never leaves this PC: its routes stay local-only even for a paired phone.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airtac import config, memory

log = logging.getLogger("airtac.pairing")

PORT = int(config.section("devices").get("port", 8766))
CODE_SECONDS = 120
MAX_DEVICES = 10
DEVICE_COOKIE = "airtac_device"          # the in-app web view's copy of the device token (HttpOnly, Secure, Strict)
KV = "devices"
TAILNET = ipaddress.ip_network("100.64.0.0/10")   # Tailscale addresses (CGNAT range)


class PairError(Exception):
    pass


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- addresses

def _usable(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and not a.is_loopback and not a.is_link_local and (a.is_private or a in TAILNET)


def lan_ips() -> list[str]:
    """This PC's Wi-Fi/LAN (and Tailscale) IPv4 addresses, the primary one first."""
    found = []
    try:   # the address Windows would use to reach the internet; a UDP "connect" sends no packet
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))     # TEST-NET-1: never routed anywhere
            found.append(s.getsockname()[0])
    except Exception:     # no network (or the tests' network guard)
        pass
    try:
        found += [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except Exception:
        pass
    out = []
    for ip in found:
        if _usable(ip) and ip not in out:
            out.append(ip)
    return out


def lan_host_ok(host: str) -> bool:
    """Host names the Wi-Fi listener answers to: a private or Tailscale IP literal, this PC's own name, or a
    name in [server] trusted_hosts (a Tailscale MagicDNS name). Anything else (a web page's domain secretly
    pointed at this PC: DNS rebinding) is refused."""
    host = host.lower().strip("[]")
    if _usable(host):
        return True
    me = socket.gethostname().lower()
    return host in (me, f"{me}.local") or host in config.TRUSTED_HOSTS


# ---------------------------------------------------------------- the certificate

def tls_dir() -> Path:
    return config.DB_PATH.parent / "tls"


def ensure_cert() -> tuple[Path, Path, str]:
    """(certificate, key, SHA-256 fingerprint of the certificate). Made once; kept across restarts so the phone's
    pin stays valid."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    d = tls_dir()
    cert_p, key_p = d / "airtac.crt", d / "airtac.key"
    if not (cert_p.exists() and key_p.exists()):
        d.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AIRTAC Advance on " + socket.gethostname()[:40])])
        alt = [x509.DNSName("localhost"), x509.DNSName(socket.gethostname().lower() or "airtac")]
        for ip in lan_ips():
            alt.append(x509.IPAddress(ipaddress.ip_address(ip)))
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName(alt), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
        cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        from airtac import machine
        machine.owner_only(key_p)
        log.info("made the HTTPS certificate for paired phones")
    return cert_p, key_p, fingerprint(cert_p)


def fingerprint(cert_path: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    der = x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


# ---------------------------------------------------------------- one-time codes

_codes: dict[str, float] = {}          # digest -> expiry; in memory only, so a restart cancels open codes
_failures: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def google_sub() -> str:
    """The Google account this PC is signed in with, or "" (then pairing doesn't ask for Google)."""
    try:
        from plugins import account
        if account.status()["signed_in"]:
            return (account._get("profile") or {}).get("sub", "")
    except Exception:
        pass
    return ""


def start(now: float | None = None) -> dict:
    """A new one-time code and what the QR code holds. Turns the Wi-Fi listener on."""
    now = now or time.time()
    if len(devices()) >= MAX_DEVICES:
        raise PairError(f"{MAX_DEVICES} devices are paired already. Remove one first.")
    _, _, fp = ensure_cert()
    code = secrets.token_urlsafe(12)
    with _lock:
        for k in [k for k, exp in _codes.items() if exp < now]:
            del _codes[k]
        _codes[_digest(code)] = now + CODE_SECONDS
    ips = lan_ips()
    payload = {"v": 1, "app": "airtac", "hosts": ips, "port": PORT, "fp": fp, "code": code,
               "exp": int(now + CODE_SECONDS), "google": bool(google_sub()), "pc": socket.gethostname()[:40]}
    qr_text = json.dumps(payload, separators=(",", ":"))
    return {**payload, "qr_text": qr_text, "qr_svg": qr_svg(qr_text), "expires_in": CODE_SECONDS,
            "nonce": nonce_for(code)}


def nonce_for(code: str) -> str:
    """The Google sign-in on the phone carries this nonce, so a sign-in can't be replayed for another pairing."""
    return hashlib.sha256(("airtac-pair:" + code).encode()).hexdigest()


def qr_svg(text: str) -> str:
    """The QR code as an SVG, or "" when the segno package isn't installed (the dialog then shows the code)."""
    try:
        import io
        import segno
    except ImportError:
        return ""
    buf = io.BytesIO()
    segno.make(text, error="m", micro=False).save(buf, kind="svg", scale=5, border=2, dark="#000", light="#fff",
                                                  xmldecl=False, svgns=True, title="AIRTAC pairing code")
    return buf.getvalue().decode()


def _limited(client: str, now: float) -> bool:
    recent = _failures[client]
    while recent and now - recent[0] > 600:
        recent.popleft()
    return len(recent) >= 5


def finish(code: str, name: str, client: str, google_id_token: str = "", now: float | None = None,
           verify_google=None) -> dict:
    """Swap a one-time code for a device token. Each code works once, for 2 minutes; 5 wrong codes from one
    address in 10 minutes and it has to wait."""
    now = now or time.time()
    if _limited(client, now):
        raise PairError("Too many wrong codes. Wait 10 minutes, then scan a new QR code.")
    key = _digest(code or "")
    with _lock:
        exp = _codes.pop(key, None)
    if exp is None or exp < now:
        _failures[client].append(now)
        raise PairError("That pairing code is wrong or has expired. Press Pair a phone again for a new QR code.")
    sub = google_sub()
    if sub:
        if not google_id_token:
            raise PairError("This PC is signed in to Google, so sign in with the same Google account in the app.")
        claims = (verify_google or _verify_google)(google_id_token, nonce_for(code))
        if not hmac.compare_digest(str(claims.get("sub", "")), sub):
            raise PairError("The phone's Google account isn't the one this PC is signed in with.")
    token, dev_id = secrets.token_urlsafe(32), secrets.token_hex(4)
    clean = "".join(ch for ch in (name or "Phone") if ch.isprintable())[:40] or "Phone"
    memory.kv_set(KV, f"dev:{dev_id}", json.dumps({"id": dev_id, "name": clean, "digest": _digest(token),
                                                    "paired": int(now), "seen": int(now), "ip": client}))
    log.info("paired a new device (%s)", dev_id)
    return {"token": token, "device_id": dev_id, "pc": socket.gethostname()[:40]}


def _verify_google(id_token: str, nonce: str) -> dict:
    """The phone's Google ID token: signature, issuer, audience (the client ID the app asks for its token with),
    expiry and nonce, via the Account plugin's checks."""
    import httpx
    from plugins import account
    cfg = config.section("account")
    aud = cfg.get("android_server_client_id") or account._client()[0]
    try:
        with httpx.Client(timeout=8) as http:
            keys = account.google_keys(http)
    except httpx.HTTPError:
        raise PairError("Couldn't reach Google to check the phone's sign-in. Check the internet and try again.")
    try:
        return account.verify_id_token(id_token, aud, nonce, keys)
    except account.AccountError as e:
        raise PairError(str(e))


# ---------------------------------------------------------------- paired devices

def devices() -> list[dict]:
    from airtac import db
    with db.connect() as conn:
        rows = conn.execute("SELECT value FROM kv WHERE plugin = ? AND key LIKE 'dev:%'", (KV,)).fetchall()
    out = []
    for (value,) in rows:
        try:
            out.append(json.loads(value))
        except ValueError:
            continue
    return sorted(out, key=lambda d: d.get("paired", 0))


def public(d: dict) -> dict:
    return {k: d[k] for k in ("id", "name", "paired", "seen", "ip") if k in d}


def device_for(token: str | None, client: str = "") -> dict | None:
    """The paired device this token belongs to, or None. Notes when it was last seen (at most once a minute)."""
    if not token or len(token) < 20:
        return None
    want = _digest(token)
    for d in devices():
        if hmac.compare_digest(d.get("digest", ""), want):
            if time.time() - d.get("seen", 0) > 60:
                d.update(seen=int(time.time()), ip=client or d.get("ip", ""))
                memory.kv_set(KV, f"dev:{d['id']}", json.dumps(d))
            return d
    return None


def device_from(request) -> dict | None:
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else request.cookies.get(DEVICE_COOKIE)
    return device_for(token, request.client.host if request.client else "")


def bearer_used(request) -> bool:
    return request.headers.get("authorization", "").lower().startswith("bearer ")


def revoke(dev_id: str) -> bool:
    from airtac import db
    with db.connect() as conn:
        n = conn.execute("DELETE FROM kv WHERE plugin = ? AND key = ?", (KV, f"dev:{dev_id}")).rowcount
    if n:
        log.info("removed device %s", dev_id)
    return bool(n)


# ---------------------------------------------------------------- the Wi-Fi listener (HTTPS)

_server = {"server": None, "thread": None, "error": ""}


def is_lan_request(request) -> bool:
    server = request.scope.get("server") or (None, None)
    return request.scope.get("scheme") == "https" and server[1] == PORT


def lan_on() -> bool:
    return memory.kv_get(KV, "lan_on") == "1"


def state() -> dict:
    srv = _server["server"]
    return {"on": lan_on(), "running": bool(srv and srv.started), "port": PORT, "ips": lan_ips(),
            "error": _server["error"], "fingerprint": fingerprint(tls_dir() / "airtac.crt")
            if (tls_dir() / "airtac.crt").exists() else ""}


def set_lan(on: bool, app=None) -> dict:
    memory.kv_set(KV, "lan_on", "1" if on else "0")
    if on:
        start_listener(app)
    else:
        stop_listener()
    return state()


def start_listener(app=None) -> None:
    """HTTPS on every interface (Wi-Fi and Tailscale), in a background thread of this same process. No second
    start-up (lifespan off): the scheduler and plugins are already running."""
    import os
    if os.environ.get("AIRTAC_NO_LAN"):          # the offline tests never open a network port
        return
    srv = _server["server"]
    if srv and not srv.should_exit:
        return
    import uvicorn
    if app is None:
        from airtac.main import app
    cert, key, _ = ensure_cert()
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=PORT, ssl_certfile=str(cert),  # nosec B104 (Wi-Fi listener, on purpose and only while switched on)
                                           ssl_keyfile=str(key), log_config=None, lifespan="off"))
    _server.update(server=server, error="")

    def run():
        try:
            server.run()
            if not server.started:
                _server["error"] = f"Port {PORT} is busy, so phones can't connect. Change [devices] port."
        except SystemExit:
            _server["error"] = f"Port {PORT} is busy, so phones can't connect. Change [devices] port."
        except Exception as e:      # keep the main app running whatever happens here
            _server["error"] = f"The phone listener stopped: {type(e).__name__}"
            log.exception("phone listener crashed")
    t = threading.Thread(target=run, name="airtac-lan", daemon=True)
    _server["thread"] = t
    t.start()
    log.info("phone access on Wi-Fi: https on port %d", PORT)


def stop_listener() -> None:
    srv = _server["server"]
    if srv:
        srv.should_exit = True
        log.info("phone access on Wi-Fi turned off")
    _server.update(server=None, thread=None)


def start_if_on(app) -> None:
    if lan_on():
        try:
            start_listener(app)
        except Exception:
            log.exception("couldn't start phone access on Wi-Fi")
