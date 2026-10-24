"""All settings in one place: config/airtac.toml for choices, .env for secrets.

Other modules read `config.X` (import the module, not the names) so tests can monkeypatch a
setting and every module sees the change.
"""
import json
import os
import re
import tomllib
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Tests set AIRTAC_SKIP_DOTENV so a real .env on disk can never switch a test run online.
if not os.getenv("AIRTAC_SKIP_DOTENV"):
    load_dotenv(ROOT / ".env")

CONFIG_PATH = Path(os.getenv("AIRTAC_CONFIG") or ROOT / "config" / "airtac.toml")


def _load() -> dict:
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


RAW = _load()


def section(name: str) -> dict:
    value = RAW.get(name, {})
    return value if isinstance(value, dict) else {}


SAVED: dict[str, str] = {}   # keys saved on the Connections page (from the vault; see airtac/connections.py)


def secret(name: str) -> str:
    """A key saved on the Connections page, else from .env or the environment. "" when it isn't set."""
    if not name:
        return ""
    return SAVED.get(name) or (os.getenv(name) or "").strip()


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(str(value))          # a TOML basic string is written like a JSON string


def _value_end(line: str, start: int) -> int:
    """Where a TOML value that starts at `start` ends (strings and one-line lists may hold '#')."""
    i = start
    if i < len(line) and line[i] in "\"[":
        close, depth, in_str = {'"': '"', "[": "]"}[line[i]], 0, False
        for j in range(i, len(line)):
            ch = line[j]
            if ch == '"' and (j == 0 or line[j - 1] != "\\"):
                in_str = not in_str
                if close == '"' and not in_str and j > i:
                    return j + 1
            elif not in_str and ch == "[":
                depth += 1
            elif not in_str and ch == "]":
                depth -= 1
                if depth == 0:
                    return j + 1
        return len(line)
    while i < len(line) and line[i] not in " #\t":
        i += 1
    return i


