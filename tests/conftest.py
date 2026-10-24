"""Shared test setup: offline, no model (keyword rules), no background thread, a fresh temp
folder for the database and files in every test, and no network at all."""
import os
import socket

# Must run before any airtac import: never read a real .env, never go online, never start the scheduler.
os.environ["AIRTAC_SKIP_DOTENV"] = "1"
os.environ["AIRTAC_OFFLINE"] = "1"
os.environ["AIRTAC_LLM"] = "mock"
os.environ["AIRTAC_NO_SCHEDULER"] = "1"
os.environ["AIRTAC_NO_LAN"] = "1"              # never open the phone listener (tests/test_pairing.py does, on loopback)
os.environ["AIRTAC_NO_INSTALL_TOKEN"] = "1"   # tests/test_hardening.py switches the per-install token back on
for _key in ("GITHUB_TOKEN", "TAVILY_API_KEY", "NEBIUS_API_KEY", "AIRTAC_PASSCODE", "GROQ_API_KEY", "NVIDIA_API_KEY",
             "OPENROUTER_API_KEY", "GEMINI_API_KEY", "HF_TOKEN", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY",
             "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "PERPLEXITY_API_KEY",
             "CEREBRAS_API_KEY", "COHERE_API_KEY", "ZAI_API_KEY"):
    os.environ.pop(_key, None)
for _key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
    os.environ.pop(_key, None)

import pytest  # noqa: E402

from airtac import agent, auth, config, connections, db, plugins, work  # noqa: E402

config.TRUSTED_HOSTS.append("testserver")  # the name FastAPI's TestClient uses
plugins.load_all()  # at import, so tests can be parametrized over the tool registry

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _guard(real):
    # Any real network connection fails the test loudly. Loopback stays allowed (asyncio uses it).
    def connect(sock, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise RuntimeError(f"Network access is disabled in tests (tried {host})")
        return real(sock, address, *args, **kwargs)
    return connect


@pytest.fixture(autouse=True)
def offline_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _guard(_real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard(_real_connect_ex))
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "airtac.db")
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", tmp_path / "knowledge")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "OFFLINE", True)
    # The app catalog is a file in the project: every test edits its own copy, never the real one.
    import shutil
    copy = tmp_path / "apps.json"
    shutil.copy(config.ROOT / "plugins" / "desktop" / "apps.json", copy)
    monkeypatch.setattr(config, "APPS_FILE", copy, raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(config, "PASSCODE", "")
    monkeypatch.setattr(db, "USE_FTS", None)
    monkeypatch.setattr(config, "SAVED", {})          # no key saved on the Connections page
    connections.LAST_TEST.clear()
    plugins.load_all()  # once; later calls return straight away
    agent._kill.clear()
    agent._pending.clear()
    auth._failures.clear()
    # Work mode jobs never run in a background thread during tests (it could outlive the temp folder).
    work.JOBS.clear()
    monkeypatch.setattr(work, "_enqueue", lambda job_id: None)
