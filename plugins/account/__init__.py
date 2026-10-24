"""Google sign-in, new-PC restore and Drive sync (Part F). Everything runs in the backend, on this PC.

Sign-in: OAuth 2.0 authorization code + PKCE (S256) + state + nonce, in your normal browser, coming back
to a one-time listener on http://127.0.0.1:<random port> (Google's "Desktop app" flow; docs:
developers.google.com/accounts/docs/OAuth2InstalledApp). The ID token is checked here: RS256 signature
against Google's published keys, iss, aud = your client ID, exp, nonce, a verified email. The first account
that signs in becomes the only one allowed (its `sub`, which never changes, is what's compared).

Scopes: openid email profile. Drive app-data (sync) and Calendar read-only are asked for only when you
switch those on (incremental, include_granted_scopes). The refresh token lives only in the vault
(airtac/vault.py: the API-key slot); never in a file, log, URL or the page. Sign out revokes it.
A refused refresh (invalid_grant: expired after 7 days while your Google project is in Testing, or
revoked) shows "Sign in again", never a crash.

Sync (off until you switch it on): Drive's hidden app-data folder gets
  vault.bin            as it is (already encrypted with your super password)
  settings.airtac      AIRTAC's settings, app list, plans and persona choice, encrypted with a key derived
                       from your vault (AES-256-GCM), so Google only ever sees ciphertext.
Restore on a new PC: sign in, Restore, then type your super password (or recovery key). The current
files are backed up first. Models and your library are not synced (they're re-downloaded / re-added).
Google sign-in proves who you are and fetches your data; it never replaces the super password.
"""
import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from airtac import config, connections, memory
from airtac.connections import Field
from airtac.plugin_api import local_only, plugin

p = plugin(__name__)
log = logging.getLogger("airtac.account")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = {"basic": "openid email profile",
          "drive": "https://www.googleapis.com/auth/drive.appdata",
          "calendar": "https://www.googleapis.com/auth/calendar.readonly",
          # Connectors (plugins/connectors), each asked for only when you connect it:
          "gmail": "https://www.googleapis.com/auth/gmail.readonly",
          "gmail_drafts": "https://www.googleapis.com/auth/gmail.compose",      # drafts only: AIRTAC never sends
          "calendar_write": "https://www.googleapis.com/auth/calendar.events",
          "drive_read": "https://www.googleapis.com/auth/drive.readonly"}
DRIVE = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
WAIT_SECONDS = 300          # the sign-in page must come back within 5 minutes

p.connector(id="google_signin", name="Google sign-in (your own client)", group="Services", order=60, logo="google",
            get_key="https://console.cloud.google.com/auth/clients", docs="https://developers.google.com/accounts/docs/OAuth2InstalledApp",
            about="The Desktop-app client ID you make once in Google Cloud (docs/GOOGLE_SETUP.md). Used only for Settings > Account.",
            powers=["sign in with Google", "Drive sync", "Google Calendar import"],
            fields=[Field("GOOGLE_CLIENT_ID", "Client ID", secret=False, placeholder="1234-abc.apps.googleusercontent.com"),
                    Field("GOOGLE_CLIENT_SECRET", "Client secret", placeholder="GOCSPX-...")])


class AccountError(Exception):
    """A sentence for the page."""


class SignInAgain(AccountError):
    pass


# ---------------------------------------------------------------- state (no secrets here)

def _get(key: str, default=None):
    try:
        return json.loads(memory.kv_get("account", key) or "null") or default
    except ValueError:
        return default


def _set(key: str, value) -> None:
    memory.kv_set("account", key, json.dumps(value))


def _client() -> tuple[str, str]:
    cid, secret = config.secret("GOOGLE_CLIENT_ID").strip(), config.secret("GOOGLE_CLIENT_SECRET").strip()
    if not cid.endswith(".apps.googleusercontent.com"):
        raise AccountError("Set up Google first: make a Desktop-app client ID (docs/GOOGLE_SETUP.md) and save it on APIs and keys.")
    return cid, secret


def _refresh_token() -> str:
    from airtac import vault
    return vault.get("google", "oauth", "key")


