"""Repairs pip inside AIRTAC's .venv when it is half-upgraded (Ali's PC, 18 Oct: "cannot import name
'get_runnable_pip'"), using the pip wheel that ships with Python itself, so the broken copy isn't needed.
    .venv\\Scripts\\python scripts\\repair_pip.py

A plain reinstall isn't enough: pip's uninstall removes only the files it recorded, so leftovers from the failed
upgrade (a "~ip" folder, stray modules like pip/_internal/build_env/) stay and keep breaking it. So the old pip
folders are MOVED (never deleted) into backups/pip-broken-<time>/ first, then pip is installed fresh.
"""
import glob
import os
import shutil
import subprocess  # nosec B404 (fixed commands)
import sys
import sysconfig
import time

import ensurepip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _works() -> bool:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.call([sys.executable, "-m", "pip", "--version"], env=env,  # noqa: S603  # nosec B603
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def _move_old_pip(site: str) -> str | None:
    old = sorted({p for pat in ("pip", "pip-*.dist-info", "~ip*") for p in glob.glob(os.path.join(site, pat))})
    if not old:
        return None
    dest = os.path.join(ROOT, "backups", "pip-broken-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dest, exist_ok=True)
    for p in old:
        if os.path.exists(p):
            shutil.move(p, os.path.join(dest, os.path.basename(p)))
    return dest


def main() -> int:
    bundled = os.path.join(os.path.dirname(ensurepip.__file__), "_bundled")
    wheels = sorted(glob.glob(os.path.join(bundled, "pip-*.whl")))
    if not wheels:
        print("Python's own pip wheel wasn't found; reinstall Python 3.14 with pip ticked.")
        return 1
    whl = wheels[-1]
    site = sysconfig.get_paths()["purelib"]
    moved = _move_old_pip(site)
    if moved:
        print(f"Moved the broken pip to {moved} (nothing deleted).")
    print(f"Installing pip from {os.path.basename(whl)} ...")
    # The working copy inside the wheel runs (it's first on the path), so the broken one isn't needed.
    env = {**os.environ, "PYTHONPATH": whl + os.pathsep + os.environ.get("PYTHONPATH", "")}
    code = subprocess.call([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-index",  # noqa: S603  # nosec B603
                            "--find-links", bundled, "pip"], env=env)
    if code == 0 and _works():
        print("pip works again.")
        return 0
    print("pip is still broken. Delete the .venv folder and run Install-AIRTAC.cmd to make a fresh one.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
