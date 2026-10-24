"""Connections: the keys and services AIRTAC uses, set up from the app instead of by editing files.

A connection is one card on the Connections page: what it is, what it switches on, where to get a
key, the boxes to fill in, and a Test button that makes one real call. They come from three places,
so a new service never needs a change here:
- every brain provider with a key (airtac/providers.py, and any a plugin adds with p.brain_provider);
- plugins, with p.connector(...) (the web plugin adds Tavily, github adds its token);
- two built in: Ollama on this PC (no key, the Test lists your models) and the phone passcode.

Where a value goes: the AIRTAC vault (plugins/passwords/store.py, AES-256-GCM), sealed to your Windows
account so brains still work while the vault is locked. Before the vault exists: Windows Credential
Manager (vault.py). Values you typed into .env by hand still work; one saved here wins over .env.

The rules (enforced here and by the routes in main.py):
- the page gets "saved" and, while the vault is unlocked, the last 4 characters; never the secret,
  except through Show (unlocked, rate-limited, written to the audit log) or Copy (PC clipboard, cleared after 20 s);
- once the vault exists, seeing the list, saving, Show, Copy and Disconnect need it unlocked;
- everything works only on this PC, never from your phone;
- a saved key takes effect at once (no restart), and the failover router picks it up on the next question.
"""
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from airtac import config

log = logging.getLogger("airtac.connections")
OWNER = "keys"          # the vault's owner name for API keys (accounts uses "accounts")


@dataclass
class Field:
    key: str                  # the setting's name, e.g. GROQ_API_KEY (also its name in .env)
    label: str
    secret: bool = True       # secrets are never shown again, only their last 4 characters
    placeholder: str = ""
    required: bool = True
    min_length: int = 0
    show_end: bool = True     # show the last 4 characters (never for a passcode)
    multiline: bool = False   # a pasted JSON file (Google Cloud service account, SAP service key)
    max_length: int = 2000


@dataclass
class Connector:
    id: str
    name: str
    about: str
    powers: list[str]
    fields: list[Field]
    get_key: str = ""                                             # where to get a key
    test: Callable[[httpx.Client], tuple[bool, str]] | None = None
    order: int = 50
    group: str = "Services"                                       # Brains, Services, This PC
    note: str = ""                                                # a warning shown on the card
    plugin: str = ""
    logo: str = ""                                                # a file in web/logos/ (no .svg); "" = letters
    docs: str = ""                                                # the page that documents the call Test makes
    custom: bool = False                                          # added with "+ Add custom API key"


REGISTRY: dict[str, Connector] = {}
LAST_TEST: dict[str, dict] = {}       # also kept in the database, so "last tested" survives a restart


class Locked(Exception):
    """The vault exists and is locked: unlock it on the Passwords or APIs and keys page first."""


def _vault_store():
    """The vault v2 module when a vault exists, else None (keys then live in Credential Manager)."""
    try:
        from plugins.passwords import store
    except Exception:
        return None
    return store if store.exists() else None


def unlocked() -> bool:
    store = _vault_store()
    return store is None or store.is_unlocked()


def require_unlocked() -> None:
    if not unlocked():
        raise Locked("Unlock the vault with your super password first.")


def _load_tests() -> None:
    if LAST_TEST:
        return
    from airtac import memory
    import json
    try:
        LAST_TEST.update(json.loads(memory.kv_get("connections", "last_test") or "{}"))
    except ValueError:
        pass


def _save_tests() -> None:
    from airtac import memory
    import json
    memory.kv_set("connections", "last_test", json.dumps(LAST_TEST))


def add(c: Connector) -> Connector:
    REGISTRY[c.id] = c
    return c


# ---------------------------------------------------------------- where values live

def _vault():
    from airtac import vault
    return vault


