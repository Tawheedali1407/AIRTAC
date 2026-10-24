"""Passwords and API keys: one vault (store.py), on this PC only.

What it keeps: your logins (site, address, user name, password, note) and your API keys and site cookies.
How it's protected (details in store.py and docs/SECURITY.md):
- Argon2id + AES-256-GCM, with a random data key wrapped by your super password and by a recovery key;
- locked by default; locks after 5 minutes idle, when the page is hidden, and with Lock;
- 5 wrong tries, then a wait of 30 s that doubles up to an hour;
- secrets are masked; Show hides again after 10 seconds; Copy is cleared from the clipboard after 20
  seconds and kept out of Windows clipboard history (clipboard.py); every reveal and copy is audit-logged
  (data/logs/audit.log, never the value);
- works only on the PC running AIRTAC; never sent to the AI model, never in the chat, a log, an error
  message, a PDF or a backup. This plugin has no tools, so the model doesn't even know the vault exists.
"""
import logging

from airtac import config
from airtac.plugin_api import plugin

p = plugin(__name__)
log = logging.getLogger("airtac.passwords")
PC_ONLY = "Vault works on this PC only."
EXPORT_FOLDER = "vault-backups"    # inside outputs/; backups and the Files page skip it


def export_dir():
    return config.OUTPUTS_DIR / EXPORT_FOLDER


@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field

    from airtac import auth
    from plugins.passwords import store

    def here(request: Request) -> None:
        if not auth.is_local_request(request):
            raise HTTPException(403, PC_ONLY)

    def reply(data: dict) -> JSONResponse:
        # Never kept in a cache, the browser's or anyone else's.
        return JSONResponse(data, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    def fail(e: Exception):
        # The message is always one of store.py's own sentences: never a value from the vault.
        raise HTTPException(423 if isinstance(e, store.Locked) else 400, str(e))

    class Passcode(BaseModel):
        passcode: str = Field(max_length=200)

    class Setup(BaseModel):
        passcode: str = Field(max_length=200)
        again: str = Field(max_length=200)

    class Change(BaseModel):
        old: str = Field(default="", max_length=200)
        new: str = Field(max_length=200)
        again: str = Field(max_length=200)

    class Recover(BaseModel):
        key: str = Field(max_length=80)
        new: str = Field(max_length=200)
        again: str = Field(max_length=200)

    class Entry(BaseModel):
        site: str = Field(max_length=80)
        url: str = Field(default="", max_length=300)
        app_id: str = Field(default="", max_length=60)
        username: str = Field(default="", max_length=200)
        password: str = Field(default="", max_length=500)
        notes: str = Field(default="", max_length=1000)

    class Copy(BaseModel):
        field: str = Field(pattern="^(username|password)$")

    class Strength(BaseModel):
        passcode: str = Field(max_length=200)

    def same(a: str, b: str) -> None:
        if a != b:
            raise HTTPException(400, "The two passwords aren't the same.")

    @router.get("/status")
    def status(request: Request):
        if not auth.is_local_request(request):
            return reply({"here": False, "message": PC_ONLY})
        return reply({"here": True, **store.status()})

    @router.post("/strength")
    def strength(body: Strength, request: Request):
        here(request)
        return reply(store.strength(body.passcode))

    @router.post("/setup")
    def setup(body: Setup, request: Request):
        """A new vault: the password typed twice. Returns the recovery key, shown once."""
        here(request)
        same(body.passcode, body.again)
        try:
            rec = store.setup(body.passcode)
        except store.VaultError as e:
            fail(e)
        log.info("passwords: vault set up")
        return reply({"recovery_key": rec, **store.status()})

    @router.post("/unlock")
    def unlock(body: Passcode, request: Request):
        here(request)
        try:
            done = store.unlock(body.passcode)
        except store.VaultError as e:
            fail(e)
        return reply({**done, **store.status()})

    @router.post("/recover")
    def recover(body: Recover, request: Request):
        here(request)
        same(body.new, body.again)
        try:
            store.recover(body.key, body.new)
        except store.VaultError as e:
            fail(e)
        return reply(store.status())

    @router.post("/recovery-key")
    def new_recovery(request: Request):
        here(request)
        try:
            return reply({"recovery_key": store.new_recovery_key(), **store.status()})
        except store.VaultError as e:
            fail(e)

    @router.post("/lock")
    def lock(request: Request):
        here(request)
        store.lock()
        return reply(store.status())

    @router.get("/generate")
    def generate(request: Request, length: int = 20):
        here(request)
        return reply({"password": store.generate(length)})

    @router.get("/entries")
    def entries(request: Request):
        here(request)
        try:
            return reply({"entries": store.entries(), **store.status()})
        except store.VaultError as e:
            fail(e)

    @router.post("/entries")
    def add(body: Entry, request: Request):
        here(request)
        try:
            new_id = store.save(body.model_dump())
        except store.VaultError as e:
            fail(e)
        log.info("passwords: saved a login")            # never which one, never a value
        return reply({"id": new_id, "entries": store.entries()})

    @router.post("/entries/{entry_id}")
    def change(entry_id: int, body: Entry, request: Request):
        here(request)
        try:
            store.save(body.model_dump(), entry_id)
        except store.VaultError as e:
            fail(e)
        return reply({"id": entry_id, "entries": store.entries()})

    @router.post("/entries/{entry_id}/delete")
    def remove(entry_id: int, request: Request):
        here(request)
        try:
            store.delete(entry_id)
        except store.VaultError as e:
            fail(e)
        return reply({"entries": store.entries()})

    @router.post("/entries/{entry_id}/copy")
    def copy(entry_id: int, body: Copy, request: Request):
        """Copy to the clipboard. On Windows AIRTAC copies it itself and clears it after 20 seconds, so the
        value never reaches the page; elsewhere the page gets it once and copies it."""
        from plugins.passwords import clipboard
        here(request)
        try:
            value = store.copy_value(entry_id, body.field)
        except store.VaultError as e:
            fail(e)
        if clipboard.copy(value):
            return reply({"copied": "pc", "clear_after": clipboard.CLEAR_AFTER})
        return reply({"value": value, "clear_after": clipboard.CLEAR_AFTER})

    @router.post("/entries/{entry_id}/reveal")
    def reveal(entry_id: int, request: Request):
        """Show a password for 10 seconds (vault unlocked; rate-limited; audit-logged)."""
        here(request)
        try:
            return reply({"value": store.reveal(entry_id), "hide_after": 10})
        except store.VaultError as e:
            fail(e)

    @router.get("/for/{app_id}")
    def for_app(app_id: str, request: Request):
        """Is there a saved login for this app (Social and AI pages)? Only its id, never a value."""
        here(request)
        return reply({"id": store.find_for_app(app_id[:60]) if store.exists() else None, **store.status()})

    @router.post("/change")
    def change_passcode(body: Change, request: Request):
        """Change the super password; it asks for the current one only when a vault exists."""
        here(request)
        same(body.new, body.again)
        try:
            rec = store.change_password(body.old, body.new)
        except store.VaultError as e:
            fail(e)
        log.info("passwords: super password changed")
        return reply({**({"recovery_key": rec} if rec else {}), **store.status()})

    @router.post("/export")
    def export(request: Request):
        here(request)
        try:
            out = store.export(export_dir())
        except store.VaultError as e:
            fail(e)
        return reply({"file": out.name, "folder": str(out.parent)})
