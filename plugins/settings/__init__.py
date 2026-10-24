"""Settings: the choices on the Settings page, kept in data/settings.json (gitignored).

- theme: crimson (dark, the default), day (light), contrast (high contrast), arc, gilded, aurora, forest, sunset,
  ocean (light), midnight, sakura (light).
  The page also keeps it in the browser, so it shows at once before the server answers.
- social_browser: "default" (your usual browser) or "chrome" (AIRTAC's Chrome profile) for the Social page.
  AI apps always open in Chrome when it's installed.
- session_token_limit: the chat header warns when this session's tokens pass it (0 = no warning).
- brain: the brain the chat asks first ("" = automatic, the failover order).
- search_engine: the engine "Search on ..." opens in your browser (Google, Bing, DuckDuckGo, Brave, Perplexity).

No keys and no passwords here: keys live on the Connections page (Windows Credential Manager),
logins in the Passwords vault.
"""
import json
import threading

from airtac import config
from airtac.plugin_api import plugin

p = plugin(__name__)
THEMES = ("crimson", "day", "contrast", "arc", "gilded", "aurora", "forest", "sunset", "ocean", "midnight", "sakura")
ENGINES = ("google", "bing", "ddg", "brave", "perplexity")
DEFAULTS = {"theme": "crimson", "social_browser": "default", "session_token_limit": 20000, "brain": "", "search_engine": "google",
            "tone": "friendly"}
_lock = threading.Lock()


def path():
    return config.DB_PATH.parent / "settings.json"


def load() -> dict:
    try:
        saved = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {k: saved.get(k, v) if isinstance(saved, dict) else v for k, v in DEFAULTS.items()}


def get(key: str):
    return load()[key]


def save(changes: dict) -> dict:
    """Check and store some settings. Raises ValueError with a sentence for the page."""
    current = load()
    for key, value in changes.items():
        if value is None:
            continue
        if key == "theme" and value not in THEMES:
            raise ValueError("Pick one of the themes on the page.")
        if key == "social_browser" and value not in ("default", "chrome"):
            raise ValueError("Social sites open in your default browser or in Chrome.")
        if key == "session_token_limit":
            value = int(value)
            if not 0 <= value <= 10_000_000:
                raise ValueError("The token warning is a number of tokens (0 turns it off).")
        if key == "search_engine" and value not in ENGINES:
            raise ValueError("Pick Google, Bing, DuckDuckGo, Brave or Perplexity.")
        if key == "brain":
            from airtac import providers
            if value and value.split(":")[0] not in (*providers.PROVIDERS, "ollama"):
                raise ValueError("That brain isn't known.")
        if key == "tone" and value not in ("friendly", "neutral"):
            raise ValueError("The tone is friendly or neutral.")
        if key in DEFAULTS:
            current[key] = value
    with _lock:
        path().parent.mkdir(parents=True, exist_ok=True)
        path().write_text(json.dumps(current, indent=1), encoding="utf-8")
    return current


@p.routes
def api(router) -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel, Field

    class Changes(BaseModel):
        theme: str | None = Field(default=None, max_length=20)
        social_browser: str | None = Field(default=None, max_length=10)
        session_token_limit: int | None = None
        brain: str | None = Field(default=None, max_length=160)
        tone: str | None = Field(default=None, max_length=10)
        search_engine: str | None = Field(default=None, max_length=12)

    @router.get("")
    def read():
        return {**load(), "themes": THEMES}

    @router.post("")
    def write(body: Changes):
        try:
            return {**save(body.model_dump()), "themes": THEMES}
        except ValueError as e:
            raise HTTPException(400, str(e))
