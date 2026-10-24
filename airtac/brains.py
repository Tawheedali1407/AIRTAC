"""Brains: which free, open model answers which kind of question.

AIRTAC Advance can use several open models through Ollama, each for what it does best:

    general    everyday questions and tools            NVIDIA Nemotron 3 Nano 4B (NVIDIA Open Model License)
    coding     code, algorithms, debugging             same model with a coding prompt, or a coder model
    reasoning  engineering, maths, physics             same model with thinking switched on
    medical    medicine and health questions           a medical model if you pull one (answers only, no tools)
    embed      turns text into numbers for search      nomic-embed-text (Apache-2.0)

Set them in config/airtac.toml under [brains]; an empty role uses the general model.
An 8 GB laptop holds one model at a time: Ollama swaps them, so the first question after a
switch takes a few extra seconds. route() picks the role from the words in a question (and
the app you're in), so you never have to choose by hand.
"""
import logging
import re

import httpx

from airtac import config

log = logging.getLogger("airtac.brains")

CODING_PROMPT = (
    "You are also a careful programming mentor. For code questions: explain the idea in one or two sentences, "
    "then give working code, then the time and space complexity. Prefer simple, readable code. If you're not sure "
    "an API exists, say so instead of guessing.")
REASONING_PROMPT = (
    "You are also an engineering and maths tutor. Work step by step, keep units in every line, and check the "
    "answer against the question at the end. If a value is missing, say what you assumed.")
MEDICAL_PROMPT = (
    "You give general medical information for learning, not a diagnosis or treatment plan. Explain clearly and "
    "mention when someone should see a doctor. If the question describes an emergency (chest pain, trouble "
    "breathing, heavy bleeding, signs of stroke, poisoning, thoughts of self-harm), say first to call 112 (India's "
    "emergency number) or go to the nearest hospital. Never give doses for a specific person. Say when the "
    "evidence is uncertain.")

ROLES = {
    "general": {"label": "General", "think": None, "tools": True, "prompt": ""},
    "coding": {"label": "Coding", "think": None, "tools": True, "prompt": CODING_PROMPT},
    "reasoning": {"label": "Engineering and maths", "think": True, "tools": True, "prompt": REASONING_PROMPT},
    "medical": {"label": "Medicine", "think": False, "tools": False, "prompt": MEDICAL_PROMPT},
}

# Words that point a question at a role. Whole words; a few phrases.
KEYWORDS = {
    "coding": ("code", "coding", "python", "java", "javascript", "typescript", "c++", "cpp", "golang", "rust",
               "kotlin", "sql", "html", "css", "react", "function", "bug", "debug", "error", "exception", "compile",
               "compiler", "algorithm", "complexity", "recursion", "array", "linked list", "binary tree", "dp",
               "dynamic programming", "leetcode", "codechef", "regex", "api", "git", "github",
               "pointer", "stack trace", "refactor", "unit test", "pytest", "fastapi", "django", "flask", "docker"),
    "medical": ("symptom", "symptoms", "fever", "cough", "medicine", "medicines", "tablet", "tablets", "dose",
                "dosage", "disease", "diagnosis", "pain", "headache", "blood pressure", "bp", "diabetes", "sugar level",
                "infection", "virus", "bacteria", "antibiotic", "doctor", "hospital", "anatomy", "physiology",
                "pharmacology", "pathology", "mbbs", "nursing", "vaccine", "allergy", "injury", "fracture",
                "pregnancy", "heart rate", "cholesterol", "vitamin", "side effects", "first aid"),
    "reasoning": ("prove", "proof", "derive", "derivation", "calculate", "equation", "integral", "integrate",
                  "derivative", "differentiate", "matrix", "eigenvalue", "circuit", "voltage", "resistor",
                  "capacitor", "transistor", "thermodynamics", "entropy", "strain", "beam", "torque",
                  "velocity", "acceleration", "physics", "chemistry", "engineering", "probability", "statistics",
                  "laplace", "fourier", "signal", "signals", "control system", "fluid", "heat transfer",
                  "mechanics", "solve for", "simplify"),
}
APP_ROLES = {"practice": "coding", "leetcode": "coding", "github": "coding", "knowledge": "general"}


def route(message: str, app_id: str | None = None) -> str:
    """The role for a question: the app you're in decides first, then the most matching words."""
    if app_id in APP_ROLES:
        return APP_ROLES[app_id]
    low = " " + re.sub(r"[^a-z0-9+#' ]+", " ", (message or "").lower()) + " "
    scores = {role: sum(1 for w in words if f" {w} " in low) for role, words in KEYWORDS.items()}
    # Medicine wins ties: a health question shouldn't get an engineering answer.
    best = max(scores, key=lambda r: (scores[r], r == "medical"))
    return best if scores[best] > 0 else "general"