def load_saved() -> int:
    """Read the keys saved in the vault into config, so config.secret() finds them. Called at start-up."""
    from airtac import memory
    import json
    try:
        index = json.loads(memory.kv_get("vault", "index") or "{}")
    except ValueError:
        index = {}
    found = 0
    for name, entry in index.items():
        owner, _, key = name.partition("/")
        if owner == OWNER and "key" in entry:
            value = _vault().get(OWNER, key, "key")
            if value:
                config.SAVED[key] = value
                found += 1
    _refresh()
    return found


def source(key: str) -> str:
    """"app" (saved here), "env" (typed into .env or the environment), or "" (not set)."""
    if config.SAVED.get(key):
        return "app"
    return "env" if (os.getenv(key) or "").strip() else ""


def _write_env(key: str, raw: str) -> None:
    from dotenv import set_key, unset_key
    path = config.ROOT / ".env"
    if not path.exists():
        path.write_text("# Your keys. Never commit this file.\n", encoding="utf-8")
    if raw:
        set_key(str(path), key, raw, quote_mode="never")
        os.environ[key] = raw
    else:
        unset_key(str(path), key, quote_mode="never")
        os.environ.pop(key, None)


def _store(key: str, raw: str) -> str:
    """Save one value. Returns where it went: "vault" or ".env"."""
    vault = _vault()
    try:
        if raw:
            vault.save(OWNER, key, "key", raw)
            config.SAVED[key] = raw
        else:
            vault.delete(OWNER, key, "key")
            config.SAVED.pop(key, None)
        return "vault"
    except vault.VaultUnavailable:
        _write_env(key, raw)
        return ".env"


def _refresh() -> None:
    """Make a change take effect now: the settings built from keys, and the cached brains."""
    new_passcode = config.secret("AIRTAC_PASSCODE")
    if new_passcode != config.PASSCODE:
        from airtac import auth
        auth.end_all_sessions()   # a new (or no) passcode signs every other device out
    config.PASSCODE = new_passcode
    config.LLM_API_KEY = config.secret(config.LLM_KEY_ENV)
    from airtac import brains, providers
    brains._cache.clear()
    for name in providers.PROVIDERS:
        providers.state(name).resting_until = 0   # a new key gets a fresh try


# ---------------------------------------------------------------- actions (the routes call these)

def save(cid: str, values: dict) -> str:
    require_unlocked()
    c = REGISTRY[cid]
    known = {f.key: f for f in c.fields}
    where = ""
    for key, raw in values.items():
        if key not in known or raw is None:
            continue
        f, raw = known[key], str(raw).strip()
        if not raw:
            continue          # an empty box means "keep what's saved"; Disconnect removes it
        if "\0" in raw or (not f.multiline and any(ch in raw for ch in "\n\r")):
            raise ValueError(f"{f.label} can't contain line breaks.")
        if len(raw) < f.min_length:
            raise ValueError(f"{f.label} needs at least {f.min_length} characters.")
        if len(raw) > f.max_length:
            raise ValueError(f"{f.label} is too long.")
        where = _store(key, raw)
        log.info("connections: saved %s for %s in the %s", key, cid, where)   # never the value
    LAST_TEST.pop(cid, None)
    _save_tests()
    _refresh()
    return where


def _in_env_file(key: str) -> bool:
    from dotenv import dotenv_values
    path = config.ROOT / ".env"
    return path.exists() and bool(dotenv_values(path).get(key))


def disconnect(cid: str) -> list[str]:
    """Remove the values: from the vault and from .env. Returns any still set as a Windows environment
    variable (outside AIRTAC, so the page tells you where to remove it)."""
    require_unlocked()
    left = []
    for f in REGISTRY[cid].fields:
        if config.SAVED.get(f.key):
            _store(f.key, "")
        if _in_env_file(f.key):
            _write_env(f.key, "")
        if source(f.key) == "env":
            left.append(f.key)
    LAST_TEST.pop(cid, None)
    _save_tests()
    _refresh()
    return left


