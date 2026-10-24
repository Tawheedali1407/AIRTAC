"""Read the old vault (v1: data/vault.db, scrypt + Fernet) once, to move it into vault v2.

Nothing writes this format any more. migrate() in store.py calls read_all() with the master passcode,
re-encrypts everything in the new format, and keeps the old file as a dated backup in data/backups/.
"""
import base64
import json
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from airtac import config

CHECK = b"AIRTAC vault v1"
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


def path() -> Path:
    return config.DB_PATH.parent / "vault.db"


def _meta(conn, key, default=None):
    row = conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def exists() -> bool:
    if not path().exists():
        return False
    with sqlite3.connect(path()) as conn:
        try:
            return _meta(conn, "salt") is not None
        except sqlite3.Error:
            return False


def locked_until() -> float:
    with sqlite3.connect(path()) as conn:
        return float(_meta(conn, "locked_until", 0) or 0)


def _fernet(passcode: str, salt: bytes, params: dict) -> Fernet:
    kdf = Scrypt(salt=salt, length=32, n=params["n"], r=params["r"], p=params["p"])
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passcode.encode("utf-8"))))


def read_all(passcode: str) -> list[dict] | None:
    """Every login, decrypted; None when the passcode is wrong. Raises ValueError if the old vault is
    still in its 10-minute wait."""
    left = locked_until() - time.time()
    if left > 0:
        raise ValueError(f"The old vault is still locked after wrong passcodes. Try again in {max(1, round(left / 60))} minutes.")
    with sqlite3.connect(path()) as conn:
        f = _fernet(passcode or "", base64.b64decode(_meta(conn, "salt")), _meta(conn, "kdf", SCRYPT))
        try:
            if f.decrypt(_meta(conn, "check").encode()) != CHECK:
                return None
        except InvalidToken:
            return None
        rows = conn.execute("SELECT site, url, app_id, username, password, notes, updated FROM entries ORDER BY id").fetchall()

    def dec(token: str) -> str:
        return f.decrypt(token.encode()).decode("utf-8") if token else ""
    return [{"site": s, "url": u, "app_id": a, "username": dec(us), "password": dec(pw), "notes": dec(n), "updated": up}
            for s, u, a, us, pw, n, up in rows]