def status() -> dict:
    profile = _get("profile")
    try:
        _client()
        ready = True
    except AccountError:
        ready = False
    signed_in = bool(profile and _refresh_token())
    return {"ready": ready, "signed_in": signed_in, "sign_in_again": bool(_get("again")), "email": (profile or {}).get("email", ""),
            "name": (profile or {}).get("name", ""), "scopes": _get("scopes", []), "sync": bool(_get("sync_on")),
            "last_sync": _get("last_sync"), "pending": bool(_pending.get("state"))}


# ---------------------------------------------------------------- the ID token

def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


_jwks: dict = {"at": 0.0, "keys": []}


def google_keys(http: httpx.Client) -> list[dict]:
    if time.time() - _jwks["at"] > 3600:
        uri = http.get(DISCOVERY).json()["jwks_uri"]
        _jwks.update(at=time.time(), keys=http.get(uri).json()["keys"])
    return _jwks["keys"]


def verify_id_token(token: str, client_id: str, nonce: str, keys: list[dict], now: float | None = None) -> dict:
    """The claims, or AccountError. Checks the RS256 signature with Google's key, iss, aud, exp, nonce, email."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        head, claims = json.loads(_b64(head_b64)), json.loads(_b64(body_b64))
    except ValueError:
        raise AccountError("Google's answer wasn't a valid sign-in.")
    if head.get("alg") != "RS256":
        raise AccountError("Google's sign-in used an unexpected signature.")
    key = next((k for k in keys if k.get("kid") == head.get("kid")), None)
    if key is None:
        raise AccountError("Google's sign-in was signed with an unknown key.")
    pub = rsa.RSAPublicNumbers(int.from_bytes(_b64(key["e"]), "big"), int.from_bytes(_b64(key["n"]), "big")).public_key()
    try:
        pub.verify(_b64(sig_b64), f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise AccountError("Google's sign-in had a bad signature, so it was refused.")
    now = now or time.time()
    if claims.get("iss") not in ISSUERS or claims.get("aud") != client_id:
        raise AccountError("That sign-in wasn't meant for AIRTAC.")
    if float(claims.get("exp", 0)) < now - 60:
        raise AccountError("That sign-in has expired. Try again.")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise AccountError("That sign-in didn't match the one AIRTAC started.")
    if not claims.get("email_verified"):
        raise AccountError("That Google account's email isn't verified.")
    return claims


# ---------------------------------------------------------------- sign in (PKCE + loopback)

_pending: dict = {}
_lock = threading.Lock()


def start(features: list[str] | None = None, http: httpx.Client | None = None) -> str:
    """The Google page to open. A one-time listener on 127.0.0.1 waits for the answer."""
    cid, _ = _client()
    from plugins.passwords import store
    if not store.exists():
        raise AccountError("Create your vault first (Passwords), so the Google sign-in can be kept encrypted in it.")
    scope = " ".join([SCOPES["basic"], *[SCOPES[f] for f in (features or []) if f in SCOPES and f != "basic"]])
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    server = HTTPServer(("127.0.0.1", 0), _Callback)
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    with _lock:
        old = _pending.get("server")
        if old:
            threading.Thread(target=old.shutdown, daemon=True).start()
        _pending.clear()
        _pending.update(state=state, nonce=nonce, verifier=verifier, redirect=redirect, server=server, scope=scope,
                        at=time.time(), http=http, error="")
    server.timeout = 1
    threading.Thread(target=_serve, args=(server,), daemon=True).start()
    profile = _get("profile") or {}
    params = {"client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": scope, "state": state,
              "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "include_granted_scopes": "true",
              "access_type": "offline", "prompt": "consent", **({"login_hint": profile["email"]} if profile.get("email") else {})}
    return f"{AUTH_URL}?{urlencode(params)}"


def _serve(server: HTTPServer) -> None:
    end = time.time() + WAIT_SECONDS
    while time.time() < end and _pending.get("server") is server and not _pending.get("done"):
        server.handle_request()
    server.server_close()


class _Callback(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server's name)
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if "code" not in q and "error" not in q:
            self.send_response(404)
            self.end_headers()
            return
        try:
            finish(q.get("code", ""), q.get("state", ""), q.get("error", ""))
            text, code = "Signed in to AIRTAC. You can close this tab.", 200
        except AccountError as e:
            text, code = f"AIRTAC: {e}", 400
        _pending["done"] = True
        body = f"<!doctype html><meta charset=utf-8><title>AIRTAC</title><p style='font:16px sans-serif'>{text}</p>".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):   # the query string holds the one-time code: never in a log
        return


def finish(code: str, state: str, error: str = "") -> dict:
    """The browser came back: check state, swap the code (with the PKCE verifier) and check the ID token."""
    pend = dict(_pending)
    if not pend or not secrets.compare_digest(state or "", pend.get("state", "")):
        raise AccountError("That sign-in wasn't started here. Start it again from Settings.")
    _pending["state"] = ""            # one use only
    if error:
        _pending["error"] = "You cancelled the sign-in." if error == "access_denied" else "Google refused the sign-in."
        raise AccountError(_pending["error"])
    if time.time() - pend["at"] > WAIT_SECONDS:
        raise AccountError("The sign-in took too long. Start it again.")
    cid, secret = _client()
    http = pend.get("http") or httpx.Client(timeout=20)
    r = http.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, "code": code, "code_verifier": pend["verifier"],
                                   "grant_type": "authorization_code", "redirect_uri": pend["redirect"]})
    if r.status_code != 200:
        raise AccountError("Google didn't accept the sign-in code. Start again.")
    tokens = r.json()
    claims = verify_id_token(tokens.get("id_token", ""), cid, pend["nonce"], google_keys(http))
    allowed = _get("allowed", [])
    if allowed and claims["sub"] not in allowed:
        raise AccountError(f"{claims.get('email', 'That account')} isn't the account this AIRTAC belongs to.")
    if tokens.get("refresh_token"):
        from airtac import vault
        vault.save("google", "oauth", "key", tokens["refresh_token"])   # the vault only
    _set("allowed", allowed or [claims["sub"]])
    _set("profile", {"sub": claims["sub"], "email": claims.get("email", ""), "name": claims.get("name", "")})
    _set("scopes", sorted(set(_get("scopes", []) + tokens.get("scope", "").split())))
    _set("again", False)
    log.info("account: signed in")             # never who, never a token
    return status()


def access_token(http: httpx.Client | None = None) -> str:
    """A fresh access token from the stored refresh token (kept only in memory, never saved)."""
    cid, secret = _client()
    refresh = _refresh_token()
    if not refresh:
        raise SignInAgain("Sign in with Google first (Settings > Account).")
    http = http or httpx.Client(timeout=20)
    r = http.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, "refresh_token": refresh, "grant_type": "refresh_token"})
    if r.status_code == 400 and "invalid_grant" in r.text:
        _set("again", True)
        raise SignInAgain("Google needs you to sign in again (the sign-in expired or was removed).")
    if r.status_code != 200:
        raise AccountError("Google didn't answer. Try again in a minute.")
    return r.json()["access_token"]


def sign_out(http: httpx.Client | None = None) -> None:
    from airtac import vault
    token = _refresh_token()
    if token:
        try:
            (http or httpx.Client(timeout=15)).post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            log.warning("account: couldn't reach Google to revoke; removed here anyway")
        vault.delete("google", "oauth", "key")
    _set("profile", None)
    _set("scopes", [])
    _set("sync_on", False)
    _set("again", False)


def has_scope(feature: str) -> bool:
    return SCOPES[feature] in _get("scopes", [])


# ---------------------------------------------------------------- Drive sync (app-data folder)

SETTINGS_FILES = ["data/settings.json", "plugins/desktop/apps.json", "plans/learn_ai.json", "config/airtac.toml"]


def settings_paths() -> dict:
    """Where each synced settings file lives on this PC (no secrets in any of them)."""
    return {"data/settings.json": config.DB_PATH.parent / "settings.json", "plugins/desktop/apps.json": config.APPS_FILE,
            "plans/learn_ai.json": config.ROOT / "plans" / "learn_ai.json", "config/airtac.toml": config.ROOT / "config" / "airtac.toml"}


def _settings_blob() -> bytes:
    """AIRTAC's non-secret setup, encrypted with a key derived from the vault (it must be unlocked)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from plugins.passwords import store
    files = {}
    for rel, path in settings_paths().items():
        if path.is_file():
            files[rel] = path.read_text(encoding="utf-8")
    kv = {k: memory.kv_get(plug, key) for plug, key, k in (("daily", "settings", "daily"), ("learn", "done", "learn_done"))}
    plain = json.dumps({"version": 1, "made": time.time(), "files": files, "kv": kv}).encode()
    nonce = secrets.token_bytes(12)
    return b"AIRTACS1" + nonce + AESGCM(store.derived_key("sync settings")).encrypt(nonce, plain, b"airtac-settings")


