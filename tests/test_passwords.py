"""The vault (v2): Argon2id + AES-256-GCM, the super password, recovery key, migration from v1, locks,
lockout, tampering, reveal/copy rules, PC-only routes, and API keys moving into the vault."""
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from airtac import config
from plugins.passwords import legacy, store

SECRET = "Hunter2-Very-Secret!"
PASS = "correct horse battery"
COPIED: list = []


@pytest.fixture(autouse=True)
def quick_kdf(monkeypatch):
    from plugins.passwords import clipboard
    monkeypatch.setattr(store, "ARGON", {"m": 1024, "p": 1})      # the real one (64 MiB) is slow on purpose
    monkeypatch.setattr(store, "TARGET_SECONDS", 0.001)
    monkeypatch.setattr(store, "_reveals", __import__("collections").deque())
    COPIED.clear()      # never touch the real clipboard: a test run would leave a password on it
    monkeypatch.setattr(clipboard, "copy", lambda text, clear_after=20: COPIED.append(text) or True)
    store.lock()
    store._state.update(service=None, weak=False)
    yield
    store.lock()
    store._state.update(service=None)


def _vault_with_one():
    rec = store.setup(PASS)
    eid = store.save({"site": "Instagram", "url": "https://www.instagram.com", "app_id": "instagram",
                      "username": "ali.cse", "password": SECRET, "notes": "2FA on my phone"})
    return eid, rec


def _client(here=True):
    from airtac.main import app
    return TestClient(app, base_url="http://localhost" if here else "http://testserver")


# ---------------------------------------------------------------- crypto and the file

def test_file_holds_no_plaintext_and_uses_argon2id_aes_gcm():
    _vault_with_one()
    raw = store.path().read_text()
    for plain in (SECRET, "ali.cse", "Instagram", "instagram.com", "2FA", PASS):
        assert plain not in raw
    v = json.loads(raw)
    assert v["header"]["version"] == 2 and v["slots"]["password"]["kdf"]["alg"] == "argon2id"
    assert set(v["slots"]) == {"password", "recovery"} and store.path().with_suffix(".bin.bak").exists()


def test_unlock_lock_and_entries_without_passwords():
    eid, _ = _vault_with_one()
    store.lock()
    assert store.entries() == [] and not store.status()["unlocked"]          # locked by default: nothing shown
    with pytest.raises(store.Locked):
        store.copy_value(eid, "password")
    store.unlock(PASS)
    rows = store.entries()
    assert rows[0]["username"] == "ali.cse" and "password" not in rows[0] and rows[0]["has_password"]
    assert store.copy_value(eid, "password") == SECRET and store.reveal(eid) == SECRET


def test_wrong_password_then_lockout_doubles_from_30_seconds(monkeypatch):
    _vault_with_one()
    store.lock()
    for i in range(4):
        with pytest.raises(store.VaultError, match="isn't right"):
            store.unlock("wrong password here")
    with pytest.raises(store.VaultError, match="Wait 30 seconds"):
        store.unlock("wrong password here")                    # the 5th wrong try starts a 30 s wait
    with pytest.raises(store.VaultError, match="Too many wrong tries"):
        store.unlock(PASS)                                    # even the right one waits
    assert 25 <= store.wait_left() <= 30
    real = store.time.time
    monkeypatch.setattr(store.time, "time", lambda: real() + 31)
    with pytest.raises(store.VaultError, match="Wait 60 seconds"):
        store.unlock("wrong again, sorry")                    # doubles
    monkeypatch.setattr(store.time, "time", lambda: real() + 31 + 61)
    store.unlock(PASS)                                        # the right one resets the count
    assert store.status()["unlocked"] and store.wait_left() == 0


def test_the_wait_never_passes_an_hour():
    _vault_with_one()
    v = json.loads(store.path().read_text())
    store._set_guard(v, 20, 0)
    store._write(v)
    store.lock()
    with pytest.raises(store.VaultError, match="Wait 3600 seconds"):
        store.unlock("wrong password here")


def test_editing_the_counter_is_caught():
    _vault_with_one()
    store.lock()
    with pytest.raises(store.VaultError):
        store.unlock("wrong password here")
    v = json.loads(store.path().read_text())
    v["guard"]["fails"] = 0                                    # an attacker resets the count...
    store.path().write_text(json.dumps(v))
    with pytest.raises(store.VaultError, match="changed outside AIRTAC"):
        store.unlock(PASS)                                    # ...and gets an hour's wait instead
    assert store.wait_left() > 3500


