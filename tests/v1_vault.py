# Test helper: the OLD vault (v1) exactly as it was before 17 Oct 2026, used only to make sample old vaults
# for the migration test. The app no longer writes this format (see plugins/passwords/legacy.py).
"""The password vault: your logins, encrypted with a master passcode that is never stored.

How it protects your passwords:
- One file, data/vault.db (gitignored, and never in a backup or a PDF). Each user name, password and
  note is encrypted with Fernet (AES-128 in CBC mode with an HMAC-SHA256 check, from the
  `cryptography` package). Only the site's name and address are stored in plain text, so the page can
  list what you saved while the vault is locked.
- The encryption key is made from your master passcode with scrypt (a slow, memory-hard function:
  about 32 MB and a fraction of a second per try) and a random salt. The passcode itself is never
  stored anywhere, so if you forget it, nobody can open the vault, not even you. There is no reset.
- The key lives only in AIRTAC's memory while the vault is unlocked. It locks after 5 minutes without
  use, when you press Lock, and when AIRTAC stops.
- 5 wrong passcodes in a row lock the vault for 10 minutes (remembered across restarts).

What it can't protect against: malware running as you on this PC while the vault is unlocked, or
someone who watches you type the passcode. A copied password sits on the clipboard for 30 seconds.
"""
import base64
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from airtac import config

SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}     # about 32 MB of memory per try: slow for guessers, fine for one unlock
IDLE_SECONDS = 300                          # lock after 5 minutes without use
MAX_TRIES, WAIT_SECONDS = 5, 600            # 5 wrong passcodes, then a 10-minute wait
MIN_PASSCODE = 8
CHECK = b"AIRTAC vault v1"                  # encrypted at setup; decrypting it proves the passcode
FIELDS = ("username", "password", "notes")
LIMITS = {"site": 80, "url": 300, "app_id": 60, "username": 200, "password": 500, "notes": 1000}


class VaultError(Exception):
    """Something the user should read (a wrong passcode, a locked vault). Never contains a secret."""


class Locked(VaultError):
    pass


_state = {"key": None, "last": 0.0}
_lock = threading.RLock()


def path() -> Path:
    return config.DB_PATH.parent / "vault.db"


