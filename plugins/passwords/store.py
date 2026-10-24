"""The vault (v2): your logins and your API keys, in one encrypted file, data/vault.bin.

How it protects them (plain words; the threat model is in docs/SECURITY.md):
- Your super password goes through Argon2id (about 64 MiB of memory and half a second to a second per
  try, tuned to this PC), which makes a key-encryption key. That key only unwraps two random 256-bit
  data keys (an "envelope"): one for logins, one for API keys and site cookies ("service").
- Every login and every key is sealed on its own with AES-256-GCM and a random nonce. The vault's
  identity (format, version, id) is part of every seal, so a changed or swapped piece is detected.
  A sealed list of what's inside catches a deleted or added entry.
- A recovery key (shown once at setup) opens a second slot, so a forgotten super password isn't the end.
  Forgetting both means the data is lost: there is no back door.
- The service data key is also sealed to your Windows account (DPAPI, airtac/machine.py), so cloud
  brains keep working while the vault is locked. Seeing or changing a key still needs the super password.
- Locked by default; locks after 5 minutes idle, when the page is hidden, and with the Lock button.
- 5 wrong tries are free; then a wait of 30 s that doubles each time up to 1 hour. The counter is signed
  with a Windows-sealed key, so editing the file can't reset it.
- Written atomically (a temporary file, then a swap) with the previous copy as vault.bin.bak; only your
  Windows user may read it. The super password and plaintext are never written anywhere.
The old vault (v1, data/vault.db, scrypt + Fernet) moves in on the first unlock: see migrate().
"""
import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from airtac import config, machine

FORMAT, VERSION = "airtac-vault", 2
ARGON = {"m": 65536, "p": 2}            # KiB (64 MiB) and lanes; time cost is tuned per PC
TARGET_SECONDS = 0.7                   # how long one try should take on this PC
IDLE_SECONDS = 300                     # lock after 5 minutes without use
FREE_TRIES, FIRST_WAIT, MAX_WAIT = 5, 30, 3600
MIN_PASSWORD = 12
FIELDS = ("username", "password", "notes")
LIMITS = {"site": 80, "url": 300, "app_id": 60, "username": 200, "password": 500, "notes": 1000}
REVEALS_PER_MINUTE = 20


class VaultError(Exception):
    """Something the user should read (a wrong password, a locked vault). Never contains a secret."""


class Locked(VaultError):
    pass


class Tampered(VaultError):
    pass


_state = {"login": None, "service": None, "last": 0.0, "weak": False}
_lock = threading.RLock()
_reveals: deque = deque()


def path() -> Path:
    return config.DB_PATH.parent / "vault.bin"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _ub64(s: str) -> bytes:
    return base64.b64decode(s)


def _wipe(buf) -> None:
    """Overwrite a key in memory (as far as Python allows: bytearrays can be wiped, bytes can't)."""
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0


def audit(what: str) -> None:
    """One line in data/logs/audit.log for every reveal, copy, unlock and change. Never a value."""
    try:
        folder = config.DB_PATH.parent / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "audit.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {what}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- keys and seals

def tune() -> dict:
    """Argon2id settings for a fresh key: 64 MiB, and as many passes as fit in about 0.7 s here."""
    try:
        from argon2.low_level import Type, hash_secret_raw
    except ImportError:          # a clear sentence instead of a crash (Ali, 18 Oct: setting the super password failed)
        raise VaultError("The vault needs the argon2-cffi package, which isn't installed yet. Double-click "
                         "Install-AIRTAC.cmd (it installs what's missing), then restart AIRTAC.")
    salt = os.urandom(16)
    start = time.perf_counter()
    hash_secret_raw(b"tuning", salt, time_cost=1, memory_cost=ARGON["m"], parallelism=ARGON["p"], hash_len=32, type=Type.ID)
    one = max(time.perf_counter() - start, 0.01)
    t = max(1, min(10, math.floor(TARGET_SECONDS / one)))
    return {"alg": "argon2id", "t": t, "m": ARGON["m"], "p": ARGON["p"], "salt": _b64(salt)}