def _open_settings(blob: bytes) -> dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from plugins.passwords import store
    if not blob.startswith(b"AIRTACS1"):
        raise AccountError("The settings in Drive aren't AIRTAC's.")
    try:
        return json.loads(AESGCM(store.derived_key("sync settings")).decrypt(blob[8:20], blob[20:], b"airtac-settings"))
    except InvalidTag:
        raise AccountError("The settings in Drive belong to another vault, or were changed.")


def _drive_files(http: httpx.Client, token: str) -> dict:
    r = http.get(DRIVE, params={"spaces": "appDataFolder", "fields": "files(id,name,modifiedTime,appProperties)"},
                 headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise AccountError("Drive didn't answer. Try again.")
    return {f["name"]: f for f in r.json().get("files", [])}


def _upload(http, token, existing: dict, name: str, data: bytes, version: int) -> None:
    meta = {"name": name, "appProperties": {"airtac_version": str(version)}}
    if name not in existing:
        meta["parents"] = ["appDataFolder"]
    boundary = secrets.token_hex(12)
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--".encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"}
    if name in existing:
        r = http.patch(f"{DRIVE_UPLOAD}/{existing[name]['id']}", params={"uploadType": "multipart"}, content=body, headers=headers)
    else:
        r = http.post(DRIVE_UPLOAD, params={"uploadType": "multipart"}, content=body, headers=headers)
    if r.status_code not in (200, 201):
        raise AccountError(f"Drive didn't take {name}. Try again.")


