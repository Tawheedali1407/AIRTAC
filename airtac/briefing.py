"""Your day, as AIRTAC says it when you open it (and in the morning notification).

The core only adds the greeting. Each plugin can add a part with a @p.briefing hook:
the schedule plugin says what's overdue, today and next; others could add a contest
starting tonight or the LeetCode daily. Parts come back in order, as screen text and as
spoken text (full day names, "2 pm"), because voices read "Sun 15 Nov" badly.
"""
import logging
from datetime import datetime

from airtac import config, plugins
from airtac.plugin_api import CURRENT

log = logging.getLogger("airtac.briefing")


def greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 22:
        return "Good evening"
    return "Hello"


def build(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    name = f", {config.USER_NAME}" if config.USER_NAME else ""
    hello = f"{greeting(now.hour)}{name}."
    from airtac import dates
    clock = f"It's {dates.spoken_time(now.strftime('%H:%M'))} on {now.strftime('%A')} {now.day} {now.strftime('%B')}."
    parts, data, sample = [], {}, False
    for p in plugins.enabled():
        hook = p.hooks.get("briefing")
        if not hook:
            continue
        token = CURRENT.set(p.id)
        try:
            part = hook(now)
        except Exception:  # one plugin's problem shouldn't silence the whole briefing
            log.exception("briefing from %s failed", p.id)
            part = None
        finally:
            CURRENT.reset(token)
        if part:
            parts.append(part)
            data.update(part.get("data", {}))
            sample = sample or bool(part.get("sample"))
    parts.sort(key=lambda x: x.get("order", 50))
    text = " ".join([hello, clock] + [x["screen"] for x in parts if x.get("screen")])
    spoken = " ".join([hello, clock] + [x.get("spoken") or x["screen"] for x in parts if x.get("screen")])
    return {"text": text, "spoken": spoken, "date": now.date().isoformat(), "sample": sample, **data}
