"""Brain providers and the failover router.

A provider is a place a model can answer from. Built in:

    groq        Groq free tier           GROQ_API_KEY        fast; no training on your data
    nebius      Nebius Token Factory     NEBIUS_API_KEY      the hackathon's Nemotron 3 Super
    nvidia      NVIDIA build (NIM API)   NVIDIA_API_KEY      free credits; meant for prototyping
    openrouter  OpenRouter free models   OPENROUTER_API_KEY  ":free" models; small daily limit
    huggingface Hugging Face router      HF_TOKEN            small monthly free credits, many open models
    cerebras    Cerebras                 CEREBRAS_API_KEY    very fast; free trial credits
    cohere      Cohere                   COHERE_API_KEY      free trial key; may train on your data
    zai         Z.ai (GLM)               ZAI_API_KEY         GLM free "flash" models; may train on your data
    gemini      Google AI Studio         GEMINI_API_KEY      free tier: Google may train on what you send
    mistral     Mistral (La Plateforme)  MISTRAL_API_KEY     free Experiment plan trains on what you send
    deepseek    DeepSeek                 DEEPSEEK_API_KEY    paid, very cheap
    anthropic   Claude (Anthropic API)   ANTHROPIC_API_KEY   paid; through Anthropic's OpenAI-compatible layer
    openai      OpenAI (ChatGPT's API)   OPENAI_API_KEY      paid
    xai         xAI (Grok)               XAI_API_KEY         paid
    together    Together AI              TOGETHER_API_KEY    paid; many open models
    ollama      Ollama on this PC        (no key)            always the last stop

A plugin can add its own provider with `p.brain_provider("name", factory)` (see plugin_api.py), so a new
local engine or service drops into plugins/ without touching this file or the agent.

The failover router (FailoverLLM) tries the tiers in the order set in config/airtac.toml [failover].
A tier is skipped when its key is missing. When a tier hits its limit (HTTP 429, 402 or a quota
message) it rests for a while (Retry-After, or cooldown_seconds) and the next tier answers. Ollama on
this PC is always last, so AIRTAC keeps working with no internet and no credits.

Adding a key is enough: a provider with a key that isn't in [failover] order is tried after the ones
that are (free tiers first, then paid), before Ollama. Two exceptions wait until you add them to the
order yourself, because their free tiers may train on what you send: gemini and mistral.
Paid providers start with a daily token limit (the Tokens app), so a long chat can't run up a bill.

Privacy: every cloud tier sees the question and the context sent with it. With no keys set nothing
leaves the PC.
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from airtac import config

log = logging.getLogger("airtac.providers")


@dataclass
class Provider:
    name: str
    label: str
    base_url: str = ""
    key_env: str = ""             # the secret in .env; "" = needs no key
    model: str = ""               # default model
    note: str = ""
    factory: Callable | None = None  # (model) -> LLM object; None = an OpenAI-compatible server
    trains_on_data: bool = False
    get_key: str = ""             # the page where you make a key
    logo: str = ""                # a file in web/logos/ (without .svg); "" = a letter badge
    paid: bool = False            # costs money per token (gets a daily token limit by default)
    temperature: float | None = 0.3   # None: don't send one (newer Claude models refuse values below 1)
    token_param: str = "max_tokens"   # OpenAI's newer models want "max_completion_tokens"
    token_scale: float = 1.0          # reasoning models spend tokens thinking first, so give them more
    extra: dict = field(default_factory=dict)   # other fields sent with every request


PROVIDERS: dict[str, Provider] = {
    "groq": Provider("groq", "Groq (free tier)", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
                     "openai/gpt-oss-120b", "About 1,000 requests a day free; very fast.",
                     get_key="https://console.groq.com/keys"),
    "nebius": Provider("nebius", "Nebius Token Factory", config.NEBIUS_BASE_URL, "NEBIUS_API_KEY",
                       config.NEBIUS_MODEL, "NVIDIA Nemotron 3 Super; paid credits.",
                       get_key="https://tokenfactory.nebius.com/", paid=True),
    "nvidia": Provider("nvidia", "NVIDIA build (NIM)", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
                       "nvidia/nemotron-3-super-120b-a12b", "Free credits for prototyping.",
                       get_key="https://build.nvidia.com/", logo="nvidia"),
    "openrouter": Provider("openrouter", "OpenRouter (free models)", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                           "nvidia/nemotron-3-super-120b-a12b:free", "Free models end in :free; about 50 a day.",
                           get_key="https://openrouter.ai/settings/keys", logo="openrouter"),
    "huggingface": Provider("huggingface", "Hugging Face (Inference Providers)", "https://router.huggingface.co/v1", "HF_TOKEN",
                            "openai/gpt-oss-120b", "One token, many open models; small monthly free credits.",
                            get_key="https://huggingface.co/settings/tokens", logo="huggingface"),
    "cerebras": Provider("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
                         "gpt-oss-120b", "Very fast open models; free trial credits (needs a card to start).",
                         get_key="https://cloud.cerebras.ai/"),
    "cohere": Provider("cohere", "Cohere", "https://api.cohere.ai/compatibility/v1", "COHERE_API_KEY",
                       "command-a-03-2025", "Free trial key: about 1,000 calls a month. May train on what you send.",
                       trains_on_data=True, get_key="https://dashboard.cohere.com/api-keys", logo="cohere"),
    "zai": Provider("zai", "Z.ai (GLM)", "https://api.z.ai/api/paas/v4", "ZAI_API_KEY",
                    "glm-4.7-flash", "GLM \"flash\" models have a free tier. May train on what you send.",
                    trains_on_data=True, get_key="https://z.ai/manage-apikey/apikey-list"),
    "gemini": Provider("gemini", "Google AI Studio (free tier)", "https://generativelanguage.googleapis.com/v1beta/openai",
                       "GEMINI_API_KEY", "gemini-3.8-flash",   # 2.5 models are closed to new keys since 18 Sep 2026
                       "Free tier may be used to train Google's models and read by reviewers.", trains_on_data=True,
                       get_key="https://aistudio.google.com/apikey", logo="googlegemini",
                       # Gemini 3 always thinks; "low" (ai.google.dev/gemini-api/docs/openai) keeps chat answers quick.
                       extra={"reasoning_effort": "low"}),
    "mistral": Provider("mistral", "Mistral (La Plateforme)", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
                        "mistral-small-latest", "Free Experiment plan (phone check); it may train on what you send.",
                        trains_on_data=True, get_key="https://console.mistral.ai/api-keys", logo="mistralai"),
    "deepseek": Provider("deepseek", "DeepSeek", "https://api.deepseek.com", "DEEPSEEK_API_KEY",
                         "deepseek-flash", "Paid, very cheap per token; strong at maths and code.",
                         get_key="https://platform.deepseek.com/api_keys", logo="deepseek", paid=True),
    "anthropic": Provider("anthropic", "Claude (Anthropic API)", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY",
                          "claude-haiku-5-5", "Paid. Uses Anthropic's OpenAI-compatible layer (meant for trying models).",
                          get_key="https://platform.claude.com/settings/keys", logo="anthropic", paid=True, temperature=None),
    "openai": Provider("openai", "OpenAI (ChatGPT's API)", "https://api.openai.com/v1", "OPENAI_API_KEY",
                       "gpt-5-mini", "Paid. Separate from a ChatGPT Plus subscription.",
                       get_key="https://platform.openai.com/api-keys", paid=True, temperature=None,
                       token_param="max_completion_tokens", token_scale=3.0, extra={"reasoning_effort": "low"}),
    "xai": Provider("xai", "xAI (Grok)", "https://api.x.ai/v1", "XAI_API_KEY", "grok-4.7", "Paid.",
                    get_key="https://console.x.ai/", paid=True),
    "together": Provider("together", "Together AI", "https://api.together.ai/v1", "TOGETHER_API_KEY",
                         "openai/gpt-oss-120b", "Paid per token; many open models (docs.together.ai).",
                         get_key="https://api.together.ai/", paid=True),
    # AIRTAC 1: the local brain from F:\Airtac1 (OpenAI-compatible, on this PC). Not a failover tier: only when picked.
    "airtac1": Provider("airtac1", "AIRTAC 1", config.AIRTAC1_URL.rstrip("/") + "/v1", "", "auto",
                        "AIRTAC 1 on this PC: your own local brain (F:\\Airtac1)."),
}
_lock = threading.Lock()


def register(provider: Provider) -> None:
    """Add a provider (plugins call this through p.brain_provider)."""
    with _lock:
        PROVIDERS[provider.name] = provider


def model_for(name: str) -> str:
    return str(config.FAILOVER.get("models", {}).get(name) or PROVIDERS[name].model)


def p_key(name: str) -> bool:
    """Has its own key set (a provider that needs none doesn't count here)."""
    p = PROVIDERS.get(name)
    return bool(p and p.key_env and config.secret(p.key_env))


def has_key(name: str) -> bool:
    p = PROVIDERS.get(name)
    return bool(p) and (not p.key_env or bool(config.secret(p.key_env)))


def build(name: str, model: str = ""):
    """The LLM object for one provider, with its model (or the one given)."""
    from airtac.llm import OpenAICompatLLM
    p = PROVIDERS[name]
    model = model or model_for(name)
    if p.factory:
        return p.factory(model)
    # No waiting on a 429 here: the router moves to the next tier instead.
    return OpenAICompatLLM(base_url=p.base_url, model=model, api_key=config.secret(p.key_env) if p.key_env else "",
                           max_429_retries=0, timeout=float(config.FAILOVER.get("timeout", 45)), tier=name,
                           temperature=p.temperature, token_param=p.token_param, token_scale=p.token_scale, extra=p.extra)


# ---------------------------------------------------------------- which tiers are resting, and why

@dataclass
class TierState:
    resting_until: float = 0.0
    last_error: str = ""
    answered: int = 0
    failed: int = 0


STATE: dict[str, TierState] = {}
ASKED: dict = {}  # what the chat's brain menu asked for this message: {"provider": "groq", "model": "..."}
LAST: dict = {}   # who answered last: {"tier": "groq", "model": "..."} (shown under the answer)
TURN: dict = {}   # tokens used while answering the current message: {"prompt": n, "completion": n}


def state(name: str) -> TierState:
    return STATE.setdefault(name, TierState())


def rest(name: str, seconds: float, why: str) -> None:
    s = state(name)
    s.resting_until = time.time() + max(5.0, seconds)
    s.last_error, s.failed = why, s.failed + 1
    log.warning("brain tier %s resting %.0f s: %s", name, seconds, why)


DEFAULT_ORDER = ["groq", "nebius", "nvidia", "openrouter"]


def wanted() -> list[str]:
    """Every cloud tier AIRTAC may use, in order: the ones in [failover] order first, then any other
    provider you added a key for (free before paid). Providers whose free tier may train on your data
    (gemini, mistral) join only when you list them in [failover] order yourself."""
    listed = [n for n in config.FAILOVER.get("order", DEFAULT_ORDER) if n in PROVIDERS and n != "ollama"]
    skip = set(config.FAILOVER.get("skip", []))
    extra = [n for n, p in PROVIDERS.items() if n not in listed and n not in skip and p.key_env and not p.trains_on_data]
    return listed + sorted(extra, key=lambda n: PROVIDERS[n].paid)


def order() -> list[str]:
    """Cloud tiers to try, in order, that have a key. Empty when offline or turned off."""
    if config.OFFLINE or not config.FAILOVER.get("enabled", True):
        return []
    return [n for n in wanted() if has_key(n)]


def status() -> list[dict]:
    now = time.time()
    rows = []
    listed = wanted()
    configured = [n for n in config.FAILOVER.get("order", DEFAULT_ORDER) if n in PROVIDERS]
    for n in configured + [n for n in PROVIDERS if n not in configured and p_key(n)]:
        if n not in PROVIDERS:
            continue
        p, s = PROVIDERS[n], state(n)
        rows.append({"tier": n, "label": p.label, "model": model_for(n), "key": has_key(n), "key_env": p.key_env,
                     "resting_seconds": max(0, round(s.resting_until - now)), "last_error": s.last_error,
                     "answered": s.answered, "failed": s.failed, "note": p.note, "trains_on_data": p.trains_on_data,
                     "in_order": n in listed, "paid": p.paid, "logo": p.logo})
    rows.append({"tier": "ollama", "label": "Ollama on this PC (last stop)", "model": config.LLM_MODEL, "key": True,
                 "key_env": "", "resting_seconds": 0, "last_error": "", "answered": state("ollama").answered,
                 "failed": state("ollama").failed, "note": "Private: never leaves the PC.", "trains_on_data": False})
    return rows


# ---------------------------------------------------------------- the router

def alternates(name: str, failed: str, limit: int = 2) -> list[str]:
    """Other chat models this provider offers to your key (its saved model list; no network call here),
    lighter ones first: names with "lite", then "flash"/"mini"/"small", then the rest."""
    import json
    from airtac import memory
    try:
        ids = json.loads(memory.kv_get("brain_models", name) or "{}").get("ids", [])
    except ValueError:
        ids = []
    if not ids and PROVIDERS.get(name) and PROVIDERS[name].model:
        ids = [PROVIDERS[name].model]
    rank = lambda m: (0 if "lite" in m else 1 if any(w in m for w in ("flash", "mini", "small")) else 2)  # noqa: E731
    return sorted((m for m in ids if m != failed and "preview" not in m and "exp" not in m), key=rank)[:limit]


def all_failed_text(tiers: list[str], now: float | None = None) -> str:
    """One short answer when no brain could reply: the PC model's reason, then the cloud brains that have a key
    (busy, and when they're tried again), or that none has a key. Never a key or a raw error dump."""
    now = now or time.time()
    parts = [state("ollama").last_error or "Your PC's model couldn't answer."]
    busy = []
    for name in tiers:
        p = PROVIDERS.get(name)
        if p is None or not has_key(name):
            continue
        wait = round(state(name).resting_until - now)
        busy.append(f"{p.label} is busy" + (f" (tried again in {max(1, wait // 60)} min)" if wait > 0 else ""))
    if busy:
        parts.append("; ".join(busy) + ".")
    else:
        parts.append("No cloud brain has a key: add a free one (Groq or OpenRouter) on APIs and keys as a backup.")
    return " ".join(parts)


@dataclass
class FailoverLLM:
    """Looks like one LLM to the agent; behind it, the tiers in order and the local model last."""
    local: object                      # the role's Ollama model (or whatever the provider setting gives)
    tiers: list[str] = field(default_factory=order)
    models: dict = field(default_factory=dict)   # per-tier model for this role, e.g. {"nebius": "..."}
    model: str = ""

    def __post_init__(self):
        self._clients: dict[str, object] = {}
        self.model = getattr(self.local, "model", "")

    def _client(self, name: str):
        if name not in self._clients:
            self._clients[name] = build(name, self.models.get(name, ""))
        return self._clients[name]

    def _try_local(self, messages, tools, max_tokens):
        """[failover] local_first = true: your PC's model answers first; the cloud tiers only when it can't
        (Ollama off, or too little free memory to load the model). A failure rests it for 2 minutes, so the
        next questions don't each wait for it to fail again."""
        from airtac.llm import LLMUnavailable
        if state("ollama").resting_until > time.time():
            return None
        try:
            reply = self.local.chat(messages, tools, max_tokens)
        except LLMUnavailable as e:
            rest("ollama", 120, str(e))
            return None
        state("ollama").answered += 1
        LAST.update(tier="ollama", model=getattr(self.local, "model", config.LLM_MODEL))
        return reply

    def _try_alternates(self, name, messages, tools, max_tokens):
        """One model of a provider is overloaded (Gemini's 503, Ali 18 Oct): try up to 2 other models of the same
        provider from its own model list (lighter "lite"/"flash" ones first) before giving up on that provider."""
        from airtac.llm import LLMUnavailable
        for model in alternates(name, self.models.get(name) or model_for(name)):
            try:
                reply = build(name, model).chat(messages, tools, max_tokens)
            except LLMUnavailable:
                continue
            log.warning("brain tier %s: %s was busy, %s answered", name, self.models.get(name) or model_for(name), model)
            state(name).answered += 1
            LAST.update(tier=name, model=model)
            return reply
        return None

    def chat(self, messages: list[dict], tools: list[dict], max_tokens: int = 600):
        from airtac.llm import LLMUnavailable, QuotaExceeded
        cooldown = float(config.FAILOVER.get("cooldown_seconds", 600))
        from airtac import plugins
        local_first = bool(config.FAILOVER.get("local_first")) and not ASKED.get("provider")   # a pick in the menu goes first
        if local_first:
            reply = self._try_local(messages, tools, max_tokens)
            if reply is not None:
                return reply
        for name in self.tiers:
            if state(name).resting_until > time.time():
                continue
            if plugins.brain_gate(name, max_tokens) is None:   # e.g. today's token limit for it is used up
                continue
            try:
                reply = self._client(name).chat(messages, tools, max_tokens)
            except QuotaExceeded as e:          # 429, 402, "quota", "rate limit": out of free requests
                rest(name, e.retry_after or cooldown, str(e))
                continue
            except LLMUnavailable as e:         # down, slow, a bad key, a 5xx: try again in a few minutes
                busy = "503" in str(e) or "overload" in str(e).lower() or "unavailable" in str(e).lower()
                alt = self._try_alternates(name, messages, tools, max_tokens) if busy else None
                if alt is not None:
                    return alt
                rest(name, 900 if "key" in str(e).lower() else 120, str(e))
                continue
            state(name).answered += 1
            LAST.update(tier=name, model=self.models.get(name) or model_for(name))
            return reply
        if local_first:   # it already failed: say why, for the PC's model and for the cloud brains
            raise LLMUnavailable(all_failed_text(self.tiers))
        reply = self.local.chat(messages, tools, max_tokens)   # the last stop: raises if Ollama is off too
        state("ollama").answered += 1
        LAST.update(tier="ollama", model=getattr(self.local, "model", config.LLM_MODEL))
        return reply

    def status(self) -> dict:
        return self.local.status() if hasattr(self.local, "status") else {"ready": True, "reason": ""}


# ---------------------------------------------------------------- the model lists in the chat's brain menu

SKIP_WORDS = ("embed", "whisper", "tts", "dall-e", "image", "audio", "moderation", "guard", "rerank", "transcribe",
              "realtime", "speech", "vision-preview", "imagen", "veo", "lyria")
LIST_HOURS = 24
MODEL_RE = r"[A-Za-z0-9._/:@+-]{1,120}"


def live_models(name: str, refresh: bool = False) -> list[str]:
    """The chat models a provider offers to your key (its /models list), kept for a day.
    Only names: the key stays on this PC. Offline or no key: the last list, or []."""
    import json
    import re
    import httpx
    from airtac import memory
    try:
        cached = json.loads(memory.kv_get("brain_models", name) or "{}")
    except ValueError:
        cached = {}
    fresh = time.time() - cached.get("at", 0) < LIST_HOURS * 3600
    p = PROVIDERS.get(name)
    if (fresh and not refresh) or p is None or not p.base_url or config.OFFLINE or not has_key(name):
        return list(cached.get("ids", []))
    try:
        r = httpx.get(f"{p.base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {config.secret(p.key_env)}"},
                      timeout=10)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("couldn't list %s models: %s", name, type(e).__name__)
        return list(cached.get("ids", []))
    rows = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    ids = []
    for row in rows:
        mid = str((row or {}).get("id", "")).removeprefix("models/")
        if not re.fullmatch(MODEL_RE, mid) or any(w in mid.lower() for w in SKIP_WORDS):
            continue
        if name == "openrouter" and not mid.endswith(":free"):
            continue   # the menu's OpenRouter group is its free models
        ids.append(mid)
    ids = sorted(set(ids))[:80]
    memory.kv_set("brain_models", name, json.dumps({"at": time.time(), "ids": ids}))
    return ids