# ---------------------------------------------------------------- which model plays each role

def model_for(role: str) -> str:
    """The model set for a role, or the general model."""
    chosen = (config.BRAINS.get(role) or "").strip() if role != "general" else ""
    return chosen or config.LLM_MODEL


def embed_model() -> str:
    return (config.BRAINS.get("embed") or "").strip()


_cache: dict[tuple, object] = {}


def get(role: str = "general", prefer: str = ""):
    """An LLM object for the role. With free cloud tiers set up ([failover] and their keys), a
    FailoverLLM that tries them in order and keeps this role's local model as the last stop.
    A role set to "nebius:<model>" puts that cloud model first for that role only.
    prefer: a provider picked in the chat's brain menu goes first (if it has a key); the rest stay behind it."""
    from airtac import providers
    if config.LLM_PROVIDER == "mock":
        from airtac.llm import MockLLM
        return MockLLM()
    role = role if role in ROLES else "general"
    model, think = model_for(role), ROLES[role]["think"]
    tiers, models = providers.order(), {}
    want, _, want_model = (prefer or "").partition(":")
    providers.ASKED.clear()
    if want == "ollama" and want_model and config.LLM_PROVIDER == "ollama":
        # A model on this PC, picked in the menu: only that one. If Ollama's list can't be read right now (None), trust
        # the pick: the call itself then says clearly what's wrong, instead of another model answering silently.
        if _has(installed_models(), want_model) is not False:
            providers.ASKED.update(provider="ollama", model=want_model)
            return _local(want_model, think)
    elif want and want in providers.PROVIDERS and providers.has_key(want) and not config.OFFLINE:
        tiers = [want] + [t for t in tiers if t != want]
        providers.ASKED.update(provider=want, model=providers.model_for(want))
        if want_model and providers.known_model(want, want_model):
            models[want] = want_model
            providers.ASKED["model"] = want_model
    if model.startswith("nebius:"):
        if providers.has_key("nebius") and not config.OFFLINE:
            tiers = ["nebius"] + [t for t in tiers if t != "nebius"]
            models["nebius"] = model[len("nebius:"):]
        model = config.LLM_MODEL   # the local last stop for this role
    local = _local(model, think)
    if not tiers or config.LLM_PROVIDER != "ollama":
        return local
    key = ("failover", tuple(tiers), tuple(sorted(models.items())), id(local))
    if key not in _cache:
        _cache[key] = providers.FailoverLLM(local=local, tiers=tiers, models=models)
    return _cache[key]


def _local(model: str, think):
    """One shared instance per model and setting."""
    from airtac.llm import OllamaLLM, OpenAICompatLLM  # imported here: llm imports this module
    key = (config.LLM_PROVIDER, model, think)
    if key not in _cache:
        if config.LLM_PROVIDER == "ollama":
            _cache[key] = OllamaLLM(model=model, think=think)
        else:  # nebius or any OpenAI-compatible server
            _cache[key] = OpenAICompatLLM(model=model)
    return _cache[key]


def prompt_for(role: str) -> str:
    return ROLES.get(role, ROLES["general"])["prompt"]


def uses_tools(role: str) -> bool:
    return ROLES.get(role, ROLES["general"])["tools"]


# ---------------------------------------------------------------- what's installed (for the Brains page)