def _kek(secret: str, kdf: dict) -> bytearray:
    try:
        from argon2.low_level import Type, hash_secret_raw
    except ImportError:
        raise VaultError("The vault needs the argon2-cffi package, which isn't installed yet. Double-click "
                         "Install-AIRTAC.cmd (it installs what's missing), then restart AIRTAC.")
    if kdf.get("alg") != "argon2id":
        raise Tampered("The vault file names a key method AIRTAC doesn't use. It may have been changed.")
    raw = bytearray(secret.encode("utf-8"))
    try:
        return bytearray(hash_secret_raw(bytes(raw), _ub64(kdf["salt"]), time_cost=int(kdf["t"]), memory_cost=int(kdf["m"]),
                                         parallelism=int(kdf["p"]), hash_len=32, type=Type.ID))
    finally:
        _wipe(raw)


def _seal(key, data: bytes, aad: bytes) -> str:
    nonce = os.urandom(12)
    return _b64(nonce + AESGCM(bytes(key)).encrypt(nonce, data, aad))


def _open(key, token: str, aad: bytes) -> bytes:
    raw = _ub64(token)
    return AESGCM(bytes(key)).decrypt(raw[:12], raw[12:], aad)


def _core(v: dict) -> bytes:
    """The vault's identity, bound into every seal (AAD)."""
    c = v["header"]
    return json.dumps({"format": c["format"], "version": c["version"], "id": c["id"]}, sort_keys=True,
                      separators=(",", ":")).encode()


def _slot_aad(v: dict, slot: str, which: str, kdf: dict) -> bytes:
    return _core(v) + f"|slot|{slot}|{which}|".encode() + json.dumps(kdf, sort_keys=True).encode()


def recovery_key() -> str:
    """32 letters and digits in groups of 4 (160 random bits): easy to write down, impossible to guess."""
    raw = base64.b32encode(os.urandom(20)).decode().rstrip("=")
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _norm_recovery(text: str) -> str:
    return "".join(c for c in (text or "").upper() if c.isalnum())


# ---------------------------------------------------------------- the file

def exists() -> bool:
    return path().exists()


def needs_migration() -> bool:
    from plugins.passwords import legacy
    return not exists() and legacy.exists()


def _load() -> dict:
    try:
        v = json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise VaultError("Set up the vault first.")
    except (ValueError, UnicodeDecodeError):
        raise Tampered("The vault file is damaged. Restore data\\vault.bin.bak or a backup.")
    if not isinstance(v, dict) or v.get("header", {}).get("format") != FORMAT or v["header"].get("version") != VERSION:
        raise Tampered("That isn't an AIRTAC vault file (or it was changed).")
    return v


def _write(v: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(v, indent=1))
        f.flush()
        os.fsync(f.fileno())
    if p.exists():
        shutil.copy2(p, p.with_suffix(".bin.bak"))
        machine.owner_only(p.with_suffix(".bin.bak"))
    os.replace(tmp, p)
    machine.owner_only(p)


# ---------------------------------------------------------------- the wrong-try counter

def _guard_key(v: dict) -> bytes:
    return machine.unseal(_ub64(v["guard_key"]))


