"""Makes a new plugin from a template, ready to edit:
    .venv\\Scripts\\python.exe scripts\\new_plugin.py weather "Weather" "Today's weather where you are."
Creates plugins/<id>/manifest.toml, plugins/<id>/__init__.py and tests/test_plugin_<id>.py.
Restart AIRTAC and it appears in the menu. See docs/PLUGINS.md.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RISKS = ("read", "local", "write", "device")
HOST = re.compile(r"^[a-z0-9.-]{3,100}$")


def make(pid: str, name: str = "", about: str = "", risk: str = "local", network: list[str] | None = None,
         root: Path = ROOT) -> Path:
    """Create plugins/<pid>/ (manifest, code, a test). Raises ValueError with a sentence for the page.
    Used by this script and by the "Add plugin" form on the Brains and plugins page."""
    pid = (pid or "").lower().strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", pid):
        raise ValueError("The id is 2 to 31 letters, digits or _ and starts with a letter, e.g. weather.")
    name = (name or pid.title()).strip()[:40]
    if not re.fullmatch(r"[A-Za-z0-9 &()+.-]{1,40}", name):
        raise ValueError("The name may use letters, digits, spaces and & ( ) + . - only.")
    about = re.sub(r'["\\{}\r\n]+', " ", about or f"What {name} does, in one line.").strip()[:140]   # it goes into code
    if risk not in RISKS:
        raise ValueError("Risk is read, local, write or device.")
    network = [h.strip().lower() for h in (network or []) if h.strip()]
    if any(not HOST.fullmatch(h) for h in network):
        raise ValueError("Websites are host names like api.example.com (no https://, no paths).")
    folder = root / "plugins" / pid
    if folder.exists():
        raise ValueError(f"plugins/{pid} already exists.")
    folder.mkdir(parents=True)
    q = json.dumps            # TOML strings, safely quoted
    (folder / "manifest.toml").write_text(f'''id = "{pid}"
name = {q(name)}
version = "0.1.0"
icon = "puzzle"
about = {q(about)}
order = 100
keywords = ["{pid}"]                # words that route a question here
quick = ["Say hello from {name}"]   # buttons on the app page

[requires]
imports = []                        # Python packages it needs, e.g. ["requests"]

[permissions]
network = {q(network)}                        # websites it may call, e.g. ["api.example.com"]
risk = "{risk}"                      # read | local | write (asks first) | device (asks first)
''', encoding="utf-8")
    (folder / "__init__.py").write_text(f'''"""{name}: {about}"""
from airtac.plugin_api import Tool, ToolResult, plugin, schema

p = plugin(__name__)


def {pid}_hello(name: str = "") -> ToolResult:
    # spoken: 1 to 3 short sentences, read aloud. screen: the details.
    return ToolResult(f"Hello{{', ' + name if name else ''}} from {name}.", "Edit plugins/{pid}/__init__.py to make me useful.")


p.add(Tool("{pid}_hello", "Say hello from the {name} plugin (replace this with what your tool does).",
           schema({{"name": {{"type": "string", "description": "Who to greet"}}}}), "local", {pid}_hello))


@p.dashboard
def page(pg: dict) -> None:
    pg["stats"] = [{{"label": "Hellos", "value": 0}}]


p.mock_rule(50, ("hello from {pid}", "{pid}"), "{pid}_hello")
''', encoding="utf-8")
    (root / "tests" / f"test_plugin_{pid}.py").write_text(f'''from airtac import tools


def test_{pid}_says_hello():
    assert "Hello" in tools.run_tool("{pid}_hello", {{"name": "Ali"}}).spoken
''', encoding="utf-8")
    return folder


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    try:
        made = make(sys.argv[1], *(sys.argv[2:4]))
    except ValueError as e:
        sys.exit(str(e))
    print(f"Made plugins/{made.name}. Edit it, run: .venv\\Scripts\\python.exe -m pytest, then restart AIRTAC.")