def _db() -> sqlite3.Connection:
    path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path())
    conn.execute("PRAGMA secure_delete = ON")   # deleted entries are overwritten, not just unlinked
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY,
            site TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            app_id TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            password TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            updated TEXT NOT NULL DEFAULT ''
        );""")
    return conn


def _meta(conn, key: str, default=None):
    row = conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _set_meta(conn, key: str, value) -> None:
    conn.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (key, json.dumps(value)))


def _derive(passcode: str, salt: bytes, params: dict) -> Fernet:
    kdf = Scrypt(salt=salt, length=32, n=params["n"], r=params["r"], p=params["p"])
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passcode.encode("utf-8"))))


def exists() -> bool:
    if not path().exists():
        return False
    with _db() as conn:
        return _meta(conn, "salt") is not None


def _check_passcode_shape(passcode: str) -> None:
    if len(passcode or "") < MIN_PASSCODE:
        raise VaultError(f"The master passcode needs at least {MIN_PASSCODE} characters. Longer is better.")
    if len(passcode) > 200:
        raise VaultError("That passcode is too long.")


def setup(passcode: str) -> None:
    """Make a new, empty vault with this master passcode, and unlock it."""
    _check_passcode_shape(passcode)
    with _lock, _db() as conn:
        if _meta(conn, "salt") is not None:
            raise VaultError("The vault is already set up. Unlock it, or change its passcode.")
        salt = os.urandom(16)
        f = _derive(passcode, salt, SCRYPT)
        _set_meta(conn, "salt", base64.b64encode(salt).decode())
        _set_meta(conn, "kdf", SCRYPT)
        _set_meta(conn, "check", f.encrypt(CHECK).decode())
        _set_meta(conn, "fails", [])
        _state.update(key=f, last=time.time())


# ---------------------------------------------------------------- passcode checks and the lock-out

def wait_left(now: float | None = None) -> int:
    """Seconds until another passcode may be tried (0 = now)."""
    now = time.time() if now is None else now
    if not path().exists():
        return 0
    with _db() as conn:
        return max(0, int(_meta(conn, "locked_until", 0) - now))


def _verify(passcode: str, now: float | None = None) -> Fernet:
    """The key for this passcode, or VaultError. Every wrong try counts towards the 10-minute wait."""
    now = time.time() if now is None else now
    problem = ""
    with _lock:
        # The count of wrong tries is saved before the error is raised: "with conn" rolls back on an exception.
        with _db() as conn:
            if _meta(conn, "salt") is None:
                raise VaultError("Set up the vault first.")
            left = int(_meta(conn, "locked_until", 0) - now)
            if left > 0:
                raise VaultError(f"Too many wrong passcodes. Try again in {max(1, round(left / 60))} minute{'s' if left > 90 else ''}.")
            salt = base64.b64decode(_meta(conn, "salt"))
            f = _derive(passcode or "", salt, _meta(conn, "kdf", SCRYPT))
            try:
                ok = f.decrypt(_meta(conn, "check").encode()) == CHECK
            except InvalidToken:
                ok = False
            if ok:
                _set_meta(conn, "fails", [])
                return f
            fails = [t for t in _meta(conn, "fails", []) if now - t < WAIT_SECONDS] + [now]
            if len(fails) >= MAX_TRIES:
                _set_meta(conn, "locked_until", now + WAIT_SECONDS)
                _set_meta(conn, "fails", [])
                _state.update(key=None)
                problem = "That's 5 wrong passcodes, so the vault is locked for 10 minutes."
            else:
                _set_meta(conn, "fails", fails)
                left_tries = MAX_TRIES - len(fails)
                problem = f"That passcode isn't right. {left_tries} more {'try' if left_tries == 1 else 'tries'} before a 10-minute wait."
    raise VaultError(problem)


def unlock(passcode: str) -> None:
    f = _verify(passcode)
    _state.update(key=f, last=time.time())


def lock() -> None:
    _state.update(key=None, last=0.0)


def is_unlocked(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if _state["key"] is not None and now - _state["last"] > IDLE_SECONDS:
        lock()                                  # 5 minutes without use
    return _state["key"] is not None


def idle_left(now: float | None = None) -> int:
    now = time.time() if now is None else now
    return max(0, int(IDLE_SECONDS - (now - _state["last"]))) if is_unlocked(now) else 0


def _key() -> Fernet:
    if not is_unlocked():
        raise Locked("The vault is locked. Type your master passcode to unlock it.")
    _state["last"] = time.time()               # using it keeps it open
    return _state["key"]


def _enc(f: Fernet, text: str) -> str:
    return f.encrypt(text.encode("utf-8")).decode() if text else ""


def _dec(f: Fernet, token: str) -> str:
    return f.decrypt(token.encode()).decode("utf-8") if token else ""


# ---------------------------------------------------------------- entries

def _clean(entry: dict) -> dict:
    out = {}
    for k, limit in LIMITS.items():
        v = str(entry.get(k) or "")
        if k != "password":
            v = v.strip()
        if len(v) > limit:
            raise VaultError(f"The {k.replace('_', ' ')} is too long (at most {limit} characters).")
        if k != "notes" and any(c in v for c in "\r\n\0"):
            raise VaultError(f"The {k.replace('_', ' ')} can't contain line breaks.")
        out[k] = v
    if not out["site"]:
        raise VaultError("Give the login a name, for example Instagram.")
    if out["url"] and not out["url"].startswith(("https://", "http://")):
        raise VaultError("The address should start with https://")
    return out


def entries() -> list[dict]:
    """What is saved. Unlocked: with user names (never passwords). Locked: names and addresses only."""
    open_ = is_unlocked()
    f = _key() if open_ else None
    with _db() as conn:
        rows = conn.execute("SELECT id, site, url, app_id, username, password, notes, updated FROM entries "
                            "ORDER BY site COLLATE NOCASE").fetchall()
    out = []
    for i, site, url, app_id, user, pw, notes, updated in rows:
        row = {"id": i, "site": site, "url": url, "app_id": app_id, "has_password": bool(pw), "updated": updated}
        if f:
            row.update(username=_dec(f, user), notes=_dec(f, notes))
        out.append(row)
    return out


def save(entry: dict, entry_id: int | None = None) -> int:
    """Add a login, or change one. An empty password box keeps the saved password."""
    e = _clean(entry)
    f = _key()
    now = datetime.now().strftime("%d %b %Y")
    with _lock, _db() as conn:
        if entry_id is None:
            cur = conn.execute("INSERT INTO entries (site, url, app_id, username, password, notes, updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (e["site"], e["url"], e["app_id"], _enc(f, e["username"]), _enc(f, e["password"]),
                                _enc(f, e["notes"]), now))
            return int(cur.lastrowid)
        if conn.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone() is None:
            raise VaultError("That login isn't in the vault.")
        conn.execute("UPDATE entries SET site = ?, url = ?, app_id = ?, username = ?, notes = ?, updated = ? WHERE id = ?",
                     (e["site"], e["url"], e["app_id"], _enc(f, e["username"]), _enc(f, e["notes"]), now, entry_id))
        if e["password"]:
            conn.execute("UPDATE entries SET password = ? WHERE id = ?", (_enc(f, e["password"]), entry_id))
        return entry_id


def delete(entry_id: int) -> None:
    _key()
    with _lock, _db() as conn:
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))


def _row(conn, entry_id: int):
    row = conn.execute("SELECT username, password FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise VaultError("That login isn't in the vault.")
    return row


def copy_value(entry_id: int, field: str) -> str:
    """A user name or password to copy to the clipboard (vault unlocked). The caller sends it to this PC's page only."""
    if field not in ("username", "password"):
        raise VaultError("Only a user name or a password can be copied.")
    f = _key()
    with _db() as conn:
        user, pw = _row(conn, entry_id)
    return _dec(f, user if field == "username" else pw)


