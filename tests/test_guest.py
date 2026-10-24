"""Guest mode (the public try-it copy): host names, the reset, and that it's off on your PC."""
from airtac import config, deps, guest


def test_guest_mode_is_off_unless_asked():
    assert config.GUEST is False
    assert "passwords" not in config.PLUGINS_DISABLED


def test_suffix_hosts(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_HOSTS", ["localhost", ".hf.space"])
    assert config.host_trusted("tawheedali1407-airtac.hf.space") and config.host_trusted("localhost")
    assert not config.host_trusted("hf.space.evil.com") and not config.host_trusted("evil.com")


def test_reset_keeps_only_the_token_and_logs(tmp_path, monkeypatch):
    data = config.DB_PATH.parent
    data.mkdir(parents=True, exist_ok=True)
    for name in ("airtac.db", "settings.json", ".install_token"):
        (data / name).write_text("x")
    (data / "logs").mkdir()
    (data / "uploads").mkdir()
    guest.wipe()
    assert sorted(p.name for p in data.iterdir()) == [".install_token", "logs"]


def test_packages_for_another_system_are_not_missing(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text('winotify==1.1.0; sys_platform == "nosuchos"\nnot-a-real-package-xyz==1.0\n')
    assert deps.missing(req) == ["not-a-real-package-xyz"]