def cached_models(name: str) -> list[str]:
    """The provider's saved model list, with no network call (the menu must answer fast: Ali, 20 Oct, an empty picker
    was the menu's GET waiting up to 10 s per provider while the page gave up after 10 s)."""
    import json
    from airtac import memory
    try:
        return list(json.loads(memory.kv_get("brain_models", name) or "{}").get("ids", []))
    except ValueError:
        return []


def _stale(name: str) -> bool:
    import json
    from airtac import memory
    try:
        at = json.loads(memory.kv_get("brain_models", name) or "{}").get("at", 0)
    except ValueError:
        at = 0
    return time.time() - at >= LIST_HOURS * 3600


_refreshing = threading.Lock()


def refresh_lists_soon() -> None:
    """Fetch stale model lists in the background (one thread at a time); the next menu shows them."""
    names = [n for n, p in PROVIDERS.items() if p.key_env and p.base_url and has_key(n) and _stale(n)]
    if config.OFFLINE or not names or _refreshing.locked():
        return

    def work():
        with _refreshing:
            for n in names:
                live_models(n, refresh=True)
    threading.Thread(target=work, daemon=True, name="airtac-model-lists").start()


def known_model(name: str, model: str) -> bool:
    """A model the menu may ask for: the default, the one in config, or one from the provider's own list."""
    return bool(model) and (model in (PROVIDERS[name].model, model_for(name)) or model in live_models(name))


