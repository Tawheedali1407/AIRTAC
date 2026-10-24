"""A passcode for your other devices (your phone, a second PC).

On this computer (localhost) AIRTAC needs nothing. From any other address (your laptop's
Tailscale name, or its Wi-Fi address) it asks for the passcode once per device and then keeps
a session cookie for 30 days. The passcode lives in .env as AIRTAC_PASSCODE; with no passcode
set, other devices are simply refused.

Wrong guesses are limited: 5 in 10 minutes per device, then it has to wait.
Session tokens are random; only their SHA-256 is stored (in the kv table), so a copy of the
database doesn't let anyone in. See docs/SECURITY.md for what this does and doesn't protect.
"""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from airtac import config, memory

COOKIE = "airtac_session"
SESSION_DAYS = 30
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
MAX_FAILURES, FAILURE_WINDOW = 5, 600

_failures: dict[str, deque] = defaultdict(deque)


LOOPBACK_PEERS = {"127.0.0.1", "::1", "localhost", "testclient"}   # "testclient": FastAPI's test client


def is_local(host: str) -> bool:
    return host in LOCAL_HOSTS


def is_local_request(request) -> bool:
    """This PC itself: the address says localhost AND the connection really comes from this machine.
    The Host header alone can be faked by another device when AIRTAC listens on the network."""
    from airtac.main import _host_name  # imported here: main imports this module
    peer = request.client.host if request.client else ""
    return is_local(_host_name(request.headers.get("host", ""))) and peer in LOOPBACK_PEERS \
        and not request.headers.get("x-forwarded-for")   # a proxy in front means the visitor is elsewhere


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def locked_out(client: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    recent = _failures[client]
    while recent and now - recent[0] > FAILURE_WINDOW:
        recent.popleft()
    return len(recent) >= MAX_FAILURES


def check_passcode(given: str, client: str, now: float | None = None) -> bool:
    """True if the passcode is right. Wrong guesses count towards the lock-out."""
    if not config.PASSCODE or locked_out(client, now):
        return False
    if hmac.compare_digest(given.encode(), config.PASSCODE.encode()):
        _failures.pop(client, None)
        return True
    _failures[client].append(time.time() if now is None else now)
    return False


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    memory.kv_set("auth", f"session:{_digest(token)}", str(int(time.time()) + SESSION_DAYS * 86400))
    return token


def valid(token: str | None) -> bool:
    if not token or not config.PASSCODE:
        return False
    expires = memory.kv_get("auth", f"session:{_digest(token)}")
    return bool(expires) and int(expires) > time.time()


def end_all_sessions() -> None:
    """A new passcode signs every other device out."""
    from airtac import db
    with db.connect() as conn:
        conn.execute("DELETE FROM kv WHERE plugin = 'auth' AND key LIKE 'session:%'")


def end_session(token: str | None) -> None:
    if token:
        memory.kv_set("auth", f"session:{_digest(token)}", "0")


# ---------------------------------------------------------------- the per-install API token (Part D)
# Every /api/ call must also carry this install's random token: a cookie the page gets when it loads
# (HttpOnly, SameSite=Strict), or the X-AIRTAC-Token header for scripts on this PC (scripts/mcp_server.py).
# A web page on another site can't send a SameSite=Strict cookie, so even a request that slips past the
# Origin check (a sandboxed frame sends "Origin: null") is refused. The token lives in data/.install_token
# (owner-only); malware already running as you could read it, which no web app can prevent.
INSTALL_COOKIE = "airtac_install"
INSTALL_HEADER = "x-airtac-token"
INSTALL_DAYS = 400                     # browsers cap cookies at 400 days; the page renews it on every load
INSTALL_OPEN = ("/api/health", "/api/login", "/api/pair/finish")   # health: start.ps1/doctor.py; login and pairing set the token


def install_token_required() -> bool:
    import os
    return os.environ.get("AIRTAC_NO_INSTALL_TOKEN") != "1"   # the offline tests opt out (tests/conftest.py)


def _install_path():
    return config.DB_PATH.parent / ".install_token"


def install_token() -> str:
    path = _install_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    from airtac import machine
    machine.owner_only(path)
    return token


def install_ok(request) -> bool:
    given = request.cookies.get(INSTALL_COOKIE) or request.headers.get(INSTALL_HEADER) or ""
    return bool(given) and hmac.compare_digest(given.encode(), install_token().encode())


# ---------------------------------------------------------------- AIRTAC 1's service token
# AIRTAC 1 (the local brain on port 8780, F:\Airtac1) asks AIRTAC Advance to answer when its own model can't, so the
# cloud keys never leave this app. It proves who it is with its own token from data/airtac1.token (owner-only),
# accepted only from this PC and only on these routes: never the vault, keys, pairing, devices, plugin switches or
# Dev tools.
SERVICE_HEADER = "x-airtac1-token"
SERVICE_ROUTES = ("/api/health", "/api/brain/answer")


def _service_path():
    return config.DB_PATH.parent / "airtac1.token"


def service_token() -> str:
    path = _service_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    from airtac import machine
    machine.owner_only(path)
    return token


def service_used(request) -> bool:
    return bool(request.headers.get(SERVICE_HEADER))


def service_ok(request) -> bool:
    """AIRTAC 1's token, from this PC, on an allowed route."""
    given = request.headers.get(SERVICE_HEADER) or ""
    return bool(given) and request.url.path in SERVICE_ROUTES and is_local_request(request) \
        and hmac.compare_digest(given.encode(), service_token().encode())
