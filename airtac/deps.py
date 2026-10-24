"""Which packages from requirements.txt aren't installed (Ali, 18 Oct: the vault failed because argon2-cffi was
missing and nothing said so). Shown in /api/health and on the page, with the one fix: Install-AIRTAC.cmd."""
import re
from importlib import metadata
from pathlib import Path

from airtac import config

_cache: dict = {}


def missing(path: Path | None = None) -> list[str]:
    path = path or config.ROOT / "requirements.txt"
    key = (str(path), path.stat().st_mtime if path.exists() else 0)
    if _cache.get("key") == key:
        return _cache["missing"]
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(==|>=|~=|$)", line.split("#")[0])
        if not m or not m.group(1):
            continue
        try:
            metadata.version(m.group(1))
        except metadata.PackageNotFoundError:
            out.append(m.group(1))
    _cache.update(key=key, missing=out)
    return out