@pytest.mark.parametrize("damage", ["entry", "header", "slot", "removed", "kdf"])
def test_tampered_vault_is_rejected(damage):
    eid, _ = _vault_with_one()
    store.save({"site": "Second", "password": "x"})
    store.lock()
    v = json.loads(store.path().read_text())
    if damage == "entry":
        raw = bytearray(store._ub64(v["logins"][str(eid)]))
        raw[-1] ^= 1
        v["logins"][str(eid)] = store._b64(bytes(raw))
    elif damage == "header":
        v["header"]["id"] = "0000000000000000"
    elif damage == "slot":
        v["slots"]["password"]["login"] = v["slots"]["recovery"]["login"]
    elif damage == "removed":
        v["logins"].pop("2")
    else:
        v["slots"]["password"]["kdf"]["m"] = 2048            # weaker settings slipped in
    store.path().write_text(json.dumps(v))
    with pytest.raises(store.VaultError):
        store.unlock(PASS)
        store.entries()


def test_idle_five_minutes_locks():
    _vault_with_one()
    assert store.is_unlocked()
    assert not store.is_unlocked(now=store.time.time() + store.IDLE_SECONDS + 1)


# ---------------------------------------------------------------- the super password and recovery

def test_new_password_rules_and_strength():
    with pytest.raises(store.VaultError, match="at least 12"):
        store.setup("short pass")
    with pytest.raises(store.VaultError, match="too easy"):
        store.setup("aaaaaaaaaaaaaaaa")
    assert store.strength("Tr1cky-Horse-Battery-77")["score"] == 4 and store.strength("abc")["score"] == 0


def test_change_password_rewraps_without_losing_anything():
    eid, rec = _vault_with_one()
    with pytest.raises(store.VaultError):
        store.change_password("not the password", "a brand new password 42")
    store.change_password(PASS, "a brand new password 42")
    store.lock()
    with pytest.raises(store.VaultError):
        store.unlock(PASS)
    store.unlock("a brand new password 42")
    assert store.reveal(eid) == SECRET


def test_change_without_a_vault_sets_one_up():
    rec = store.change_password("", "my first super password")
    assert rec and store.exists() and store.status()["unlocked"]


def test_recovery_key_resets_a_forgotten_password():
    eid, rec = _vault_with_one()
    assert len(rec.replace("-", "")) == 32
    store.lock()
    with pytest.raises(store.VaultError):
        store.recover("AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH", "another good password 9")
    store.recover(rec.lower(), "another good password 9")      # case and dashes don't matter
    store.lock()
    store.unlock("another good password 9")
    assert store.reveal(eid) == SECRET
    new = store.new_recovery_key()
    store.lock()
    with pytest.raises(store.VaultError):
        store.recover(rec, "yet another password 10")         # the old key stopped working
    store.recover(new, "yet another password 10")


# ---------------------------------------------------------------- migration from v1

def _make_v1(monkeypatch, entries):
    import tests.v1_vault as v1
    monkeypatch.setattr(v1, "SCRYPT", {"n": 2 ** 10, "r": 8, "p": 1})
    monkeypatch.setattr(legacy, "SCRYPT", {"n": 2 ** 10, "r": 8, "p": 1})
    v1.setup("old master passcode")
    for e in entries:
        v1.save(e)
    v1.lock()
    return v1


def test_old_vault_migrates_with_every_entry_intact(monkeypatch):
    backed = []
    import plugins.backup as backup
    monkeypatch.setattr(backup, "make_backup", lambda: backed.append(1))
    sample = [{"site": "Instagram", "url": "https://www.instagram.com", "app_id": "instagram", "username": "ali.cse",
               "password": SECRET, "notes": "2FA on my phone"},
              {"site": "LeetCode", "url": "https://leetcode.com", "app_id": "", "username": "MMDTAWHEEDALI",
               "password": "ünïcödé pässwörd ✓", "notes": "line one\nline two"}]
    _make_v1(monkeypatch, sample)
    assert store.needs_migration() and store.status()["migrate"]
    with pytest.raises(store.VaultError):
        store.unlock("not the old passcode")
    assert legacy.path().exists() and not store.exists()        # a wrong passcode changes nothing
    done = store.unlock("old master passcode")
    assert done["migrated"] and len(done["recovery_key"].replace("-", "")) == 32 and backed == [1]
    assert not legacy.path().exists() and list((config.DB_PATH.parent / "backups").glob("vault-v1-*.db"))
    got = sorted(store.entries(), key=lambda e: e["site"])
    assert [(e["site"], e["url"], e["app_id"], e["username"], e["notes"]) for e in got] == \
           [(e["site"], e["url"], e["app_id"], e["username"], e["notes"]) for e in sorted(sample, key=lambda e: e["site"])]
    assert {store.reveal(e["id"]) for e in got} == {SECRET, "ünïcödé pässwörd ✓"}
    store.lock()
    store.unlock("old master passcode")                         # same password afterwards
    assert store.status()["weak"] is False                      # 19 characters: long enough


