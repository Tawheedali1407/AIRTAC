"""Missing packages are named on the page (the vault once failed silently without argon2-cffi)."""
from airtac import deps


def test_missing_packages_are_listed(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi==0.1  # web\nsurely-not-installed-pkg==1.0\n\n# comment\nhttpx>=0.1\n", encoding="utf-8")
    assert deps.missing(req) == ["surely-not-installed-pkg"]


def test_health_reports_them():
    from fastapi.testclient import TestClient
    from airtac.main import app
    assert isinstance(TestClient(app, base_url="http://localhost").get("/api/health").json()["missing_packages"], list)
