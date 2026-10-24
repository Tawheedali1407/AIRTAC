"""Step 0 of the fix plan: the page always has somewhere to say the server is down, and the
helpers that start, stop and check AIRTAC are in place."""
import re

from fastapi.testclient import TestClient

from airtac import config
from airtac.main import app

WEB = config.WEB_DIR


def test_offline_banner_is_in_the_page_and_wired():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for el_id in ("conn-banner", "conn-banner-title", "conn-banner-detail", "conn-start", "conn-retry"):
        assert f'id="{el_id}"' in html, el_id                     # app.js writes into each of these
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert '$("conn-start").addEventListener("click", startServer)' in js
    assert "link.pageReady = true" in js                          # recovery refreshes in place


def test_every_section_uses_the_one_api_client():
    """No page script calls fetch("/api/...") directly: everything goes through api(), so every
    section fails the same readable way and opens the banner."""
    for name in ("app.js", "hub.js"):
        js = (WEB / name).read_text(encoding="utf-8")
        calls = re.findall(r'fetch\(([^,)]*)', js)
        # Allowed: api()'s own fetch (API + path), the banner's health check, voice (audio blobs, not JSON) and the
        # streamed answer (read piece by piece, which api() can't do; it raises the same ApiError).
        direct = [c for c in calls if "/api/" in c and "/api/health" not in c and "/api/voice/" not in c
                  and "/api/chat/stream" not in c]
        assert not direct, (name, direct)


def test_health_answers_and_doctor_is_this_pc_only(monkeypatch):
    here = TestClient(app, base_url="http://localhost")
    assert here.get("/api/health").json()["status"] == "ok"
    monkeypatch.setattr(config, "PASSCODE", "phone-passcode-123")
    phone = TestClient(app, base_url="http://testserver")
    phone.post("/api/login", json={"passcode": "phone-passcode-123"})
    assert phone.post("/api/plugins/devtools/doctor").status_code == 403


def test_start_scripts_exist():
    root = config.ROOT
    for f in ("start_airtac.bat", "scripts/serve.py", "scripts/start.ps1", "scripts/stop.ps1", "scripts/doctor.py"):
        assert (root / f).is_file(), f
    assert "-ServerOnly" in (root / "scripts" / "make_shortcut.ps1").read_text(encoding="utf-8")


def test_main_chat_holds_only_the_chat():
    """Apps, AI tools, Open and the route trace left Main chat; every app is in the menu instead."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    home = html.split('id="view-home"')[1].split('id="view-app"')[0]
    for gone in ('id="apps"', 'id="ai-tools"', 'id="launch"', 'id="route"', 'class="elements"'):
        assert gone not in home, gone
    assert "air · fire" not in html and 'id="drawer-apps"' in html


def test_phone_layout_rules_are_in_place():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert 'name="viewport"' in html and "width=device-width" in html
    assert "@media (max-width: 600px), (pointer: coarse)" in css and "min-height: 44px" in css   # 44 px touch targets