def _guard(v: dict) -> tuple[int, float, bool]:
    """(wrong tries, locked until, signature ok)."""
    g = v.get("guard", {})
    try:
        want = hmac.new(_guard_key(v), _core(v) + json.dumps([g["fails"], g["until"]]).encode(), hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(want, g.get("mac", ""))
    except Exception:
        ok = False
    if not ok:      # someone edited the counter (or the file came from another PC): treat it as the longest wait
        return FREE_TRIES + 10, 0.0, False
    return int(g["fails"]), float(g["until"]), True


def _set_guard(v: dict, fails: int, until: float) -> None:
    mac = hmac.new(_guard_key(v), _core(v) + json.dumps([fails, until]).encode(), hashlib.sha256).hexdigest()
    v["guard"] = {"fails": fails, "until": until, "mac": mac}


def wait_left(now: float | None = None) -> int:
    now = time.time() if now is None else now
    if not exists():
        return 0
    try:
        v = _load()
        fails, until, ok = _guard(v)
    except VaultError:
        return 0
    return max(0, int(until - now))


def _wrong(v: dict, now: float) -> str:
    fails, _until, ok = _guard(v)
    fails = (fails if ok else FREE_TRIES) + 1
    until = 0.0
    if fails >= FREE_TRIES:
        until = now + min(MAX_WAIT, FIRST_WAIT * 2 ** (fails - FREE_TRIES))
    _set_guard(v, fails, until)
    _write(v)
    audit("wrong password")
    if until:
        wait = int(until - now)
        return f"That isn't right. Wait {wait} seconds before the next try (it doubles each time, up to an hour)."
    left = FREE_TRIES - fails
    return f"That isn't right. {left} more {'try' if left == 1 else 'tries'} before a wait."


def _check_wait(v: dict, now: float) -> None:
    fails, until, ok = _guard(v)
    if not ok:
        _set_guard(v, FREE_TRIES, now + MAX_WAIT)
        _write(v)
        audit("counter signature invalid: locked for an hour")
        raise VaultError("The vault's try counter was changed outside AIRTAC, so it waits an hour.")
    if until > now:
        raise VaultError(f"Too many wrong tries. Wait {int(until - now)} seconds.")


# ---------------------------------------------------------------- making a vault

def strength(password: str) -> dict:
    """A rough strength score 0-4 (length and variety; common passwords score 0). Shown as a meter."""
    p = password or ""
    kinds = sum(bool(any(f(c) for c in p)) for f in (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum()))
    common = p.lower() in {"password1234", "qwertyuiop12", "123456789012", "airtacairtac", "iloveyou1234"} or len(set(p)) < 5  # gitleaks:allow (examples of weak passwords, not secrets)
    score = 0 if common else min(4, (len(p) >= 12) + (len(p) >= 16) + (kinds >= 3) + (kinds == 4 or len(p) >= 20))
    return {"score": score, "label": ["very weak", "weak", "fair", "good", "strong"][score]}


def _check_new_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise VaultError(f"The super password needs at least {MIN_PASSWORD} characters. A short sentence is easy to remember.")
    if len(password) > 200:
        raise VaultError("That password is too long.")
    if strength(password)["score"] == 0:
        raise VaultError("That password is too easy to guess. Use a longer, less common one.")


def _new_vault(password: str, logins: list[dict], service: dict[str, str]) -> tuple[dict, str]:
    v = {"header": {"format": FORMAT, "version": VERSION, "id": secrets.token_hex(8),
                    "created": datetime.now().isoformat(timespec="seconds")}}
    dk_login, dk_service = bytearray(os.urandom(32)), bytearray(os.urandom(32))
    rec = recovery_key()
    v["guard_key"] = _b64(machine.seal(os.urandom(32)))
    _set_guard(v, 0, 0.0)
    _wrap(v, "password", password, dk_login, dk_service)
    _wrap(v, "recovery", _norm_recovery(rec), dk_login, dk_service)
    v["machine"] = _b64(machine.seal(bytes(dk_service)))
    v["logins"], v["service"] = {}, {}
    ids = []
    for n, e in enumerate(logins, 1):
        v["logins"][str(n)] = _seal(dk_login, json.dumps(e).encode(), _core(v) + f"|login|{n}".encode())
        ids.append(n)
    v["manifest"] = _seal(dk_login, json.dumps({"ids": ids, "next": len(ids) + 1}).encode(), _core(v) + b"|manifest")
    for name, value in service.items():
        v["service"][name] = _seal(dk_service, value.encode(), _core(v) + f"|service|{name}".encode())
    v["service_manifest"] = _seal(dk_service, json.dumps(sorted(service)).encode(), _core(v) + b"|service-manifest")
    _state.update(login=dk_login, service=dk_service, last=time.time())
    return v, rec


def _wrap(v: dict, slot: str, secret: str, dk_login, dk_service) -> None:
    kdf = tune()
    kek = _kek(secret, kdf)
    try:
        v.setdefault("slots", {})[slot] = {
            "kdf": kdf,
            "login": _seal(kek, bytes(dk_login), _slot_aad(v, slot, "login", kdf)),
            "service": _seal(kek, bytes(dk_service), _slot_aad(v, slot, "service", kdf))}
    finally:
        _wipe(kek)


def setup(password: str) -> str:
    """Make a new, empty vault (API keys saved before move in). Returns the recovery key, shown once."""
    _check_new_password(password)
    with _lock:
        if exists() or needs_migration():
            raise VaultError("The vault is already set up. Unlock it, or change its password.")
        v, rec = _new_vault(password, [], _old_service_secrets())
        _write(v)
    audit("vault created")
    return rec


def _old_service_secrets() -> dict[str, str]:
    """API keys and site cookies kept in Windows Credential Manager before v2: they move into the vault."""
    try:
        from airtac import vault as old
        return old.legacy_all()
    except Exception:
        return {}


def migrate(password: str) -> str:
    """Move the old vault (v1) into v2 with the same password. data\\ is backed up first; the old file
    goes to data\\backups\\ with today's date. Returns the new recovery key."""
    from plugins.passwords import legacy
    with _lock:
        rows = legacy.read_all(password)
        if rows is None:
            raise VaultError("That isn't the vault's password.")
        try:
            from plugins.backup import make_backup
            make_backup()
        except Exception as e:
            raise VaultError(f"Couldn't back up the data folder first ({type(e).__name__}), so nothing was changed.")
        v, rec = _new_vault(password, rows, _old_service_secrets())
        _write(v)
        folder = config.DB_PATH.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy.path()), str(folder / f"vault-v1-{datetime.now():%Y%m%d-%H%M%S}.db"))
    _state["weak"] = len(password) < MIN_PASSWORD
    audit(f"migrated the v1 vault ({len(rows)} logins)")
    return rec