def reveal(entry_id: int, passcode: str) -> str:
    """Show a password: needs the master passcode again, even while the vault is unlocked."""
    f = _verify(passcode)
    with _db() as conn:
        _, pw = _row(conn, entry_id)
    _state.update(key=f, last=time.time())
    return _dec(f, pw)


def find_for_app(app_id: str) -> int | None:
    with _db() as conn:
        row = conn.execute("SELECT id FROM entries WHERE app_id = ? ORDER BY id LIMIT 1", (app_id,)).fetchone()
    return int(row[0]) if row else None


# ---------------------------------------------------------------- new passcode, and the encrypted backup

def change_passcode(old: str, new: str) -> None:
    """Re-encrypt every entry with a key from the new passcode (and a new salt), all in one transaction."""
    _check_passcode_shape(new)
    f_old = _verify(old)
    salt = os.urandom(16)
    f_new = _derive(new, salt, SCRYPT)
    with _lock, _db() as conn:
        rows = conn.execute("SELECT id, username, password, notes FROM entries").fetchall()
        for i, user, pw, notes in rows:
            conn.execute("UPDATE entries SET username = ?, password = ?, notes = ? WHERE id = ?",
                         (_enc(f_new, _dec(f_old, user)), _enc(f_new, _dec(f_old, pw)), _enc(f_new, _dec(f_old, notes)), i))
        _set_meta(conn, "salt", base64.b64encode(salt).decode())
        _set_meta(conn, "kdf", SCRYPT)
        _set_meta(conn, "check", f_new.encrypt(CHECK).decode())
    _state.update(key=f_new, last=time.time())


def export(folder: Path) -> Path:
    """An encrypted copy of the vault: the same ciphertext and salt, readable only with your master
    passcode. Written to outputs/vault-backups/ (which normal backups and the Files page skip)."""
    f = _key()
    folder.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        rows = [dict(zip(("site", "url", "app_id", "username", "password", "notes", "updated"), r))
                for r in conn.execute("SELECT site, url, app_id, username, password, notes, updated FROM entries")]
        kdf = {**_meta(conn, "kdf", SCRYPT), "salt": _meta(conn, "salt")}
        check = _meta(conn, "check")
    # The whole list (site names too) is encrypted once more, so the file shows nothing but its format.
    data = {"format": "airtac-vault-backup", "version": 1, "made": datetime.now().isoformat(timespec="seconds"),
            "kdf": kdf, "check": check, "payload": f.encrypt(json.dumps(rows).encode("utf-8")).decode()}
    out = folder / f"airtac-vault-{datetime.now():%Y%m%d-%H%M%S}.airtacvault"
    out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return out


def read_backup(file: Path, passcode: str) -> list[dict]:
    """The logins in a backup file, decrypted with the passcode the vault had when it was exported.
    (For checking a backup; restoring it is a planned step.)"""
    data = json.loads(file.read_text(encoding="utf-8"))
    if data.get("format") != "airtac-vault-backup":
        raise VaultError("That isn't an AIRTAC vault backup.")
    f = _derive(passcode, base64.b64decode(data["kdf"]["salt"]), data["kdf"])
    try:
        rows = json.loads(f.decrypt(data["payload"].encode()))
    except InvalidToken:
        raise VaultError("That passcode doesn't open this backup.")
    return [{**r, **{k: _dec(f, r[k]) for k in FIELDS}} for r in rows]


def count() -> int:
    with _db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0])


def status() -> dict:
    """Safe to ask often: it never keeps the vault open (only using it does)."""
    made = exists()
    return {"exists": made, "unlocked": made and is_unlocked(), "wait_seconds": wait_left() if made else 0,
            "idle_seconds": IDLE_SECONDS, "idle_left": idle_left() if made else 0, "count": count() if made else 0}
