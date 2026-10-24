"""Guest mode's reset: the public try-it copy (AIRTAC_GUEST=1) is shared by everyone who opens it, so whatever
visitors type (chats, schedule items, notes, people) is wiped every config.GUEST_RESET_MINUTES. Never runs on your PC.

Deleting the database file is enough: every request opens a fresh connection, and the next one creates the tables
again. The install token and the logs stay, so open pages keep working through a reset.
"""
import logging
import shutil
import threading

from airtac import config

log = logging.getLogger("airtac.guest")
KEEP = {".install_token", "logs"}
_stop = threading.Event()


def wipe() -> int:
    """Delete everything visitors could have made. Returns how many files and folders went."""
    gone = 0
    for folder in (config.DB_PATH.parent, config.OUTPUTS_DIR, config.WORKSPACE_DIR):
        if not folder.exists():
            continue
        for item in folder.iterdir():
            if item.name in KEEP:
                continue
            try:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
                gone += 1
            except OSError:
                log.warning("guest reset couldn't remove %s", item.name)
    return gone


def _loop() -> None:
    while not _stop.wait(max(5, config.GUEST_RESET_MINUTES) * 60):
        log.info("guest reset: removed %d items", wipe())


def start() -> None:
    if not config.GUEST:
        return
    wipe()   # every start is a clean copy too
    threading.Thread(target=_loop, name="airtac-guest-reset", daemon=True).start()
