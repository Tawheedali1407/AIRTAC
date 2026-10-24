"""The HTTP API, the passcode for other devices, and the trusted-host guard."""
import pytest
from fastapi.testclient import TestClient

from airtac import auth, config
from airtac.main import app

client = TestClient(app, base_url="http://localhost")  # this PC: no passcode needed


def test_health_and_the_home_screen():
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["brain"]["provider"] == "mock" and h["offline"]
    apps = client.get("/api/apps").json()
    ids = {a["id"] for a in apps}
    assert {"schedule", "knowledge", "memory", "practice", "documents", "coach"} <= ids and "web" not in ids
    assert client.get("/api/feed").json()["sections"][0]["sample"] is True
    progress = client.get("/api/progress").json()
    assert len(progress["week"]) == 7 and progress["hero"]["label"] == "Solved on LeetCode"
    assert ", Ali." in client.get("/api/briefing", params={"offset": -330}).json()["text"]  # any time of day


@pytest.mark.parametrize("app_id", ["schedule", "knowledge", "memory", "study", "practice", "leetcode", "documents",
                                    "github", "news", "diagrams", "system", "desktop", "music", "backup", "coach"])
def test_every_app_page_draws(app_id):
    page = client.get(f"/api/apps/{app_id}").json()
    assert page["id"] == app_id and isinstance(page["stats"], list) and "chats" in page
    assert "permissions" in page


def test_plugins_and_brains_pages():
    plugins = client.get("/api/plugins").json()
    assert any(p["id"] == "phone" and p["state"] == "planned" for p in plugins)
    brains = client.get("/api/brains").json()
    assert [r["role"] for r in brains["roles"]][:4] == ["general", "coding", "reasoning", "medical"]
    assert any(c["role"] == "medical" for c in brains["catalog"])


def test_chat_answers_and_returns_an_id_for_feedback():
    r = client.post("/api/chat", json={"message": "Remember my exam is on 12 Nov"}).json()
    assert r["steps"][0]["tool"] == "memory_save" and r["message_id"]
    fb = client.post("/api/feedback", json={"message_id": r["message_id"], "good": False,
                                            "note": "Say the date as 12 November."}).json()
    assert fb["ok"] and fb["lesson"]
    lessons = client.get("/api/lessons").json()["lessons"]
    assert lessons[0]["active"] and "12 November" in lessons[0]["text"]
    assert client.post(f"/api/lessons/{lessons[0]['id']}", params={"active": False}).json() == {"ok": True}
    assert client.delete(f"/api/lessons/{lessons[0]['id']}").json() == {"ok": True}
    assert client.post("/api/feedback", json={"message_id": 999, "good": True}).status_code == 404


def test_write_tools_wait_for_confirm():
    r = client.post("/api/chat", json={"message": "Create an issue: add dark mode"}).json()
    assert r["pending"] and r["steps"][0]["status"] == "pending"
    done = client.post("/api/confirm", json={"pending_id": r["pending"]["id"], "approve": False}).json()
    assert done["spoken"] == "Okay, I cancelled that."


def test_files_are_served_only_from_outputs():
    client.post("/api/chat", json={"message": "Make a study plan pdf"})
    files = client.get("/api/files").json()
    assert files and files[0]["plugin"] == "memory"
    assert client.get(files[0]["url"]).status_code == 200
    assert client.get("/files/../.env").status_code == 404
    assert client.get("/files/%2e%2e/config/airtac.toml").status_code == 404


def test_notifications_start_from_the_latest():
    from airtac import memory
    first = client.get("/api/notifications").json()
    memory.notify("Reminder: drink water", "", "Drink water.", "schedule")
    news = client.get("/api/notifications", params={"after": first["last"]}).json()
    assert news["items"][0]["title"] == "Reminder: drink water"


def test_unknown_host_names_are_refused():
    r = client.get("/api/health", headers={"host": "evil.example"})
    assert r.status_code == 403


# ---------------------------------------------------------------- other devices need the passcode

@pytest.fixture
def phone(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_HOSTS", config.TRUSTED_HOSTS + ["ali-pc.tail1234.ts.net"])
    return TestClient(app, base_url="https://ali-pc.tail1234.ts.net")


def test_another_device_needs_the_passcode(phone, monkeypatch):
    assert phone.get("/api/health").status_code == 401
    page = phone.get("/", follow_redirects=False)
    assert page.status_code in (302, 307) and page.headers["location"].endswith("/login.html")
    assert phone.get("/login.html").status_code == 200
    # No passcode set: other devices can't get in at all.
    assert phone.post("/api/login", json={"passcode": "anything"}).status_code == 403
    monkeypatch.setattr(config, "PASSCODE", "correct horse")
    assert phone.post("/api/login", json={"passcode": "wrong"}).status_code == 401
    ok = phone.post("/api/login", json={"passcode": "correct horse"})
    assert ok.status_code == 200 and "httponly" in ok.headers["set-cookie"].lower()
    assert "secure" in ok.headers["set-cookie"].lower()
    assert phone.get("/api/health").status_code == 200  # the cookie works
    phone.post("/api/logout")
    phone.cookies.clear()
    assert phone.get("/api/health").status_code == 401


def test_wrong_passcodes_lock_out_for_a_while(phone, monkeypatch):
    monkeypatch.setattr(config, "PASSCODE", "right")
    for _ in range(auth.MAX_FAILURES):
        assert phone.post("/api/login", json={"passcode": "nope"}).status_code == 401
    assert phone.post("/api/login", json={"passcode": "right"}).status_code == 429


def test_this_computer_never_needs_the_passcode(monkeypatch):
    monkeypatch.setattr(config, "PASSCODE", "set")
    assert TestClient(app, base_url="http://localhost").get("/api/health").status_code == 200