def run_test(cid: str, client: httpx.Client | None = None) -> dict:
    c = REGISTRY[cid]
    if c.test is None:
        result = {"ok": True, "message": "Saved. There's nothing to call for this one."}
    elif not is_set(c):
        result = {"ok": False, "message": "Fill in the boxes and Save first."}
    elif config.OFFLINE:
        result = {"ok": False, "message": "AIRTAC is in offline mode ([general] offline in config/airtac.toml), so it won't go online."}
    else:
        try:
            ok, message = c.test(client or httpx.Client(timeout=15))
        except httpx.HTTPError as e:
            ok, message = False, f"Couldn't reach the service ({type(e).__name__}). Check your internet."
        result = {"ok": ok, "message": message}
    from airtac import logsafe
    result["message"] = logsafe.redact(result["message"])[:300]   # a service's error text never echoes a key
    LAST_TEST[cid] = {**result, "at": time.strftime("%d %b %H:%M")}
    _save_tests()
    return LAST_TEST[cid]


def call_public(http: httpx.Client, url: str, headers: dict) -> httpx.Response:
    """A custom key's Test: one GET to the address you typed, only if it's on the public internet.
    It runs here, in the core, so the plugin network guard knows it was checked."""
    from airtac import websearch
    try:
        websearch.check_public(url)
    except Exception as e:
        raise httpx.ConnectError(str(e))
    return http.get(url, headers=headers, follow_redirects=False)


def secret_value(cid: str, key: str, why: str) -> str:
    """One saved value, for Show or Copy on this PC: vault unlocked, rate-limited, audit-logged (never the value)."""
    require_unlocked()
    c = REGISTRY[cid]
    if key not in {f.key for f in c.fields}:
        raise ValueError("No such field.")
    value = config.secret(key)
    if not value:
        raise ValueError("Nothing is saved there.")
    store = _vault_store()
    if store is not None:
        store._rate_limit()
        store.audit(f"{why} {key} ({cid})")
    return value


# ---------------------------------------------------------------- what the page sees (never a secret)

def is_set(c: Connector) -> bool:
    return all(config.secret(f.key) for f in c.fields if f.required)


def _shown(f: Field, open_: bool = True) -> str:
    v = config.secret(f.key)
    if not v:
        return ""
    if not f.secret:
        return v if open_ or len(v) < 200 else v[:60] + "…"
    return f"••••{v[-4:]}" if open_ and f.show_end and len(v) > 12 and not f.multiline else "••••••••"


def describe(c: Connector) -> dict:
    _load_tests()
    test = LAST_TEST.get(c.id)
    open_ = unlocked()
    # off -> saved (not tested yet) -> connected (a real call worked) or error (it didn't)
    status = "off" if not is_set(c) else "error" if test and not test["ok"] else "connected" if test else "saved"
    if not c.fields and c.test is None:
        status = "info"           # nothing to fill in here (Google Drive arrives with Google sign-in)
    if c.logo.startswith("/api/"):
        logo = c.logo             # a custom key's uploaded logo
    else:
        logo = f"logos/{c.logo}.svg" if c.logo and (config.WEB_DIR / "logos" / f"{c.logo}.svg").is_file() else ""
    return {"id": c.id, "name": c.name, "about": c.about, "powers": c.powers, "get_key": c.get_key, "group": c.group,
            "logo": logo, "docs": c.docs, "custom": c.custom,
            "note": c.note, "plugin": c.plugin, "status": status, "test": test, "can_test": c.test is not None,
            "fields": [{"key": f.key, "label": f.label, "secret": f.secret, "placeholder": f.placeholder,
                        "set": bool(config.secret(f.key)), "shown": _shown(f, open_), "source": source(f.key),
                        "required": f.required, "multiline": f.multiline} for f in c.fields]}


GROUPS = ("Brains", "Search and dev tools", "Hackathon and cloud", "Services", "Custom", "This PC")


def all_connectors() -> list[dict]:
    _brain_connectors()
    rank = {g: i for i, g in enumerate(GROUPS)}
    return [describe(c) for c in sorted(REGISTRY.values(), key=lambda c: (rank.get(c.group, 9), c.order, c.name))]