def test_migration_stops_if_the_data_backup_fails(monkeypatch):
    import plugins.backup as backup
    monkeypatch.setattr(backup, "make_backup", lambda: (_ for _ in ()).throw(OSError("disk full")))
    _make_v1(monkeypatch, [{"site": "X", "password": "y"}])
    with pytest.raises(store.VaultError, match="back up"):
        store.unlock("old master passcode")
    assert legacy.path().exists() and not store.exists()


# ---------------------------------------------------------------- API keys move into the vault

def test_api_keys_move_into_the_vault_and_work_while_locked(monkeypatch):
    from airtac import vault as service
    monkeypatch.setattr(service, "legacy_all", lambda: {"keys/GROQ_API_KEY/key": "gsk_test_1234567890"})
    service._save_index({"keys/GROQ_API_KEY": {"key": {"parts": 1, "saved": "17 Oct 2026"}}})
    store.setup(PASS)
    store.lock()
    store._state["service"] = None                              # as after a restart: the Windows seal opens it
    assert service.get("keys", "GROQ_API_KEY", "key") == "gsk_test_1234567890"
    service.save("keys", "TAVILY_API_KEY", "key", "tvly-abcdefgh12345")
    assert "tvly-abcdefgh12345" not in store.path().read_text()
    assert service.get("keys", "TAVILY_API_KEY", "key") == "tvly-abcdefgh12345"
    service.delete("keys", "TAVILY_API_KEY", "key")
    assert service.get("keys", "TAVILY_API_KEY", "key") == ""
    assert service.available() == (True, "AIRTAC vault (AES-256-GCM)")


# ---------------------------------------------------------------- backups, routes, rules

def test_export_is_the_encrypted_file_and_opens_only_with_the_password(tmp_path):
    _vault_with_one()
    out = store.export(tmp_path)
    assert SECRET not in out.read_text()
    assert store.read_backup(out, PASS)[0]["password"] == SECRET
    with pytest.raises(store.VaultError):
        store.read_backup(out, "wrong password here")


def test_vault_never_goes_into_a_normal_backup():
    from plugins.backup import make_backup
    _vault_with_one()
    store.export(config.OUTPUTS_DIR / "vault-backups")
    names = zipfile.ZipFile(make_backup()).namelist()
    assert not any("vault" in n or "machine.key" in n for n in names)


def test_routes_setup_twice_typed_reveal_copy_and_pc_only(monkeypatch):
    c = _client()
    assert c.post("/api/plugins/passwords/setup", json={"passcode": PASS, "again": "different"}).status_code == 400
    r = c.post("/api/plugins/passwords/setup", json={"passcode": PASS, "again": PASS}).json()
    assert r["unlocked"] and len(r["recovery_key"]) == 39
    r = c.post("/api/plugins/passwords/entries", json={"site": "X", "app_id": "x", "username": "ali", "password": SECRET})
    eid = r.json()["id"]
    assert r.headers["cache-control"] == "no-store" and SECRET not in r.text
    r = c.post(f"/api/plugins/passwords/entries/{eid}/copy", json={"field": "password"})
    assert r.json() == {"copied": "pc", "clear_after": 20} and SECRET not in r.text and COPIED == [SECRET]
    shown = c.post(f"/api/plugins/passwords/entries/{eid}/reveal").json()
    assert shown == {"value": SECRET, "hide_after": 10}
    audit = (config.DB_PATH.parent / "logs" / "audit.log").read_text()
    assert "revealed the password of login" in audit and SECRET not in audit
    assert c.get("/api/plugins/passwords/generate").json()["password"]
    monkeypatch.setattr(config, "PASSCODE", "phone-passcode-123")
    phone = _client(here=False)
    phone.post("/api/login", json={"passcode": "phone-passcode-123"})
    assert phone.get("/api/plugins/passwords/status").json() == {"here": False, "message": "Vault works on this PC only."}
    assert phone.post(f"/api/plugins/passwords/entries/{eid}/reveal").status_code == 403


def test_reveals_are_rate_limited():
    eid, _ = _vault_with_one()
    for _ in range(store.REVEALS_PER_MINUTE):
        store.reveal(eid)
    with pytest.raises(store.VaultError, match="Too many reveals"):
        store.reveal(eid)


def test_errors_never_echo_a_secret():
    c = _client()
    c.post("/api/plugins/passwords/setup", json={"passcode": PASS, "again": PASS})
    r = c.post("/api/plugins/passwords/entries", json={"site": "X", "password": "S3cret" * 100})
    assert r.status_code == 422 and "S3cret" not in r.text
    r = c.post("/api/plugins/passwords/unlock", json={"passcode": "Wr0ng-Guess-Here"})
    assert "Wr0ng" not in r.text


def test_the_ai_never_sees_the_vault():
    from airtac import plugins, tools
    assert not plugins.get("passwords").tools
    assert not any("password" in t.name for t in tools.REGISTRY.values() if t.plugin == "passwords")
