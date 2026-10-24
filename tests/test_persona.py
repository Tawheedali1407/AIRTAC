"""Part E1: the shared persona file reaches every chat; Settings switches the tone."""
from fastapi.testclient import TestClient

from airtac import config, llm
from airtac.main import app

client = TestClient(app, base_url="http://localhost")


def test_every_chat_gets_the_friendly_honest_persona():
    text = llm.system_prompt([])
    assert "smart friend and study buddy" in text
    assert "If their idea, answer or plan has a mistake, say so kindly" in text   # honest, not flattering
    assert "never pretend to be human" in text and "{name}" not in text


def test_tone_switch_in_settings():
    assert client.post("/api/plugins/settings", json={"tone": "neutral"}).json()["tone"] == "neutral"
    assert "neutral tone" in llm.system_prompt([]) and "study buddy" not in llm.system_prompt([])
    assert client.post("/api/plugins/settings", json={"tone": "cheeky"}).status_code == 400
    client.post("/api/plugins/settings", json={"tone": "friendly"})


def test_the_files_are_shared_for_airtac1_and_the_phone():
    for name in llm.PERSONAS.values():
        assert (config.ROOT / "prompts" / name).is_file()


def test_default_brain_can_be_a_menu_pick():
    assert client.post("/api/plugins/settings", json={"brain": "ollama:qwen3:1.7b"}).status_code == 200
    assert client.post("/api/plugins/settings", json={"brain": "nobody:x"}).status_code == 400
    client.post("/api/plugins/settings", json={"brain": ""})