def store_status() -> dict:
    ok, detail = _vault().available()
    store = _vault_store()
    return {"vault": ok, "detail": detail if not ok else "Windows Credential Manager" if "Windows" in detail else detail,
            "v2": store is not None, "unlocked": unlocked()}


# ---------------------------------------------------------------- the built-in cards

def _brain_test(name: str):
    def test(http: httpx.Client) -> tuple[bool, str]:
        from airtac import providers
        p, model = providers.PROVIDERS[name], providers.model_for(name)
        body = {"model": model, "messages": [{"role": "user", "content": "Reply with the word OK."}],
                p.token_param: int(16 * p.token_scale), **p.extra}
        r = http.post(f"{p.base_url.rstrip('/')}/chat/completions",
                      headers={"Authorization": f"Bearer {config.secret(p.key_env)}"}, json=body)
        if r.status_code in (401, 403):
            return False, f"{p.label} refused the key. Copy it again from their site."
        if r.status_code in (429, 402):
            return True, f"The key works, but {p.label} is out of free requests right now. It rests and the next brain answers."
        if r.status_code in (400, 404):
            return False, f"The key works, but the model {model} wasn't accepted. Change it under [failover.models]."
        r.raise_for_status()
        return True, f"Connected. {model} answered."
    return test


def _brain_connectors() -> None:
    """One card per provider that needs a key, including ones plugins added."""
    from airtac import providers
    wanted = providers.wanted()
    for i, (name, p) in enumerate(providers.PROVIDERS.items()):
        if not p.key_env or name in REGISTRY:
            continue
        if name not in wanted:
            note = (f'Its free tier may train on what you send, so a saved key is used only after you add "{name}" '
                    "to [failover] order in config/airtac.toml (or pick it in the chat's brain menu).")
        elif p.paid:
            note = "Paid per token. It starts with a daily token limit; change it in the Tokens app."
        else:
            note = ""
        add(Connector(id=name, name=p.label, group="Brains", order=10 + (wanted.index(name) if name in wanted else 20 + i),
                      about=f"A cloud brain, tried before the model on your PC. {p.note}",
                      powers=[f"answers with {providers.model_for(name)}"], get_key=p.get_key, logo=p.logo,
                      fields=[Field(p.key_env, "API key", placeholder="Paste your key")], test=_brain_test(name), note=note))


def _ollama_test(http: httpx.Client) -> tuple[bool, str]:
    from airtac import brains
    try:
        names = brains.installed_models()
    except Exception:
        names = None
    if names is None:
        return False, "Ollama isn't answering. Open it from the Start menu (or run: ollama serve), then Test again."
    want = config.LLM_MODEL
    if not any(n == want or n.split(":")[0] == want.split(":")[0] for n in names):
        return False, f"Ollama is running, but {want} isn't downloaded. Run: ollama pull {want}"
    return True, f"Ollama is running with {len(names)} model{'s' if len(names) != 1 else ''}, including {want}."


add(Connector(id="ollama", name="Ollama on this PC", group="This PC", order=10,
              about="The private brain: it runs on your laptop, needs no key and no internet, and is always the last stop.",
              powers=["answers when every cloud brain is off or resting", "everything, offline"],
              get_key="https://ollama.com/download", fields=[], test=_ollama_test, logo="ollama"))

add(Connector(id="phone", name="Phone and other devices", group="This PC", order=20,
              about="A passcode your phone types once to use AIRTAC over Wi-Fi or Tailscale. With none, only this PC can use it.",
              powers=["AIRTAC on your phone"], get_key="", fields=[Field("AIRTAC_PASSCODE", "Passcode", min_length=8, show_end=False,
                                                                         placeholder="At least 8 characters")],
              note="Also add your laptop's Tailscale name to [server] trusted_hosts (docs/PHONE_AND_PC.md)."))
