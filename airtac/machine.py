"""Protect small secrets with this Windows account (DPAPI), so AIRTAC can use them without a password.

Used for two things only:
- the vault's "service" key slot: API keys must work while the vault's window is locked (cloud brains
  answer in the background), so their data key is also sealed to your Windows sign-in. Seeing or
  changing a key still needs the super password;
- the key that signs the vault's wrong-password counter, so editing the file can't reset it.

On Windows this is CryptProtectData (DPAPI): only your Windows account on this PC can unseal it; a copied
file is useless elsewhere. On other systems (the tests, Linux) a random key in data/.machine.key
(readable by this user only) stands in for it.
"""
import ctypes
import os
import sys
from pathlib import Path

from airtac import config

ENTROPY = b"AIRTAC Advance machine seal v1"


def _file_key() -> bytes:
    path = Path(config.DB_PATH).parent / ".machine.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
    return path.read_bytes()


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> _Blob:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _out(blob: _Blob) -> bytes:
        data = ctypes.string_at(blob.pbData, blob.cbData)
        ctypes.windll.kernel32.LocalFree(blob.pbData)
        return data

    def seal(data: bytes) -> bytes:
        out = _Blob()
        ent = _blob(ENTROPY)
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(_blob(data)), "AIRTAC", ctypes.byref(ent),
                                                      None, None, 0x1, ctypes.byref(out)):   # 0x1: no UI
            raise OSError("Windows couldn't protect the data (DPAPI).")
        return _out(out)

    def unseal(data: bytes) -> bytes:
        out = _Blob()
        ent = _blob(ENTROPY)
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(_blob(data)), None, ctypes.byref(ent),
                                                        None, None, 0x1, ctypes.byref(out)):
            raise ValueError("This was sealed by another Windows account or PC.")
        return _out(out)
else:
    def seal(data: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        return nonce + AESGCM(_file_key()).encrypt(nonce, data, ENTROPY)

    def unseal(data: bytes) -> bytes:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            return AESGCM(_file_key()).decrypt(data[:12], data[12:], ENTROPY)
        except (InvalidTag, ValueError):
            raise ValueError("This was sealed on another machine.")


def owner_only(path: Path) -> None:
    """Let only this user read the file: an ACL on Windows (no inherited access), mode 600 elsewhere."""
    try:
        if sys.platform == "win32":
            import subprocess
            user = os.environ.get("USERNAME", "")
            if user:
                subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],  # noqa: S603,S607 (fixed command)
                               capture_output=True, timeout=20)
        else:
            os.chmod(path, 0o600)
    except Exception:   # the file is encrypted anyway; the ACL is an extra layer
        pass
