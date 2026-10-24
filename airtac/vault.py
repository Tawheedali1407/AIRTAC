"""Service secrets: API keys and site session cookies.

Since Part D (17 Oct 2026) they live in the vault (data/vault.bin, plugins/passwords/store.py: AES-256-GCM,
sealed to your Windows account so cloud brains work while the vault is locked). Before the vault is set
up they stay where they were: the operating system's own password store, through the `keyring` package (MIT):
on Windows that is Credential Manager, encrypted with your Windows sign-in (DPAPI). Nothing secret
is written to AIRTAC's folder, so it is never in a backup, a portable zip or a copy of data/.
Only a small list of what is saved (which site, which field, when) lives in AIRTAC's database.

The rules, enforced here and in the routes that use this:
- a secret never goes to the AI model, the chat history, a log line or a tool result;
- it can only be read back on this PC (the page asks for it from localhost, never from your phone);
- a secret is used only to call the site it belongs to (for example LeetCode's own address).

Windows limits one credential to 2,560 bytes, so long values (a full cookie header) are split
into numbered parts and joined again on read.
"""
import json
import logging
from datetime import datetime

from airtac import memory

log = logging.getLogger("airtac.vault")

SERVICE = "AIRTAC Advance"
FIELDS = ("password", "cookie", "key")   # key: an API key saved on the Connections page
CHUNK = 1000            # characters per stored part (Credential Manager holds about 1,280)
MAX_LEN = 16000


class VaultUnavailable(Exception):
    """No safe password store on this computer. The message is a sentence for the user."""


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        raise VaultUnavailable("The password vault needs the keyring package. Run: .venv\\Scripts\\pip install keyring")
    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring) or "Plaintext" in type(backend).__name__:
        raise VaultUnavailable("This computer has no safe password store, so AIRTAC won't keep passwords here.")
    return keyring


def available() -> tuple[bool, str]:
    if _store() is not None:
        return True, "AIRTAC vault (AES-256-GCM)"
    try:
        kr = _keyring()
    except VaultUnavailable as e:
        return False, str(e)
    return True, type(kr.get_keyring()).__name__


def _index() -> dict:
    try:
        return json.loads(memory.kv_get("vault", "index") or "{}")
    except ValueError:
        return {}


def _save_index(index: dict) -> None:
    memory.kv_set("vault", "index", json.dumps(index))


def _name(owner: str, site: str, field: str, part: int) -> str:
    return f"{owner}/{site}/{field}/{part}"


def _store():
    """The vault v2, when it exists (else None: keyring is used, as before)."""
    try:
        from plugins.passwords import store
    except Exception:
        return None
    return store if store.exists() else None


def legacy_all() -> dict[str, str]:
    """Every secret still in Windows Credential Manager, to move into the vault when it's made."""
    out = {}
    try:
        kr = _keyring()
    except VaultUnavailable:
        return out
    for name, entry in _index().items():
        owner, _, site = name.partition("/")
        for field, info in entry.items():
            chunks = [kr.get_password(SERVICE, _name(owner, site, field, i)) for i in range(info.get("parts", 0))]
            if chunks and all(c is not None for c in chunks):
                out[f"{owner}/{site}/{field}"] = "".join(chunks)
    return out


def save(owner: str, site: str, field: str, value: str) -> None:
    """Store one secret (owner = the plugin, e.g. "accounts")."""
    if field not in FIELDS:
        raise ValueError(f"field must be one of {FIELDS}")
    value = (value or "").strip()
    if not value:
        return delete(owner, site, field)
    if len(value) > MAX_LEN:
        raise ValueError("That's too long to store.")
    store = _store()
    if store is not None:
        store.service_set(f"{owner}/{site}/{field}", value)
        index = _index()
        index.setdefault(f"{owner}/{site}", {})[field] = {"parts": 0, "saved": datetime.now().strftime("%d %b %Y"), "in": "vault"}
        _save_index(index)
        log.info("vault: saved %s for %s/%s", field, owner, site)   # never the value
        return
    kr = _keyring()
    old_parts = _index().get(f"{owner}/{site}", {}).get(field, {}).get("parts", 0)
    parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)]
    for i, chunk in enumerate(parts):
        kr.set_password(SERVICE, _name(owner, site, field, i), chunk)
    for i in range(len(parts), old_parts):   # a shorter value: remove the leftover parts
        try:
            kr.delete_password(SERVICE, _name(owner, site, field, i))
        except Exception:
            pass
    index = _index()
    index.setdefault(f"{owner}/{site}", {})[field] = {"parts": len(parts), "saved": datetime.now().strftime("%d %b %Y")}
    _save_index(index)
    log.info("vault: saved %s for %s/%s", field, owner, site)   # never the value


def get(owner: str, site: str, field: str) -> str:
    """The secret, or "" when nothing is saved. Callers must not log or display it."""
    entry = _index().get(f"{owner}/{site}", {}).get(field)
    if not entry:
        return ""
    store = _store()
    if store is not None:
        try:
            value = store.service_get(f"{owner}/{site}/{field}")
        except store.VaultError:      # sealed on another PC: unlock once here
            value = ""
        if value or entry.get("in") == "vault":
            return value
    try:
        kr = _keyring()
    except VaultUnavailable:
        return ""
    chunks = [kr.get_password(SERVICE, _name(owner, site, field, i)) for i in range(entry["parts"])]
    return "" if any(c is None for c in chunks) else "".join(chunks)


def delete(owner: str, site: str, field: str | None = None) -> None:
    index = _index()
    entry = index.get(f"{owner}/{site}", {})
    store = _store()
    if store is not None:
        for f in ([field] if field else list(entry)):
            try:
                store.service_set(f"{owner}/{site}/{f}", None)
            except store.VaultError:
                pass
    try:
        kr = _keyring()
    except VaultUnavailable:
        kr = None
    for f in ([field] if field else list(entry)):
        for i in range(entry.get(f, {}).get("parts", 0)):
            try:
                if kr:
                    kr.delete_password(SERVICE, _name(owner, site, f, i))
            except Exception:
                pass
        entry.pop(f, None)
    if entry:
        index[f"{owner}/{site}"] = entry
    else:
        index.pop(f"{owner}/{site}", None)
    _save_index(index)


def summary(owner: str, site: str, fields: tuple = ("password", "cookie")) -> dict:
    """What is saved, safe to show anywhere: {"password": "12 Oct 2026" or "", "cookie": ...}."""
    entry = _index().get(f"{owner}/{site}", {})
    return {f: entry.get(f, {}).get("saved", "") for f in fields}
