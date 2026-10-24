"""Tokens: counts what every brain used, and keeps the cloud ones inside the limits you set.

A token is a piece of a word (about 4 characters). Cloud brains count your free requests and paid
credits in tokens, so this plugin:
- counts the tokens of every answer, per brain and per day (the @p.usage hook);
- gives each cloud brain a daily limit. Once a brain reaches it, AIRTAC skips that brain until
  midnight and the next one answers (the @p.brain_gate hook); your PC (Ollama) is never skipped;
- sets the answer length for every brain: Short (saves the most), Normal or Long;
- warns you once a day when a brain passes 80% of its limit.
Change the limits on the Tokens tab of the side panel, or the Tokens app page. Counts stay on this PC.
"""
import json
from datetime import date, timedelta

from airtac import db, memory
from airtac.plugin_api import Tool, ToolResult, local_only, plugin, schema

p = plugin(__name__)
p.sql("""
CREATE TABLE IF NOT EXISTS token_usage (
    day TEXT NOT NULL,
    tier TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    prompt INTEGER NOT NULL DEFAULT 0,
    completion INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, tier, model)
);
""")

LENGTHS = {"short": 300, "normal": None, "long": 1400}     # None = what AIRTAC asks for (usually 600)
DEFAULTS = {"length": "normal", "limits": {"nebius": 100000}}   # Nebius is paid credits: a safe default
WARN_AT = 0.8
PAID_DEFAULT = 100000      # tokens a day for a paid brain until you change it


def _saved() -> dict:
    try:
        saved = json.loads(memory.kv_get("tokens", "settings") or "{}")
    except ValueError:
        saved = {}
    return saved if isinstance(saved, dict) else {}


def _default_limits() -> dict:
    """Every paid brain (and Perplexity) starts with a daily limit, so adding a key can't run up a bill."""
    from airtac import providers
    paid = {name: PAID_DEFAULT for name, prov in providers.PROVIDERS.items() if prov.paid}
    return {**paid, "perplexity": 50000, **DEFAULTS["limits"]}


def settings() -> dict:
    """{"length": "normal", "limits": {tier: tokens a day}}. A limit of 0 means no limit."""
    saved = _saved()
    length = saved.get("length") if saved.get("length") in LENGTHS else DEFAULTS["length"]
    return {"length": length, "limits": {**_default_limits(), **saved.get("limits", {})}}


def save_settings(length: str | None = None, limits: dict | None = None) -> dict:
    saved = _saved()
    if length is not None:
        if length not in LENGTHS:
            raise ValueError("Answer length is short, normal or long.")
        saved["length"] = length
    if limits is not None:
        merged = dict(saved.get("limits", {}))
        for tier, value in limits.items():
            value = int(value or 0)
            if value < 0 or value > 100_000_000:
                raise ValueError("A daily limit is a number of tokens: 0 means no limit.")
            merged[str(tier)[:40]] = value
        saved["limits"] = merged
    memory.kv_set("tokens", "settings", json.dumps(saved))
    return settings()


def limit_for(tier: str) -> int:
    return int(settings()["limits"].get(tier, 0) or 0)


def today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------- counting (hook)

@p.usage
def seen(tier: str, model: str, prompt: int, completion: int) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO token_usage (day, tier, model, prompt, completion, calls) VALUES (?, ?, ?, ?, ?, 1) "
                     "ON CONFLICT(day, tier, model) DO UPDATE SET prompt = prompt + excluded.prompt, "
                     "completion = completion + excluded.completion, calls = calls + 1",
                     (today(), tier, model or "", max(0, prompt), max(0, completion)))
    _maybe_warn(tier)


def used(tier: str, day: str | None = None) -> int:
    with db.connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(prompt + completion), 0) FROM token_usage WHERE day = ? AND tier = ?",
                           (day or today(), tier)).fetchone()
    return int(row[0])


def _maybe_warn(tier: str) -> None:
    limit = limit_for(tier)
    if tier == "ollama" or not limit:
        return
    n = used(tier)
    for level, key in ((1.0, "full"), (WARN_AT, "warn")):
        flag = f"{key}:{today()}:{tier}"
        if n >= limit * level and not memory.kv_get("tokens", flag):
            memory.kv_set("tokens", flag, "1")
            label = _label(tier)
            if key == "full":
                memory.notify(f"{label} reached today's token limit",
                              f"{n:,} of {limit:,}. The next brain answers until midnight.", plugin="tokens")
            else:
                memory.notify(f"{label} used 80% of today's tokens", f"{n:,} of {limit:,}.", plugin="tokens")
            break


# ---------------------------------------------------------------- limits (hook)

