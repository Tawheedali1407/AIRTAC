"""Settings > Dictation: switch scripts/dictate.py on or off, pick the hold-to-talk key, and see its state.
Every route is local-only (it starts a program on this PC). The command is fixed in code; nothing from the request
goes into it. When it's on, AIRTAC starts it again each time the server starts (so at login too)."""
import json
import os
import subprocess  # nosec B404 (fixed command)
import sys
import time

from airtac import config
from airtac.plugin_api import local_only, plugin

p = plugin(__name__)

KEYS = ["right ctrl", "right alt", "caps lock", "f8", "f9", "scroll lock", "insert"]
LANGS = {"en": "English", "hi": "Hindi", "te": "Telugu", "ur": "Urdu", "ta": "Tamil"}
MODELS = {"base.en": "English, fast (base.en, about 150 MB)", "base": "Many languages (base, about 150 MB)",
          "small": "Many languages, more accurate, slower (small, about 500 MB)"}


def _data():
    return config.DB_PATH.parent


def settings() -> dict:
    try:
        found = json.loads((_data() / "dictation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    return {"on": bool(found.get("on")), "key": found.get("key") if found.get("key") in KEYS else "right ctrl",
            "language": found.get("language") if found.get("language") in LANGS else "en",
            "model": found.get("model") if found.get("model") in MODELS else "base.en", "clean": found.get("clean", True) is not False}


def save(changes: dict) -> dict:
    s = {**settings(), **{k: v for k, v in changes.items() if v is not None}}
    s = {"on": bool(s["on"]), "key": s["key"] if s["key"] in KEYS else "right ctrl",
         "language": s["language"] if s["language"] in LANGS else "en",
         "model": s["model"] if s["model"] in MODELS else "base.en", "clean": bool(s["clean"])}
    _data().mkdir(parents=True, exist_ok=True)
    (_data() / "dictation.json").write_text(json.dumps(s), encoding="utf-8")
    return s


def state() -> dict:
    try:
        st = json.loads((_data() / "dictation.state").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"running": False, "status": "off"}
    alive = False
    try:
        import psutil
        alive = psutil.pid_exists(int(st.get("pid", 0))) and st.get("status") not in ("stopped", "error")
    except Exception:
        pass
    return {**st, "running": alive}


def start() -> dict:
    if sys.platform != "win32":
        return {"running": False, "status": "error", "error": "Dictation types into Windows apps, so it runs on Windows only."}
    if state()["running"]:
        return state()
    (_data() / "dictation.stop").unlink(missing_ok=True)
    python = os.path.join(sys.prefix, "Scripts", "pythonw.exe")
    if not os.path.exists(python):
        python = sys.executable
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([python, str(config.ROOT / "scripts" / "dictate.py")], cwd=str(config.ROOT),  # noqa: S603  # nosec B603
                     creationflags=flags, close_fds=True)
    for _ in range(20):
        time.sleep(0.25)
        if state()["running"] or state().get("status") == "error":
            break
    return state()


def stop() -> dict:
    (_data() / "dictation.stop").write_text("stop", encoding="utf-8")
    return {"running": False, "status": "stopping"}


@p.startup
def at_start() -> None:
    if settings()["on"] and not config.OFFLINE and sys.platform == "win32":
        try:
            start()
        except Exception:
            pass


@p.routes
def api(router) -> None:
    from fastapi import Request
    from pydantic import BaseModel, Field

    class Change(BaseModel):
        on: bool | None = None
        key: str | None = Field(default=None, max_length=20)
        language: str | None = Field(default=None, max_length=5)
        model: str | None = Field(default=None, max_length=10)
        clean: bool | None = None

    @router.get("")
    def show(request: Request):
        local_only(request)
        return {"settings": settings(), "state": state(), "keys": KEYS, "languages": LANGS, "models": MODELS}

    @router.post("")
    def change(body: Change, request: Request):
        local_only(request)
        before = settings()
        s = save(body.model_dump())
        st = state()
        if s["on"] and (not st["running"] or any(before[k] != s[k] for k in ("key", "language", "model", "clean"))):
            if st["running"]:
                stop()
                time.sleep(1.2)
            st = start()
        elif not s["on"] and st["running"]:
            st = stop()
        return {"settings": s, "state": st, "keys": KEYS, "languages": LANGS, "models": MODELS}