MENU_GROUPS = [("gemini", "Google (Gemini / AI Studio)"), ("groq", "Groq"), ("xai", "xAI Grok"),
               ("openrouter", "OpenRouter free models"), ("nebius", "Nebius"), ("huggingface", "Hugging Face Inference")]


def airtac1_up(timeout: float = 1.0) -> bool:
    """Is AIRTAC 1 answering on this PC? (GET /brain/health)"""
    import httpx
    try:
        return httpx.get(config.AIRTAC1_URL.rstrip("/") + "/brain/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def airtac1_group() -> dict:
    group = {"id": "airtac1", "label": "AIRTAC 1", "key": "none", "note": "", "items": []}
    if not airtac1_up():
        group["note"] = "AIRTAC 1 isn't running. Start it with start_airtac1.bat in F:\\Airtac1."
        return group
    ids = live_models("airtac1", refresh=True) or ["auto"]
    group["items"] = [{"id": f"airtac1:{m}", "label": "Automatic (chat or coder)" if m == "auto" else m} for m in ids]
    return group


NO_LOCAL = "no models found — check Ollama"


def local_group() -> dict:
    """Local (Ollama): every chat model `ollama list` shows. Empty (Ollama answers with no models) and unreachable
    both say "no models found — check Ollama" with the reason, never a blank group or a model that isn't there."""
    from airtac import brains
    names = brains.installed_models()
    items = [{"id": f"ollama:{n}", "label": n} for n in (names or []) if not any(w in n for w in ("embed", "nomic"))]
    note = ""
    if names is None:
        note = f"{NO_LOCAL} (it isn't answering at {config.LLM_BASE_URL}: open Ollama, then press Update the model lists)"
    elif not items:
        note = f"{NO_LOCAL} (Ollama has no chat model: run  ollama pull {config.LLM_MODEL})"
    return {"id": "local", "label": "Local (Ollama on this PC)", "key": "none", "note": note, "items": items,
            "reachable": names is not None}


def menu() -> list[dict]:
    """The groups of the chat's brain menu, never a key: Local, AIRTAC 1, the main clouds, then any other with a key.
    No slow calls here: model lists come from the saved copy (refreshed in the background when a day old)."""
    refresh_lists_soon()
    groups = [local_group(), airtac1_group()]
    shown = set()
    for name, label in MENU_GROUPS + [(n, PROVIDERS[n].label) for n in PROVIDERS if n not in dict(MENU_GROUPS)]:
        p = PROVIDERS.get(name)
        if p is None or not p.key_env or name in shown:
            continue
        if name not in dict(MENU_GROUPS) and not has_key(name):
            continue   # the others appear once they have a key
        shown.add(name)
        ids = [model_for(name)] + [m for m in cached_models(name) if m != model_for(name)]
        groups.append({"id": name, "label": label, "key": "set" if has_key(name) else "needed", "key_env": p.key_env,
                       "get_key": p.get_key, "paid": p.paid, "trains_on_data": p.trains_on_data,
                       "logo": f"logos/{p.logo}.svg" if p.logo and (config.WEB_DIR / "logos" / f"{p.logo}.svg").is_file() else "",
                       "items": [{"id": name if i == 0 else f"{name}:{m}", "label": m} for i, m in enumerate(ids)]})
    return groups