@p.brain_gate
def gate(tier: str, max_tokens: int) -> int | None:
    limit = limit_for(tier)
    if tier != "ollama" and limit and used(tier) >= limit:
        return None                                   # skip this cloud brain until midnight
    choice = settings()["length"]
    if choice == "short":
        return min(max_tokens, LENGTHS["short"])     # never longer than Short
    if choice == "long":
        return max(max_tokens, LENGTHS["long"])      # room for long answers
    return max_tokens


# ---------------------------------------------------------------- what the page and the chat see

def _label(tier: str) -> str:
    from airtac import providers
    if tier == "ollama":
        return "Your PC (Ollama)"
    p_ = providers.PROVIDERS.get(tier)
    return p_.label if p_ else tier.capitalize()


def _tiers() -> list[str]:
    from airtac import providers
    names = [r["tier"] for r in providers.status()]
    with db.connect() as conn:
        names += [r[0] for r in conn.execute("SELECT DISTINCT tier FROM token_usage")]
    return list(dict.fromkeys(names))


def summary() -> dict:
    day0 = date.today()
    week = [(day0 - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    with db.connect() as conn:
        rows = conn.execute("SELECT day, tier, SUM(prompt), SUM(completion), SUM(calls) FROM token_usage "
                            "WHERE day >= ? GROUP BY day, tier", (week[0],)).fetchall()
    by_day = {d: 0 for d in week}
    today_rows = {}
    for d, tier, pr, co, calls in rows:
        by_day[d] = by_day.get(d, 0) + pr + co
        if d == today():
            today_rows[tier] = {"prompt": pr, "completion": co, "calls": calls}
    s = settings()
    tiers = []
    for t in _tiers():
        r = today_rows.get(t, {"prompt": 0, "completion": 0, "calls": 0})
        lim = limit_for(t)
        n = r["prompt"] + r["completion"]
        tiers.append({"tier": t, "label": _label(t), "used": n, "calls": r["calls"], "limit": lim,
                      "left": max(0, lim - n) if lim else None, "paused": bool(lim and n >= lim and t != "ollama"),
                      "local": t == "ollama"})
    total = sum(t["used"] for t in tiers)
    return {"today": total, "cloud_today": sum(t["used"] for t in tiers if not t["local"]),
            "week": [{"day": d, "tokens": by_day[d]} for d in week], "week_total": sum(by_day.values()),
            "tiers": tiers, "length": s["length"], "lengths": {"short": "Short", "normal": "Normal", "long": "Long"}}


def token_usage() -> ToolResult:
    s = summary()
    used_ = [t for t in s["tiers"] if t["used"]]
    if not used_:
        return ToolResult("No tokens used yet today.", f"This week: {s['week_total']:,} tokens.")
    parts = ", ".join(f"{t['label'].split(' (')[0]} {t['used']:,}" for t in used_[:3])
    spoken = f"Today you used {s['today']:,} tokens: {parts}."
    paused = [t["label"].split(" (")[0] for t in s["tiers"] if t["paused"]]
    if paused:
        spoken += f" {', '.join(paused)} reached today's limit."
    lines = ["Tokens today"] + [
        f"- {t['label']}: {t['used']:,} in {t['calls']} answer{'s' if t['calls'] != 1 else ''}"
        + (f" (limit {t['limit']:,}, {t['left']:,} left)" if t["limit"] else "") for t in s["tiers"] if t["used"] or t["limit"]]
    lines.append(f"This week: {s['week_total']:,} tokens. Answer length: {s['length']}.")
    return ToolResult(spoken, "\n".join(lines))


p.add(Tool("token_usage", "How many AI tokens each brain used today and this week, and the daily limits.",
           schema(), "local", token_usage))
p.mock_rule(30, ("token", "tokens", "token usage"), "token_usage")


@p.dashboard
def page(pg: dict) -> None:
    s = summary()
    pg["stats"] = [{"label": "Tokens today", "value": f"{s['today']:,}"},
                   {"label": "In the cloud today", "value": f"{s['cloud_today']:,}"},
                   {"label": "This week", "value": f"{s['week_total']:,}"}]
    limited = [t for t in s["tiers"] if t["limit"]]
    if limited:
        pg["bars"] = {"title": "Today against each daily limit", "kind": "progress",
                      "items": [{"label": t["label"], "value": t["used"], "total": t["limit"]} for t in limited]}
    pg["lists"] = [{"title": "Last 7 days", "items": [{"title": d["day"], "meta": f"{d['tokens']:,} tokens"} for d in reversed(s["week"])],
                    "empty": "Nothing yet."}]


@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Request
    from pydantic import BaseModel

    class Settings(BaseModel):
        length: str | None = None
        limits: dict[str, int] | None = None

    @router.get("/summary")
    def get_summary():
        return summary()

    @router.post("/settings")
    def set_settings(body: Settings, request: Request):
        local_only(request)
        try:
            save_settings(body.length, body.limits)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return summary()
