"""Part C: models on this PC, picking a model per role, switching plugins, the Add plugin form, and
Dev tools' live output."""
import shutil
import sys
import time
import tomllib

import httpx
import pytest
from fastapi.testclient import TestClient

from airtac import brains, config
from airtac.main import app

here = lambda: TestClient(app, base_url="http://localhost")   # noqa: E731
away = lambda: TestClient(app)                                # noqa: E731


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "airtac.toml"
    path.write_text('# AIRTAC settings\n[llm]\nmodel = "nemotron-3-nano:4b" # the brain\n\n[brains]\ncoding = ""  # code\n'
                    'embed = "nomic-embed-text"\n\n[plugins]\ndisabled = []\n', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "RAW", tomllib.loads(path.read_text()))
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "BRAINS", {"coding": "", "embed": "nomic-embed-text"})
    tags = {"models": [{"name": "nemotron-3-nano:4b", "size": 2_800_000_000, "details": {"family": "nemotron", "parameter_size": "4B"}},
                       {"name": "qwen2.5-coder:1.5b", "size": 990_000_000, "details": {}},
                       {"name": "nomic-embed-text:latest", "size": 274_000_000, "details": {}}]}
    monkeypatch.setattr(brains.httpx, "get", lambda url, **k: httpx.Response(200, json=tags, request=httpx.Request("GET", url)))
    return path


def test_models_show_size_memory_and_roles(cfg):
    d = here().get("/api/brains/models").json()
    by = {m["name"]: m for m in d["models"]}
    assert by["nemotron-3-nano:4b"]["size_gb"] == 2.8 and by["nemotron-3-nano:4b"]["ram_gb"] == 3.9
    assert "general" in by["nemotron-3-nano:4b"]["roles"] and "embed" in by["nomic-embed-text:latest"]["roles"]


def test_picking_a_model_saves_config_keeps_comments_and_applies_at_once(cfg):
    r = here().post("/api/brains/models", json={"role": "coding", "model": "qwen2.5-coder:1.5b"})
    assert r.status_code == 200 and brains.model_for("coding") == "qwen2.5-coder:1.5b"
    text = cfg.read_text()
    assert 'coding = "qwen2.5-coder:1.5b"  # code' in text and "# AIRTAC settings" in text
    assert list((config.DB_PATH.parent / "backups" / "config").glob("airtac-*.toml"))
    assert here().post("/api/brains/models", json={"role": "coding", "model": "llama-not-pulled"}).status_code == 400
    assert here().post("/api/brains/models", json={"role": "boss", "model": ""}).status_code == 400
    assert away().post("/api/brains/models", json={"role": "coding", "model": ""}).status_code in (401, 403)


def test_switching_a_plugin_writes_the_disabled_list(cfg):
    assert here().post("/api/plugins/music/switch", json={"on": False}).json()["disabled"] == ["music"]
    assert tomllib.loads(cfg.read_text())["plugins"]["disabled"] == ["music"]
    assert here().post("/api/plugins/music/switch", json={"on": True}).json()["disabled"] == []
    assert here().post("/api/plugins/settings/switch", json={"on": False}).status_code == 400
    assert here().post("/api/plugins/nope/switch", json={"on": False}).status_code == 404


def test_add_plugin_form_makes_a_working_plugin_found_as_new(tmp_path, monkeypatch, cfg):
    plugins_dir = tmp_path / "plugins"
    shutil.copytree(config.PLUGINS_DIR / "music", plugins_dir / "music")
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(config, "PLUGINS_DIR", plugins_dir)
    r = here().post("/api/plugins/new", json={"id": "tides", "name": "Tides", "about": 'Today\'s "tides"',
                                              "risk": "read", "websites": ["api.open-meteo.com"]})
    assert r.status_code == 200, r.text
    manifest = tomllib.loads((plugins_dir / "tides" / "manifest.toml").read_text())
    assert manifest["permissions"] == {"network": ["api.open-meteo.com"], "risk": "read"}
    compile((plugins_dir / "tides" / "__init__.py").read_text(), "tides", "exec")     # valid Python
    assert (tmp_path / "tests" / "test_plugin_tides.py").exists()
    rows = {x["id"]: x for x in here().get("/api/plugins").json()}
    assert rows["tides"]["state"] == "new" and "Restart" in rows["tides"]["reason"]
    for bad in ({"id": "Bad Id"}, {"id": "ok_id", "name": 'x"); import os'}, {"id": "ok_two", "websites": ["https://x.com/path"]},
                {"id": "ok_three", "risk": "root"}, {"id": "tides"}):
        assert here().post("/api/plugins/new", json=bad).status_code == 400, bad


def test_dev_tools_stream_output_while_running(monkeypatch):
    from plugins import devtools
    tool = {"cmd": [sys.executable, "-c", "import time\nfor i in range(3): print('line', i, flush=True); time.sleep(0.2)"],
            "need": [], "check": lambda: True, "after": "All set."}
    monkeypatch.setattr(devtools, "_tools", lambda: {"demo": tool})
    monkeypatch.setattr(devtools, "_present", lambda need: True)
    first = here().post("/api/plugins/devtools/run", json={"tool": "demo"}).json()
    assert first["lines"][0].startswith("$ ") and not first["done"]
    assert here().post("/api/plugins/devtools/run", json={"tool": "demo"}).status_code == 409   # one at a time
    for _ in range(50):
        r = here().get("/api/plugins/devtools/run", params={"after": 0}).json()
        if r["done"]:
            break
        time.sleep(0.1)
    assert r["done"] and r["ok"] and [x for x in r["lines"] if x.startswith("line")] == ["line 0", "line 1", "line 2"]
    assert here().get("/api/plugins/devtools/run", params={"after": r["next"]}).json()["lines"] == []
    assert away().post("/api/plugins/devtools/run", json={"tool": "demo"}).status_code in (401, 403)
