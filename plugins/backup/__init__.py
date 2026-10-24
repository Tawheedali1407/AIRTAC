"""Backup: a zip of your AIRTAC data, kept on this laptop and, if you want, copied to your own
cloud drive with rclone (open source; works with Google Drive, OneDrive, Dropbox, Mega and more).

What goes in: the database (memory, schedule, chats, lessons; copied safely while AIRTAC runs),
config/airtac.toml, and if you choose, your knowledge library and the files AIRTAC made.
What never goes in: .env (your keys), the password vault (data/vault.db and its exports) and the
downloaded models (you can pull them again).

Cloud copy: run `rclone config` once to add a remote (make it a "crypt" remote and the cloud
only ever sees encrypted files), then set [plugins.backup] remote = "mycrypt:AIRTAC".
Sending a backup to the cloud waits for your Confirm unless you turn on auto_upload.
"""
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime, time, timedelta
from pathlib import Path

from airtac import config, memory
from airtac.plugin_api import Tool, ToolResult, plugin, schema

p = plugin(__name__)


def folder() -> Path:
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return config.BACKUP_DIR


def make_backup(now: datetime | None = None) -> Path:
    now = now or datetime.now()
    path = folder() / f"airtac-backup-{now:%Y%m%d-%H%M%S}.zip"
    snapshot = folder() / ".db-snapshot.db"
    if config.DB_PATH.exists():  # SQLite's own backup: a consistent copy even while AIRTAC is writing
        src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if snapshot.exists():
            z.write(snapshot, "data/airtac.db")
        cfg = config.CONFIG_PATH
        if cfg.exists():
            z.write(cfg, "config/airtac.toml")
        extra = [("knowledge", config.KNOWLEDGE_DIR)] if p.setting("include_knowledge", False) else []
        extra += [("outputs", config.OUTPUTS_DIR)] if p.setting("include_outputs", True) else []
        for name, root in extra:
            if root.exists():
                for f in root.rglob("*"):
                    # The password vault's own encrypted copies never go into a backup (see plugins/passwords).
                    if f.is_file() and "vault-backups" not in f.relative_to(root).parts and f.suffix != ".airtacvault":
                        z.write(f, f"{name}/{f.relative_to(root).as_posix()}")
    snapshot.unlink(missing_ok=True)
    keep = int(p.setting("keep", 7) or 7)
    for old in sorted(folder().glob("airtac-backup-*.zip"))[:-keep]:
        old.unlink()
    return path


def rclone() -> str | None:
    return shutil.which("rclone")


def upload(path: Path) -> tuple[bool, str]:
    remote = (p.setting("remote", "") or "").strip()
    if not remote:
        return False, "No cloud remote is set. Add [plugins.backup] remote in config/airtac.toml."
    if not rclone():
        return False, "rclone isn't installed. Get it from rclone.org, run rclone config, then try again."
    try:
        done = subprocess.run([rclone(), "copy", str(path), remote], capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "The upload took too long, so I stopped it."
    if done.returncode != 0:
        return False, "rclone couldn't upload it: " + (done.stderr.strip().splitlines() or ["unknown error"])[-1][:200]
    memory.kv_set(p.id, "last_upload", datetime.now().isoformat(timespec="minutes"))
    return True, f"Uploaded {path.name} to {remote}."


def backup_now() -> ToolResult:
    path = make_backup()
    size = path.stat().st_size / 1_000_000
    memory.kv_set(p.id, "last_backup", datetime.now().isoformat(timespec="minutes"))
    return ToolResult(f"Backed up your AIRTAC data ({size:.1f} MB). It's in the backups folder.",
                      f"Saved {path}\nKeys in .env and downloaded models are never included.")


def backup_upload() -> ToolResult:
    latest = sorted(folder().glob("airtac-backup-*.zip"))
    path = latest[-1] if latest else make_backup()
    ok, message = upload(path)
    return ToolResult(message, message, ok=ok)


p.add(Tool("backup_now", "Make a backup zip of the user's AIRTAC data (memory, schedule, chats, settings) on this laptop.",
           schema(), "local", backup_now))
p.add(Tool("backup_upload", "Copy the newest backup to the user's own cloud drive with rclone (asks the user first).",
           schema(), "write", backup_upload, summarize=lambda a: f"upload your newest backup to {p.setting('remote', 'the cloud') or 'the cloud'}"))


@p.job(300)
def nightly(now: datetime) -> list[dict]:
    """Once a day at [plugins.backup] time (default 22:00): a local backup, and the cloud copy if auto_upload is on."""
    at = str(p.setting("time", "22:00") or "")
    if not at or memory.kv_get(p.id, "auto_day") == now.date().isoformat():
        return []
    start = datetime.combine(now.date(), time.fromisoformat(at))
    if not start <= now < start + timedelta(hours=2):
        return []
    memory.kv_set(p.id, "auto_day", now.date().isoformat())
    path = make_backup(now)
    memory.kv_set(p.id, "last_backup", now.isoformat(timespec="minutes"))
    if p.setting("auto_upload", False):
        ok, message = upload(path)
        if not ok:
            return [{"title": "Cloud backup didn't work", "body": message, "spoken": ""}]
    return []


@p.dashboard
def page(pg: dict) -> None:
    zips = sorted(folder().glob("airtac-backup-*.zip"), reverse=True)
    pg["stats"] = [{"label": "Backups kept", "value": len(zips)},
                   {"label": "Last backup", "value": memory.kv_get(p.id, "last_backup") or "never"},
                   {"label": "Cloud copy", "value": memory.kv_get(p.id, "last_upload") or
                    ("not set up" if not p.setting("remote") else "never")}]
    pg["lists"] = [{"title": "On this laptop", "empty": "No backups yet. Say: back up my data.",
                    "items": [{"title": z.name, "meta": f"{z.stat().st_size / 1_000_000:.1f} MB"} for z in zips[:7]]}]


p.mock_rule(27.5, ("back up", "backup"), "backup_now")
p.mock_rule(27.4, ("upload my backup", "backup to the cloud", "back up to the cloud", "cloud backup"), "backup_upload")
