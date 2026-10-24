"""Checks that AIRTAC Advance can run here and says what to fix, in plain words.
    .venv\\Scripts\\python.exe scripts\\check_system.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AIRTAC_NO_SCHEDULER", "1")

ok = True


def line(good: bool, text: str, fix: str = "") -> None:
    global ok
    ok = ok and good
    print(("  OK   " if good else "  FIX  ") + text + (f"\n         -> {fix}" if fix and not good else ""))


print("AIRTAC Advance: system check\n")
line(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}", "Install Python 3.11 or newer.")

try:
    import psutil
    ram = psutil.virtual_memory().total / 2**30
    line(ram >= 7, f"Memory: {ram:.0f} GB", "Under 8 GB: set [llm] model = \"qwen3.5:2b\" in config/airtac.toml (Nemotron Nano 4B needs about 3 GB free).")
    free = psutil.disk_usage(str(Path(__file__).anchor or "/")).free / 2**30
    line(free >= 8, f"Free disk: {free:.0f} GB", "Models need about 4 to 10 GB. Free some space.")
except ImportError:
    line(False, "psutil", "Run Install-AIRTAC.cmd again.")

from airtac import brains, config, plugins  # noqa: E402

plugins.load_all()
st = brains.status()
if config.LLM_PROVIDER == "ollama":
    line(bool(st["ollama_up"]), "Ollama is running", "Open Ollama from the Start menu (or run: ollama serve).")
    for r in st["roles"]:
        if r["installed"] is False:
            line(False, f"{r['label']} model {r['model']}", f"ollama pull {r['model']}")
        elif r["installed"]:
            line(True, f"{r['label']} model {r['model']}")
else:
    print(f"  --   Brain: {config.LLM_PROVIDER} ({config.LLM_MODEL})")

print("\nPlugins")
for s in sorted(plugins.STATUS.values(), key=lambda s: s["order"]):
    if s["state"] == "planned":
        continue
    line(s["state"] == "on", f"{s['name']}: {s['state']}" + (f" ({s['reason']})" if s["reason"] and s["state"] == "on" else ""),
         s["reason"])
planned = [s["name"] for s in plugins.STATUS.values() if s["state"] == "planned"]
print(f"  --   On the roadmap: {', '.join(planned)}")
print("\nAll good." if ok else "\nFix the lines marked FIX, then run this again.")