def write_setting(section_name: str, key: str, value) -> None:
    """Change one value in config/airtac.toml from the app, keeping every comment. The old file is copied
    to data/backups/config/ first. Most changes apply after a restart (models apply at once)."""
    import shutil
    import time as _t
    text = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else ""
    if CONFIG_PATH.exists():
        backups = DB_PATH.parent / "backups" / "config"
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CONFIG_PATH, backups / f"airtac-{_t.strftime('%Y%m%d-%H%M%S')}-{_t.time_ns() % 1000000:06d}.toml")
        for old in sorted(backups.glob("airtac-*.toml"))[:-20]:
            old.unlink()
    lines = text.splitlines()
    head = next((i for i, x in enumerate(lines) if x.strip() == f"[{section_name}]"), None)
    new = f"{key} = {_toml(value)}"
    if head is None:
        lines += ["", f"[{section_name}]", new]
    else:
        end = next((i for i in range(head + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        at = next((i for i in range(head + 1, end) if re.match(rf"\s*{re.escape(key)}\s*=", lines[i])), None)
        if at is None:
            lines.insert(head + 1, new)
        else:
            line = lines[at]
            start = line.index("=") + 1
            while start < len(line) and line[start] == " ":
                start += 1
            stop = _value_end(line, start)
            lines[at] = line[:start] + _toml(value) + line[stop:]
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".toml.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tomllib.loads(tmp.read_text(encoding="utf-8"))        # never save a file AIRTAC can't read back
    os.replace(tmp, CONFIG_PATH)
    RAW.setdefault(section_name, {})[key] = value


def plugin_settings(plugin_id: str) -> dict:
    """[plugins.<id>] from airtac.toml."""
    value = section("plugins").get(plugin_id)
    return value if isinstance(value, dict) else {}


def _flag(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


_general, _llm, _voice, _server, _plugins = (section(n) for n in ("general", "llm", "voice", "server", "plugins"))
_brains = section("brains")

# --- You
USER_NAME = _general.get("user_name", "Ali")
LANG = _general.get("language", "en-IN")
# The tests set AIRTAC_OFFLINE: then plugins answer from their sample/ folders (recorded responses) and
# nothing goes online. The app itself never shows sample data: without internet, a plugin says so.
OFFLINE = _flag(os.getenv("AIRTAC_OFFLINE"))

# --- The brain
LLM_PROVIDER = os.getenv("AIRTAC_LLM") or _llm.get("provider", "ollama")   # ollama | nebius | openai | mock
NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
NEBIUS_MODEL = "nvidia/nemotron-3-super-120b-a12b"   # the hackathon brain, in the cloud
LLM_BASE_URL = str(_llm.get("base_url") or (NEBIUS_BASE_URL if LLM_PROVIDER == "nebius" else "http://localhost:11434")).rstrip("/")
LLM_MODEL = _llm.get("model") or (NEBIUS_MODEL if LLM_PROVIDER == "nebius" else "nemotron-3-nano:4b")
LLM_FALLBACK = _llm.get("fallback", "qwen3.5:4b")      # used when the main model isn't downloaded
LLM_KEY_ENV = _llm.get("api_key_env") or ("NEBIUS_API_KEY" if LLM_PROVIDER == "nebius" else "")
LLM_API_KEY = secret(LLM_KEY_ENV)
LLM_THINK = bool(_llm.get("think", False))
LLM_NUM_CTX = int(_llm.get("num_ctx", 4096))
LLM_TIMEOUT = float(_llm.get("timeout", 120))
# The chat path (Ali, 20 Oct: "chat hangs like the Ollama model issue"): every model call from a chat has a deadline
# for its first word and for the whole answer, and Stop cancels it. Ollama unloads the model after 2 idle minutes
# (frees RAM on the 8 GB PC) and gets a smaller context than Work mode, so a 4B model on the i3 starts sooner.
CHAT_FIRST_TOKEN_SECONDS = float(_llm.get("chat_first_token_seconds", 45))
CHAT_TOTAL_SECONDS = float(_llm.get("chat_total_seconds", 90))
CHAT_KEEP_ALIVE = str(_llm.get("chat_keep_alive", "2m"))
CHAT_NUM_CTX = int(_llm.get("chat_num_ctx", 3072))
MAX_TOOLS = int(_llm.get("max_tools", 12))
# One model per kind of question (see airtac/brains.py). Empty = the model above.
BRAINS = {k: str(v) for k, v in _brains.items() if isinstance(v, str)}
# Free cloud tiers tried before the local model (see airtac/providers.py). Used only when their key is set.
FAILOVER = section("failover")

# --- Voice
STT = _voice.get("stt", "browser")          # browser | local
TTS = _voice.get("tts", "browser")          # browser | local
WHISPER_MODEL = _voice.get("whisper_model", "base.en")
PIPER_VOICE = _voice.get("piper_voice", "")

# --- Server
TRUSTED_HOSTS = [h.lower() for h in _server.get("trusted_hosts", ["localhost", "127.0.0.1", "[::1]"])]
PORT = int(_server.get("port", 8765))
# From another device (your phone over Tailscale or Wi-Fi) AIRTAC asks for this passcode once per
# device. It lives in .env, never here. No passcode set = only this computer can use AIRTAC.
PASSCODE = secret("AIRTAC_PASSCODE")
SCHEDULER = bool(_server.get("scheduler", True)) and not os.getenv("AIRTAC_NO_SCHEDULER")

# --- Plugins
PLUGINS_ENABLED = _plugins.get("enabled")            # None = every plugin that can run
PLUGINS_DISABLED = list(_plugins.get("disabled", []))

# --- Paths (your data stays in data/, outputs/ and workspace/, all gitignored)
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "airtac.db"
OUTPUTS_DIR = ROOT / "outputs"
WORKSPACE_DIR = ROOT / "workspace"
PLUGINS_DIR = ROOT / "plugins"
WEB_DIR = ROOT / "web"
FONTS_DIR = ROOT / "assets" / "fonts"
MODELS_DIR = ROOT / "models"
AIRTAC1_URL = str(section("airtac1").get("url", "http://127.0.0.1:8780"))   # AIRTAC 1, the local brain (F:\\Airtac1)
KNOWLEDGE_DIR = ROOT / "knowledge"     # your books and notes for the knowledge library
BACKUP_DIR = ROOT / "backups"
