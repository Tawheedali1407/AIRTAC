"""Background jobs: the part of AIRTAC that works while you're not looking.

Plugins register jobs with @p.job(seconds). A job may return notifications
[{"title", "body", "spoken"}]; they're saved (the page shows and reads them out) and, on
Windows with the winotify package installed, also shown as a Windows notification.
One thread, checked every 15 seconds: light enough for an old laptop.
"""
import importlib.util
import logging
import sys
import threading
import time
from datetime import datetime

from airtac import config, memory, plugins
from airtac.plugin_api import CURRENT

log = logging.getLogger("airtac.scheduler")
TICK_SECONDS = 15


def toast(title: str, body: str) -> None:
    """A Windows notification, when possible. Silent everywhere else."""
    if sys.platform != "win32" or importlib.util.find_spec("winotify") is None:
        return
    try:
        from winotify import Notification
        icon = config.ROOT / "web" / "icons" / "airtac-192.png"
        Notification(app_id="AIRTAC", title=title, msg=body[:250], icon=str(icon) if icon.exists() else "").show()
    except Exception:
        log.exception("Windows notification failed")


class Scheduler:
    def __init__(self):
        self.last: dict[tuple, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self, now: datetime | None = None, clock: float | None = None) -> int:
        """Run every job that is due. Returns how many notifications were made."""
        now = now or datetime.now()
        clock = time.monotonic() if clock is None else clock
        made = 0
        for p in plugins.enabled():
            for every, fn in p.jobs:
                key = (p.id, fn.__name__)
                if clock - self.last.get(key, -1e9) < every:
                    continue
                self.last[key] = clock
                token = CURRENT.set(p.id)
                try:
                    out = fn(now) or []
                except Exception:
                    log.exception("job %s.%s failed", p.id, fn.__name__)
                    out = []
                finally:
                    CURRENT.reset(token)
                for n in out:
                    memory.notify(n["title"], n.get("body", ""), n.get("spoken", ""), p.id)
                    toast(n["title"], n.get("body", ""))
                    made += 1
        return made

    def _run(self):
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="airtac-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


SCHEDULER = Scheduler()
