"""Finds plugins in plugins/<id>/manifest.toml, decides which can run, and loads them.

A plugin is on when:
  - its status isn't "planned",
  - airtac.toml doesn't switch it off ([plugins] enabled / disabled),
  - it supports this computer (requires.platforms),
  - the Python packages it needs are installed (requires.imports).
Otherwise it stays off, and the Plugins page says why and how to fix it.
A plugin that fails while loading is reported, never allowed to stop AIRTAC from starting.
"""
import importlib
import importlib.util
import logging
import sys
import tomllib

from airtac import config
from airtac.plugin_api import LOADED, Plugin

log = logging.getLogger("airtac.plugins")

STATUS: dict[str, dict] = {}   # every plugin found, on or off, with the reason
_loaded = False


def read_manifest(path) -> dict:
    with open(path, "rb") as f:
        m = tomllib.load(f)
    m.setdefault("id", path.parent.name)
    m.setdefault("name", m["id"].title())
    m.setdefault("status", "ready")
    m.setdefault("order", 50)
    m.setdefault("requires", {})
    m.setdefault("permissions", {})
    return m


def discover() -> list[dict]:
    found = []
    for path in sorted(config.PLUGINS_DIR.glob("*/manifest.toml")):
        try:
            m = read_manifest(path)
            m["_dir"] = path.parent
            found.append(m)
        except (OSError, tomllib.TOMLDecodeError) as e:
            STATUS[path.parent.name] = {"id": path.parent.name, "name": path.parent.name, "state": "error",
                                        "reason": f"manifest.toml can't be read: {e}"}
    return sorted(found, key=lambda m: (m["order"], m["id"]))


def _missing_imports(m: dict) -> list[str]:
    """pip names of the packages this plugin needs that aren't installed."""
    imports = m["requires"].get("imports", [])
    pips = m["requires"].get("pip", imports)
    missing = []
    for i, name in enumerate(imports):
        if importlib.util.find_spec(name) is None:
            missing.append(pips[i] if i < len(pips) else name)
    return missing


def _decide(m: dict) -> tuple[str, str]:
    if m["status"] == "planned":
        return "planned", "On the roadmap. See its manifest for the plan."
    if m["id"] in config.PLUGINS_DISABLED or (config.PLUGINS_ENABLED is not None and m["id"] not in config.PLUGINS_ENABLED):
        return "off", "Switched off in config/airtac.toml."
    platforms = m["requires"].get("platforms", [])
    if platforms and sys.platform not in platforms:
        return "unsupported", f"Only works on {', '.join(platforms)}."
    needed = [x for x in m["requires"].get("plugins", []) if x not in LOADED]
    if needed:
        return "needs-plugins", f"Needs the {', '.join(needed)} plugin, which isn't on."
    missing = _missing_imports(m)
    hint = f"pip install {' '.join(missing)}  (or scripts\\plugins.ps1 {m['id']})"
    if missing and not m["requires"].get("optional"):
        return "needs-packages", f"Install: {hint}"
    if missing:  # works without them, better with them
        return "on", f"Works better with: {hint}"
    return "on", ""


def load_all(force: bool = False) -> None:
    """Load every plugin that can run. Called once at start-up (and by tests)."""
    global _loaded
    if _loaded and not force:
        return
    from airtac import tools  # imported here: tools imports this module

    STATUS.clear()
    LOADED.clear()
    tools.reset()
    for m in discover():
        state, reason = _decide(m)
        info = {"id": m["id"], "name": m["name"], "icon": m.get("icon", "puzzle"), "about": m.get("about", ""),
                "status": m["status"], "version": m.get("version", "0.1.0"), "state": state, "reason": reason,
                "permissions": m["permissions"], "requires": m["requires"], "plan": m.get("plan", {}),
                "order": m["order"], "tools": []}
        STATUS[m["id"]] = info
        if state != "on":
            continue
        p = Plugin(id=m["id"], name=m["name"], dir=m["_dir"], manifest=m)
        LOADED[m["id"]] = p
        module = f"plugins.{m['id']}"
        try:
            if module in sys.modules and force:
                importlib.reload(sys.modules[module])
            else:
                importlib.import_module(module)
        except Exception as e:  # a broken plugin must not stop AIRTAC
            log.exception("plugin %s failed to load", m["id"])
            LOADED.pop(m["id"], None)
            info.update(state="error", reason=f"Failed to load: {type(e).__name__}: {e}")
            continue
        tools.register_plugin(p)
        info["tools"] = [t.name for t in p.tools]
    _loaded = True


def brain_gate(tier: str, max_tokens: int) -> int | None:
    """Ask the plugins' @p.brain_gate hooks whether a brain may answer now. None = skip this cloud tier
    (for example, it used up today's token limit); otherwise the answer length to ask for."""
    for p in enabled():
        hook = p.hooks.get("brain_gate")
        if not hook:
            continue
        try:
            result = hook(tier, max_tokens)
        except Exception:
            log.exception("brain gate in %s failed", p.id)
            continue
        if result is None:
            return None
        max_tokens = max(32, int(result))
    return max_tokens


def report_usage(tier: str, model: str, prompt: int, completion: int) -> None:
    """Tell the plugins' @p.usage hooks how many tokens an answer took. Never breaks an answer."""
    for p in enabled():
        hook = p.hooks.get("usage")
        if hook:
            try:
                hook(tier, model, int(prompt or 0), int(completion or 0))
            except Exception:
                log.exception("usage hook in %s failed", p.id)


def get(plugin_id: str) -> Plugin | None:
    return LOADED.get(plugin_id)


def enabled() -> list[Plugin]:
    return sorted(LOADED.values(), key=lambda p: (p.manifest["order"], p.id))


def apps() -> list[dict]:
    """Plugins with tools get an app page (menu entry, dashboard, own chat)."""
    return [{"id": p.id, "name": p.name, "icon": p.manifest.get("icon", "puzzle"), "about": p.manifest.get("about", "")}
            for p in enabled() if p.tools and p.manifest.get("app", True)]