def push(http: httpx.Client | None = None, force: bool = False) -> dict:
    """Upload the vault file and the encrypted settings. Refuses to overwrite a newer copy unless force."""
    from plugins.passwords import store
    if not has_scope("drive"):
        raise AccountError("Switch on Drive sync first (it asks Google for the app-data permission).")
    if not store.exists():
        raise AccountError("Create your vault first: sync only ever uploads encrypted data.")
    http = http or httpx.Client(timeout=60)
    token = access_token(http)
    existing = _drive_files(http, token)
    mine = int(_get("sync_version", 0) or 0)
    theirs = max([int(f.get("appProperties", {}).get("airtac_version", 0)) for f in existing.values()] or [0])
    if theirs > mine and not force:
        raise AccountError("Drive has a newer copy (from another PC). Restore it first, or choose Upload anyway.")
    version = max(mine, theirs) + 1
    _upload(http, token, existing, "vault.bin", store.path().read_bytes(), version)
    _upload(http, token, existing, "settings.airtac", _settings_blob(), version)
    _set("sync_version", version)
    _set("last_sync", time.strftime("%d %b %H:%M"))
    return {"version": version, "files": ["vault.bin", "settings.airtac"]}


def pull(http: httpx.Client | None = None) -> dict:
    """Download from Drive. Current files are backed up first (data/backups/restore-<time>/)."""
    import shutil
    from plugins.passwords import store
    if not has_scope("drive"):
        raise AccountError("Switch on Drive sync first.")
    http = http or httpx.Client(timeout=60)
    token = access_token(http)
    existing = _drive_files(http, token)
    if "vault.bin" not in existing:
        raise AccountError("There's no AIRTAC copy in your Drive yet.")
    backup = config.DB_PATH.parent / "backups" / f"restore-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir(parents=True, exist_ok=True)

    def download(name: str) -> bytes:
        r = http.get(f"{DRIVE}/{existing[name]['id']}", params={"alt": "media"}, headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            raise AccountError(f"Drive didn't send {name}.")
        return r.content
    vault_bytes = download("vault.bin")
    json.loads(vault_bytes)                        # a vault file is JSON; anything else is refused
    if store.path().exists():
        shutil.copy2(store.path(), backup / "vault.bin")
    store.lock()
    store.path().parent.mkdir(parents=True, exist_ok=True)
    store.path().write_bytes(vault_bytes)
    restored = ["vault.bin"]
    if "settings.airtac" in existing:
        (backup / "settings.airtac").write_bytes(download("settings.airtac"))
        restored.append("settings (after you unlock: Restore settings)")
    _set("sync_version", max([int(f.get("appProperties", {}).get("airtac_version", 0)) for f in existing.values()] or [0]))
    return {"restored": restored, "backup": str(backup)}


def apply_settings(backup_dir: str = "") -> list[str]:
    """After unlocking the restored vault: decrypt the downloaded settings and put the files back."""
    import shutil
    folder = config.DB_PATH.parent / "backups"
    found = sorted(folder.glob("restore-*/settings.airtac")) if not backup_dir else [folder / backup_dir / "settings.airtac"]
    if not found or not found[-1].is_file():
        raise AccountError("Restore from Drive first.")
    data = _open_settings(found[-1].read_bytes())
    done, paths = [], settings_paths()
    for rel, text in data["files"].items():
        if rel not in paths:
            continue                      # only the files AIRTAC itself uploads
        path = paths[rel]
        if path.exists():
            shutil.copy2(path, found[-1].parent / path.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        done.append(rel)
    for k, (plug, key) in {"daily": ("daily", "settings"), "learn_done": ("learn", "done")}.items():
        if data["kv"].get(k):
            memory.kv_set(plug, key, data["kv"][k])
    return done


# ---------------------------------------------------------------- Google Calendar (read-only) into Schedule

def calendar_items(http: httpx.Client | None = None, days: int = 90) -> list[dict]:
    from datetime import datetime, timedelta, timezone
    if not has_scope("calendar"):
        raise AccountError("Switch on Google Calendar first (it asks Google for read-only access).")
    http = http or httpx.Client(timeout=30)
    token = access_token(http)
    now = datetime.now(timezone.utc)
    r = http.get(CALENDAR, params={"timeMin": now.isoformat(), "timeMax": (now + timedelta(days=days)).isoformat(),
                                   "singleEvents": "true", "orderBy": "startTime", "maxResults": 250},
                 headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise AccountError("Google Calendar didn't answer. Try again.")
    from plugins.schedule import importer
    out = []
    for e in r.json().get("items", []):
        start = e.get("start", {})
        when = start.get("dateTime") or start.get("date") or ""
        title = (e.get("summary") or "").strip()
        if not when or not title:
            continue
        if "T" in when:
            dt = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone()
            day, at = dt.date().isoformat(), dt.strftime("%H:%M")
        else:
            day, at = when[:10], ""
        out.append({"title": title[:120], "date": day, "time": at, "category": importer._guess(title),
                    "notes": "", "source": "Google Calendar"})
    return out


# ---------------------------------------------------------------- routes (this PC only)

@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Request
    from pydantic import BaseModel, Field as F

    class Start(BaseModel):
        features: list[str] = F(default_factory=list, max_length=3)

    class Push(BaseModel):
        force: bool = False

    def run(fn, *a):
        try:
            return fn(*a)
        except SignInAgain as e:
            raise HTTPException(409, str(e))   # "Sign in again"
        except AccountError as e:
            raise HTTPException(400, str(e))
        except connections.Locked as e:
            raise HTTPException(423, str(e))
        except Exception as e:
            from plugins.passwords import store
            if isinstance(e, store.VaultError):
                raise HTTPException(423 if isinstance(e, store.Locked) else 400, str(e))
            raise

    @router.get("")
    def read(request: Request):
        local_only(request)
        return {**status(), "error": _pending.get("error", "")}

    @router.post("/signin")
    def signin(body: Start, request: Request):
        local_only(request)
        if any(f not in SCOPES for f in body.features):
            raise HTTPException(400, "Unknown feature.")
        return {"url": run(start, body.features)}

    @router.post("/signout")
    def signout(request: Request):
        local_only(request)
        run(sign_out)
        return status()

    @router.post("/sync")
    def sync_now(body: Push, request: Request):
        local_only(request)
        r = run(push, None, body.force)
        _set("sync_on", True)
        return r

    @router.post("/restore")
    def restore(request: Request):
        local_only(request)
        return run(pull)

    @router.post("/restore/settings")
    def restore_settings(request: Request):
        local_only(request)
        return {"restored": run(apply_settings)}

    @router.post("/calendar")
    def calendar(request: Request):
        local_only(request)
        from plugins.schedule import save_items
        return run(lambda: save_items(calendar_items()))
