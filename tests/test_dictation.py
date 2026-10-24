"""Dictation like Wispr Flow (Ali, 18 Oct): Settings > Dictation switches scripts/dictate.py on and off. The script
itself needs Windows, a microphone and the keyboard hook; here: its text tidy-up and the settings routes."""
import importlib.util
import sys

from fastapi.testclient import TestClient

from airtac import config
from airtac.main import app
from plugins import dictation

client = TestClient(app, base_url="http://localhost")
spec = importlib.util.spec_from_file_location("dictate", config.ROOT / "scripts" / "dictate.py")
dictate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dictate)


def test_tidy_drops_fillers_and_repeats_but_never_rewords():
    assert dictate.tidy(" um so I think the the meeting is uh at five ") == "So I think the meeting is at five"
    assert dictate.tidy("um  hello", clean=False) == "um hello"
    assert dictate.tidy("") == ""


def test_settings_are_checked_and_kept(monkeypatch):
    started = []
    monkeypatch.setattr(dictation, "start", lambda: started.append(1) or {"running": True, "status": "ready"})
    r = client.get("/api/plugins/dictation").json()
    assert r["settings"] == {"on": False, "key": "right ctrl", "language": "en", "model": "base.en", "clean": True}
    r = client.post("/api/plugins/dictation", json={"on": True, "key": "f9", "model": "bogus"}).json()
    assert r["settings"]["on"] and r["settings"]["key"] == "f9" and r["settings"]["model"] == "base.en" and started
    assert dictate.settings.__module__ == "dictate"
    assert client.post("/api/plugins/dictation", json={"key": "x" * 30}).status_code == 422


def test_only_this_pc_can_switch_it():
    assert client.get("/api/plugins/dictation", headers={"X-Forwarded-For": "192.168.1.9"}).status_code in (401, 403)


def test_start_refuses_off_windows():
    if sys.platform != "win32":
        assert "Windows only" in dictation.start()["error"]