# ---------------------------------------------------------------- unlocking

def _unwrap(v: dict, slot: str, secret: str) -> tuple[bytearray, bytearray] | None:
    s = v.get("slots", {}).get(slot)
    if not s:
        raise Tampered("The vault file is missing a key slot. Restore data\\vault.bin.bak or a backup.")
    kek = _kek(secret, s["kdf"])
    try:
        return (bytearray(_open(kek, s["login"], _slot_aad(v, slot, "login", s["kdf"]))),
                bytearray(_open(kek, s["service"], _slot_aad(v, slot, "service", s["kdf"]))))
    except InvalidTag:
        return None
    finally:
        _wipe(kek)


def _ids(v: dict, dk) -> list[int]:
    try:
        ids = json.loads(_open(dk, v["manifest"], _core(v) + b"|manifest"))["ids"]
    except (InvalidTag, KeyError, ValueError):
        raise Tampered("The vault file was changed outside AIRTAC. Restore data\\vault.bin.bak or a backup.")
    if sorted(int(k) for k in v.get("logins", {})) != sorted(ids):
        raise Tampered("A login was added or removed outside AIRTAC. Restore data\\vault.bin.bak or a backup.")
    return ids


def _open_with(slot: str, secret: str) -> dict:
    """Unlock with a slot's secret (the password or the recovery key). Wrong tries count."""
    now = time.time()
    with _lock:
        v = _load()
        _check_wait(v, now)
        keys = _unwrap(v, slot, secret)
        if keys is None:
            raise VaultError(_wrong(v, now))
        _ids(v, keys[0])                         # the list of logins must match, or the file was edited
        _set_guard(v, 0, 0.0)
        try:                                     # a vault copied from another PC: re-seal the service key here
            machine.unseal(_ub64(v["machine"]))
        except Exception:
            v["machine"] = _b64(machine.seal(bytes(keys[1])))
        _write(v)
        lock()
        _state.update(login=keys[0], service=keys[1], last=time.time())
        return v


def unlock(password: str) -> dict:
    """Unlock (and on the first unlock after the update, move the old vault in). {"recovery_key"} when
    a migration just made one: the page shows it once."""
    if needs_migration():
        return {"migrated": True, "recovery_key": migrate(password), "weak": len(password or "") < MIN_PASSWORD}
    _open_with("password", password or "")
    _state["weak"] = len(password or "") < MIN_PASSWORD
    audit("unlocked")
    return {"migrated": False}


def recover(key: str, new_password: str) -> None:
    """Forgot the super password: open with the recovery key and set a new password."""
    _check_new_password(new_password)
    v = _open_with("recovery", _norm_recovery(key))
    with _lock:
        _wrap(v, "password", new_password, _state["login"], _state["service"])
        _write(v)
    _state["weak"] = False
    audit("password reset with the recovery key")


def lock() -> None:
    _wipe(_state["login"])
    _state.update(login=None, last=0.0)