def installed_models() -> list[str] | None:
    """Model names Ollama has downloaded, or None when Ollama isn't reachable (or isn't the provider)."""
    if config.LLM_PROVIDER != "ollama":
        return None
    try:
        # 4 s: on the i3 /api/tags can be slow while a model loads; a 2 s cut-off made the Local group look empty.
        r = httpx.get(f"{config.LLM_BASE_URL}/api/tags", timeout=4)
        return [m.get("name", "") for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return None


def _has(names: list[str] | None, model: str) -> bool | None:
    if names is None:
        return None
    full = model if ":" in model.split("/")[-1] else model + ":latest"
    return model in names or full in names


def models() -> dict:
    """For the Brains page: each downloaded model with its size, the memory it roughly needs (its file
    plus about 20% and the context), and the roles that use it; plus this PC's memory."""
    rows, names = [], None
    if config.LLM_PROVIDER == "ollama":
        try:
            r = httpx.get(f"{config.LLM_BASE_URL}/api/tags", timeout=3)
            rows = r.json().get("models", [])
            names = [m.get("name", "") for m in rows]
        except (httpx.HTTPError, ValueError):
            rows = []
    using = {}
    for role in [*ROLES, "embed"]:
        model = embed_model() if role == "embed" else model_for(role)
        full = model if ":" in model.split("/")[-1] else model + ":latest"
        using.setdefault(full, []).append(role)
    try:
        import psutil
        ram = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        ram = None
    out = []
    for m in rows:
        size = m.get("size", 0) / 1e9
        out.append({"name": m.get("name", ""), "size_gb": round(size, 2), "ram_gb": round(size * 1.2 + 0.5, 1),
                    "family": (m.get("details") or {}).get("family", ""), "params": (m.get("details") or {}).get("parameter_size", ""),
                    "roles": using.get(m.get("name", ""), []), "fits": ram is None or size * 1.2 + 0.5 < ram * 0.75})
    return {"ollama_up": names is not None, "ram_gb": ram, "models": out,
            "roles": [{"role": r, "label": ROLES[r]["label"] if r in ROLES else "Knowledge search",
                       "model": embed_model() if r == "embed" else model_for(r)} for r in [*ROLES, "embed"]]}


def set_model(role: str, model: str) -> None:
    """Pick a role's model ("" = the general model). Saved to config/airtac.toml, applied at once."""
    if role not in ROLES and role != "embed":
        raise ValueError("Unknown role.")
    if role == "general" and not model:
        raise ValueError("The general model can't be empty.")
    names = installed_models()
    if model and names is not None and not _has(names, model) and not model.startswith("nebius:"):
        raise ValueError(f"{model} isn't downloaded. Run: ollama pull {model}")
    if role == "general":
        config.write_setting("llm", "model", model)
        config.LLM_MODEL = model
    else:
        config.write_setting("brains", role, model)
        config.BRAINS[role] = model
    _cache.clear()


def status() -> dict:
    names = installed_models()
    roles = []
    for role, spec in ROLES.items():
        model = model_for(role)
        roles.append({"role": role, "label": spec["label"], "model": model, "own_model": model != config.LLM_MODEL,
                      "tools": spec["tools"],
                      "installed": _has(names, model) if config.LLM_PROVIDER == "ollama" and not model.startswith("nebius:") else None})
    embed = embed_model()
    roles.append({"role": "embed", "label": "Knowledge search", "model": embed or "(off: keyword search)",
                  "own_model": bool(embed), "tools": False,
                  "installed": _has(names, embed) if embed and config.LLM_PROVIDER == "ollama" else None})
    from airtac import providers
    return {"provider": config.LLM_PROVIDER, "ollama_up": names is not None if config.LLM_PROVIDER == "ollama" else None,
            "roles": roles, "catalog": CATALOG, "tiers": providers.status()}


# Free models that run on an 8 GB laptop, for the Brains page and docs/BRAINS.md.
# Check each model's card before relying on it; licences can change between versions.
CATALOG = [
    {"model": "nemotron-3-nano:4b", "role": "general", "size": "2.8 GB", "licence": "NVIDIA Open Model License (free, open weights)",
     "note": "Default. NVIDIA's small model for tools and reasoning; thinking can be switched on or off."},
    {"model": "qwen3.5:4b", "role": "general", "size": "3.4 GB", "licence": "Apache-2.0",
     "note": "The backup brain (used if Nemotron isn't downloaded). Also reads images."},
    {"model": "qwen3.5:2b", "role": "general", "size": "2.7 GB", "licence": "Apache-2.0",
     "note": "Faster on an i3, a little less clever."},
    {"model": "qwen2.5-coder:7b", "role": "coding", "size": "4.7 GB", "licence": "Apache-2.0",
     "note": "Stronger at code; slow on an i3 (a few words a second)."},
    {"model": "qwen2.5-coder:1.5b", "role": "coding", "size": "1.0 GB", "licence": "Apache-2.0",
     "note": "Quick code answers and small fixes."},
    {"model": "hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M", "role": "medical", "size": "2.5 GB",
     "licence": "Health AI Developer Foundations terms (open weights, not OSI open source)",
     "note": "Google's medical model. For learning only: not for diagnosis or treatment."},
    {"model": "nebius:nvidia/nemotron-3-super-120b-a12b", "role": "reasoning (cloud, optional)", "size": "none: runs on Nebius",
     "licence": "NVIDIA Open Model License; Nebius Token Factory pricing",
     "note": "The hackathon brain. Needs NEBIUS_API_KEY and the internet; your question leaves the PC."},
    {"model": "nomic-embed-text", "role": "embed", "size": "0.3 GB", "licence": "Apache-2.0",
     "note": "Finds passages by meaning in your knowledge library."},
]