def is_unlocked(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if _state["login"] is not None and now - _state["last"] > IDLE_SECONDS:
        lock()
    return _state["login"] is not None


def idle_left(now: float | None = None) -> int:
    now = time.time() if now is None else now
    return max(0, int(IDLE_SECONDS - (now - _state["last"]))) if is_unlocked(now) else 0


def _key() -> bytearray:
    if not is_unlocked():
        raise Locked("The vault is locked. Type your super password to unlock it.")
    _state["last"] = time.time()
    return _state["login"]


def derived_key(label: str) -> bytes:
    """A 256-bit key for something else that belongs to this vault (the Drive sync of your settings).
    Needs the vault unlocked; the same vault on a new PC gives the same key after you unlock it."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    v = _load()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=v["header"]["id"].encode(),
                info=f"airtac {label}".encode()).derive(bytes(_key()))


def change_password(old: str, new: str) -> str | None:
    """Change the super password (it asks for the current one only when a vault exists). With no vault
    yet, this sets one up and returns the recovery key."""
    _check_new_password(new)
    if not exists() and not needs_migration():
        return setup(new)
    if needs_migration():
        unlock(old)
    v = _open_with("password", old or "")
    with _lock:
        _wrap(v, "password", new, _state["login"], _state["service"])
        _write(v)
    _state["weak"] = False
    audit("password changed")
    return None


def new_recovery_key() -> str:
    """Replace the recovery key (vault unlocked). The old one stops working."""
    _key()
    with _lock:
        v = _load()
        rec = recovery_key()
        _wrap(v, "recovery", _norm_recovery(rec), _state["login"], _state["service"])
        _write(v)
    audit("new recovery key")
    return rec


# ---------------------------------------------------------------- logins

def _clean(entry: dict) -> dict:
    out = {}
    for k, limit in LIMITS.items():
        val = str(entry.get(k) or "")
        if k != "password":
            val = val.strip()
        if len(val) > limit:
            raise VaultError(f"The {k.replace('_', ' ')} is too long (at most {limit} characters).")
        if k != "notes" and any(c in val for c in "\r\n\0"):
            raise VaultError(f"The {k.replace('_', ' ')} can't contain line breaks.")
        out[k] = val
    if not out["site"]:
        raise VaultError("Give the login a name, for example Instagram.")
    if out["url"] and not out["url"].startswith(("https://", "http://")):
        raise VaultError("The address should start with https://")
    return out


def _read(v: dict, dk, n: int) -> dict:
    try:
        return json.loads(_open(dk, v["logins"][str(n)], _core(v) + f"|login|{n}".encode()))
    except (InvalidTag, KeyError, ValueError):
        raise Tampered("A login in the vault file was changed outside AIRTAC. Restore data\\vault.bin.bak or a backup.")


def entries() -> list[dict]:
    """The logins, without passwords (vault unlocked). Locked: an empty list."""
    if not exists() or not is_unlocked():
        return []
    dk = _key()
    v = _load()
    out = []
    for n in _ids(v, dk):
        e = _read(v, dk, n)
        out.append({"id": n, "site": e["site"], "url": e["url"], "app_id": e["app_id"], "username": e["username"],
                    "notes": e["notes"], "has_password": bool(e["password"]), "updated": e.get("updated", "")})
    return sorted(out, key=lambda e: e["site"].lower())


def save(entry: dict, entry_id: int | None = None) -> int:
    """Add a login, or change one. An empty password box keeps the saved password."""
    e = _clean(entry)
    dk = _key()
    with _lock:
        v = _load()
        man = json.loads(_open(dk, v["manifest"], _core(v) + b"|manifest"))
        if entry_id is None:
            n = man["next"]
            man["ids"].append(n)
            man["next"] = n + 1
        else:
            n = int(entry_id)
            if n not in man["ids"]:
                raise VaultError("That login isn't in the vault.")
            if not e["password"]:
                e["password"] = _read(v, dk, n)["password"]
        e["updated"] = datetime.now().strftime("%d %b %Y")
        v["logins"][str(n)] = _seal(dk, json.dumps(e).encode(), _core(v) + f"|login|{n}".encode())
        v["manifest"] = _seal(dk, json.dumps(man).encode(), _core(v) + b"|manifest")
        _write(v)
    return n


def delete(entry_id: int) -> None:
    dk = _key()
    with _lock:
        v = _load()
        man = json.loads(_open(dk, v["manifest"], _core(v) + b"|manifest"))
        if int(entry_id) in man["ids"]:
            man["ids"].remove(int(entry_id))
            v["logins"].pop(str(int(entry_id)), None)
            v["manifest"] = _seal(dk, json.dumps(man).encode(), _core(v) + b"|manifest")
            _write(v)


def _rate_limit() -> None:
    now = time.time()
    while _reveals and now - _reveals[0] > 60:
        _reveals.popleft()
    if len(_reveals) >= REVEALS_PER_MINUTE:
        raise VaultError("Too many reveals in a minute. Wait a moment.")
    _reveals.append(now)


def copy_value(entry_id: int, field: str) -> str:
    """A user name or password to copy (vault unlocked). The caller sends it to this PC's page only."""
    if field not in ("username", "password"):
        raise VaultError("Only a user name or a password can be copied.")
    dk = _key()
    _rate_limit()
    v = _load()
    _ids(v, dk)
    audit(f"copied the {field} of login {int(entry_id)}")
    return _read(v, dk, int(entry_id))[field]


def reveal(entry_id: int) -> str:
    """Show a password (vault unlocked; rate-limited; written to the audit log, never the value)."""
    dk = _key()
    _rate_limit()
    v = _load()
    _ids(v, dk)
    audit(f"revealed the password of login {int(entry_id)}")
    return _read(v, dk, int(entry_id))["password"]


def find_for_app(app_id: str) -> int | None:
    """The login for an app (vault unlocked; None while locked)."""
    for e in entries():
        if e["app_id"] == app_id:
            return e["id"]
    return None


def generate(length: int = 20) -> str:
    """A random password: letters, digits and symbols, from the operating system's secure generator."""
    length = max(12, min(64, int(length)))
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_=+?"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if strength(pw)["score"] >= 3:
            return pw


# ---------------------------------------------------------------- API keys and cookies ("service")

def _service_key() -> bytearray:
    """The service data key: open when unlocked, else unsealed with this Windows account (DPAPI)."""
    if _state["service"] is None:
        v = _load()
        try:
            _state["service"] = bytearray(machine.unseal(_ub64(v["machine"])))
        except Exception:
            raise Locked("Unlock the vault once on this PC so AIRTAC can use your API keys here.")
    return _state["service"]


def service_names() -> list[str]:
    if not exists():
        return []
    dk = _service_key()
    v = _load()
    try:
        names = json.loads(_open(dk, v["service_manifest"], _core(v) + b"|service-manifest"))
    except (InvalidTag, KeyError, ValueError):
        raise Tampered("The API keys in the vault file were changed outside AIRTAC.")
    if sorted(names) != sorted(v.get("service", {})):
        raise Tampered("An API key was added or removed outside AIRTAC.")
    return names


def service_get(name: str) -> str:
    if not exists():
        return ""
    dk = _service_key()
    v = _load()
    token = v.get("service", {}).get(name)
    if not token:
        return ""
    try:
        return _open(dk, token, _core(v) + f"|service|{name}".encode()).decode("utf-8")
    except InvalidTag:
        raise Tampered("An API key in the vault file was changed outside AIRTAC.")


def service_set(name: str, value: str | None) -> None:
    """Save (or with None/"" remove) one service secret."""
    dk = _service_key()
    with _lock:
        v = _load()
        names = set(service_names())
        if value:
            v["service"][name] = _seal(dk, value.encode("utf-8"), _core(v) + f"|service|{name}".encode())
            names.add(name)
        else:
            v["service"].pop(name, None)
            names.discard(name)
        v["service_manifest"] = _seal(dk, json.dumps(sorted(names)).encode(), _core(v) + b"|service-manifest")
        _write(v)


# ---------------------------------------------------------------- backups

def export(folder: Path) -> Path:
    """An encrypted copy of the vault (the file itself: readable only with the super password or the
    recovery key). In outputs/vault-backups/, which normal backups and the Files page skip."""
    _key()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"airtac-vault-{datetime.now():%Y%m%d-%H%M%S}.airtacvault"
    shutil.copy2(path(), out)
    machine.owner_only(out)
    audit("exported an encrypted copy")
    return out


def read_backup(file: Path, password: str) -> list[dict]:
    """The logins in a backup, opened with the super password it had (for checking a backup)."""
    try:
        v = json.loads(Path(file).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise VaultError("That isn't an AIRTAC vault backup.")
    if v.get("header", {}).get("format") != FORMAT:
        raise VaultError("That isn't an AIRTAC vault backup.")
    keys = _unwrap(v, "password", password)
    if keys is None:
        raise VaultError("That password doesn't open this backup.")
    return [_read(v, keys[0], n) for n in _ids(v, keys[0])]


def count() -> int:
    if not exists():
        return 0
    try:
        return len(_load().get("logins", {}))
    except VaultError:
        return 0


def status() -> dict:
    """Safe to ask often: it never keeps the vault open (only using it does)."""
    made = exists()
    migrate_ = needs_migration()
    return {"exists": made or migrate_, "migrate": migrate_, "unlocked": made and is_unlocked(),
            "wait_seconds": wait_left() if made else 0, "idle_seconds": IDLE_SECONDS,
            "idle_left": idle_left() if made else 0, "count": count(), "weak": bool(_state["weak"]) and made,
            "min_length": MIN_PASSWORD, "version": VERSION}
