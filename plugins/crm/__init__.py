"""People and pipeline: the schedule as a CRM (Ali, 18 Oct: "schedule should be like CRM").

- People: contacts (a person or an organisation) with role, company, email, phone, tags and notes.
- Board: items (an internship application, a project, a client, an event to plan) in stages you name
  (default: Lead, Contacted, In progress, Waiting, Done); drag a card to move it.
- Follow-ups: a date (and time) to get back to a person or item. It goes on your schedule (category
  "follow-up"), so the greeting announces it and it shows on the calendar.
- History: every note, call, meeting, email, stage change and follow-up is logged for the person and the item.

Everything stays in AIRTAC's database on this PC. The chat can look someone up, log a note and set a follow-up
(changes inside AIRTAC only, so no Confirm is needed, like adding to your schedule).
"""
import json
import re
from datetime import date

from airtac import db, dates, memory
from airtac.plugin_api import Tool, ToolResult, plugin, schema

p = plugin(__name__)

p.sql("""
CREATE TABLE IF NOT EXISTS crm_contacts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'person',
    org TEXT DEFAULT '',
    role TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS crm_items (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    contact_id INTEGER,
    stage TEXT NOT NULL,
    value TEXT DEFAULT '',
    due TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    position REAL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS crm_activity (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER,
    item_id INTEGER,
    kind TEXT NOT NULL,
    text TEXT DEFAULT '',
    at TEXT DEFAULT CURRENT_TIMESTAMP
);
""")

DEFAULT_STAGES = ["Lead", "Contacted", "In progress", "Waiting", "Done"]
KINDS = ("note", "call", "meeting", "email", "message")
CONTACT_FIELDS = ("name", "kind", "org", "role", "email", "phone", "tags", "notes")
ITEM_FIELDS = ("title", "contact_id", "stage", "value", "due", "notes")


# ---------------------------------------------------------------- stages

def stages() -> list[str]:
    try:
        found = json.loads(memory.kv_get("crm", "stages") or "null")
    except ValueError:
        found = None
    return found if isinstance(found, list) and found else list(DEFAULT_STAGES)


def set_stages(names: list[str]) -> list[str]:
    clean, seen = [], set()
    for n in names:
        n = " ".join(str(n).split())[:30]
        if n and n.lower() not in seen:
            seen.add(n.lower())
            clean.append(n)
    if not 2 <= len(clean) <= 10:
        raise ValueError("Keep between 2 and 10 stages.")
    old = stages()
    with db.connect() as conn:   # items in a removed stage move to the first one, never lost
        for s in old:
            if s not in clean:
                conn.execute("UPDATE crm_items SET stage = ? WHERE stage = ?", (clean[0], s))
    memory.kv_set("crm", "stages", json.dumps(clean))
    return clean


# ---------------------------------------------------------------- people

def _text(value, n: int) -> str:
    return " ".join(str(value or "").split())[:n] if n <= 200 else str(value or "").strip()[:n]


def _contact_values(body: dict) -> dict:
    out = {"name": _text(body.get("name"), 80), "kind": "org" if body.get("kind") == "org" else "person",
           "org": _text(body.get("org"), 80), "role": _text(body.get("role"), 80), "email": _text(body.get("email"), 120),
           "phone": _text(body.get("phone"), 40), "tags": _text(body.get("tags"), 120), "notes": _text(body.get("notes"), 2000)}
    if len(out["name"]) < 2:
        raise ValueError("Give the contact a name.")
    if out["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", out["email"]):
        raise ValueError("That email address doesn't look right.")
    return out


def add_contact(body: dict) -> int:
    v = _contact_values(body)
    with db.connect() as conn:
        cid = conn.execute(f"INSERT INTO crm_contacts ({', '.join(v)}) VALUES ({', '.join('?' * len(v))})",  # nosec B608 (fixed names)
                           tuple(v.values())).lastrowid
    log(contact_id=cid, kind="created", text=f"Added {v['name']}")
    return cid


def update_contact(cid: int, body: dict) -> bool:
    v = _contact_values(body)
    with db.connect() as conn:
        n = conn.execute(f"UPDATE crm_contacts SET {', '.join(f'{k} = ?' for k in v)}, updated_at = CURRENT_TIMESTAMP "  # nosec B608 (fixed names)
                         "WHERE id = ?", (*v.values(), cid)).rowcount
    return bool(n)


def delete_contact(cid: int) -> bool:
    """Only when you press Delete. Their items stay on the board (without a person); their history goes."""
    with db.connect() as conn:
        n = conn.execute("DELETE FROM crm_contacts WHERE id = ?", (cid,)).rowcount
        conn.execute("UPDATE crm_items SET contact_id = NULL WHERE contact_id = ?", (cid,))
        conn.execute("DELETE FROM crm_activity WHERE contact_id = ? AND item_id IS NULL", (cid,))
    return bool(n)


def contacts(q: str = "") -> list[dict]:
    sql, args = "SELECT * FROM crm_contacts", []
    words = q.split()[:5]
    if words:
        sql += " WHERE " + " AND ".join("(name || ' ' || org || ' ' || role || ' ' || tags || ' ' || email) LIKE ?" for _ in words)
        args = [f"%{w}%" for w in words]
    sql += " ORDER BY updated_at DESC, id DESC LIMIT 500"
    with db.connect() as conn:
        rows = db.rows(conn.execute(sql, args))  # nosec B608 (fixed text, placeholders only)
    nxt = _next_follow_ups()
    for r in rows:
        r["follow_up"] = nxt.get(("contact", r["id"]))
        r["last"] = _last_activity(contact_id=r["id"])
    return rows


def contact(cid: int) -> dict | None:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT * FROM crm_contacts WHERE id = ?", (cid,)))
        if not found:
            return None
        c = found[0]
        c["items"] = db.rows(conn.execute("SELECT * FROM crm_items WHERE contact_id = ? ORDER BY updated_at DESC", (cid,)))
        c["history"] = db.rows(conn.execute("SELECT * FROM crm_activity WHERE contact_id = ? ORDER BY id DESC LIMIT 200", (cid,)))
    c["follow_ups"] = follow_ups(contact_id=cid)
    return c


def find_contact(name: str) -> dict | None:
    name = " ".join((name or "").split())
    if len(name) < 2:
        return None
    with db.connect() as conn:
        for sql, arg in (("SELECT * FROM crm_contacts WHERE lower(name) = lower(?)", name),
                         ("SELECT * FROM crm_contacts WHERE name LIKE ? ORDER BY updated_at DESC", f"{name}%"),
                         ("SELECT * FROM crm_contacts WHERE name LIKE ? OR org LIKE ? ORDER BY updated_at DESC", f"%{name}%")):
            args = (arg, arg) if sql.count("?") == 2 else (arg,)
            found = db.rows(conn.execute(sql, args))
            if found:
                return found[0]
    return None


# ---------------------------------------------------------------- the board

def _item_values(body: dict) -> dict:
    v = {"title": _text(body.get("title"), 120), "stage": body.get("stage") if body.get("stage") in stages() else stages()[0],
         "value": _text(body.get("value"), 60), "due": "", "notes": _text(body.get("notes"), 2000), "contact_id": None}
    if len(v["title"]) < 2:
        raise ValueError("Give the item a title.")
    if body.get("due"):
        try:
            v["due"] = date.fromisoformat(str(body["due"])).isoformat()
        except ValueError:
            raise ValueError("The due date should look like 2026-11-14.")
    if body.get("contact_id"):
        with db.connect() as conn:
            if conn.execute("SELECT 1 FROM crm_contacts WHERE id = ?", (int(body["contact_id"]),)).fetchone():
                v["contact_id"] = int(body["contact_id"])
    return v


def add_item(body: dict) -> int:
    v = _item_values(body)
    with db.connect() as conn:
        top = conn.execute("SELECT COALESCE(MIN(position), 0) FROM crm_items WHERE stage = ?", (v["stage"],)).fetchone()[0]
        iid = conn.execute("INSERT INTO crm_items (title, contact_id, stage, value, due, notes, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (v["title"], v["contact_id"], v["stage"], v["value"], v["due"], v["notes"], top - 1)).lastrowid
    log(item_id=iid, contact_id=v["contact_id"], kind="created", text=f"Added {v['title']} in {v['stage']}")
    return iid


def update_item(iid: int, body: dict) -> bool:
    old = item(iid)
    if not old:
        return False
    v = _item_values({**old, **body})
    with db.connect() as conn:
        conn.execute("UPDATE crm_items SET title = ?, contact_id = ?, stage = ?, value = ?, due = ?, notes = ?, "
                     "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (v["title"], v["contact_id"], v["stage"], v["value"], v["due"], v["notes"], iid))
    if v["stage"] != old["stage"]:
        log(item_id=iid, contact_id=v["contact_id"], kind="stage", text=f"{old['stage']} → {v['stage']}")
    return True


def move_item(iid: int, stage: str, before: int | None = None) -> bool:
    """Drag and drop: put the card in a stage, above the card `before` (or at the bottom)."""
    old = item(iid)
    if not old or stage not in stages():
        return False
    with db.connect() as conn:
        order = [r[0] for r in conn.execute("SELECT id FROM crm_items WHERE stage = ? AND id != ? ORDER BY position, id DESC",
                                            (stage, iid))]
        at = order.index(before) if before in order else len(order)
        order.insert(at, iid)
        for n, x in enumerate(order):   # renumber the column: simple, and a column is never long
            conn.execute("UPDATE crm_items SET position = ? WHERE id = ?", (n, x))
        conn.execute("UPDATE crm_items SET stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (stage, iid))
    if stage != old["stage"]:
        log(item_id=iid, contact_id=old["contact_id"], kind="stage", text=f"{old['stage']} → {stage}")
    return True


def delete_item(iid: int) -> bool:
    with db.connect() as conn:
        n = conn.execute("DELETE FROM crm_items WHERE id = ?", (iid,)).rowcount
        conn.execute("DELETE FROM crm_activity WHERE item_id = ?", (iid,))
    return bool(n)


def item(iid: int) -> dict | None:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT * FROM crm_items WHERE id = ?", (iid,)))
    return found[0] if found else None


def item_detail(iid: int) -> dict | None:
    it = item(iid)
    if not it:
        return None
    with db.connect() as conn:
        it["history"] = db.rows(conn.execute("SELECT * FROM crm_activity WHERE item_id = ? ORDER BY id DESC LIMIT 200", (iid,)))
    it["contact"] = contact_name(it["contact_id"])
    it["follow_ups"] = follow_ups(item_id=iid)
    return it


def contact_name(cid) -> str:
    if not cid:
        return ""
    with db.connect() as conn:
        row = conn.execute("SELECT name FROM crm_contacts WHERE id = ?", (cid,)).fetchone()
    return row[0] if row else ""


def board() -> dict:
    with db.connect() as conn:
        rows = db.rows(conn.execute("SELECT i.*, c.name AS contact FROM crm_items i LEFT JOIN crm_contacts c ON c.id = i.contact_id "
                                    "ORDER BY i.position, i.id DESC"))
    nxt = _next_follow_ups()
    names = stages()
    cols = {s: [] for s in names}
    for r in rows:
        r["follow_up"] = nxt.get(("item", r["id"]))
        cols.setdefault(r["stage"], []).append(r)
    return {"stages": names, "columns": [{"stage": s, "items": cols.get(s, [])} for s in names]}


# ---------------------------------------------------------------- history and follow-ups

def log(contact_id=None, item_id=None, kind: str = "note", text: str = "") -> int:
    with db.connect() as conn:
        aid = conn.execute("INSERT INTO crm_activity (contact_id, item_id, kind, text) VALUES (?, ?, ?, ?)",
                           (contact_id, item_id, kind[:20], str(text).strip()[:2000])).lastrowid
        if contact_id:
            conn.execute("UPDATE crm_contacts SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (contact_id,))
        if item_id:
            conn.execute("UPDATE crm_items SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (item_id,))
    return aid


def _last_activity(contact_id: int) -> dict | None:
    with db.connect() as conn:
        found = db.rows(conn.execute("SELECT kind, text, at FROM crm_activity WHERE contact_id = ? AND kind != 'created' "
                                     "ORDER BY id DESC LIMIT 1", (contact_id,)))
    return found[0] if found else None


def add_follow_up(when: str, time_: str = "", note: str = "", contact_id=None, item_id=None) -> dict:
    """A follow-up is a schedule event (category follow-up, source crm:contact:<id> or crm:item:<id>), so the
    greeting and the calendar know it."""
    from plugins.schedule import store
    d = date.fromisoformat(when)
    if item_id:
        it = item(item_id)
        if not it:
            raise ValueError("That item is gone.")
        who, source, contact_id = it["title"], f"crm:item:{item_id}", contact_id or it["contact_id"]
    elif contact_id:
        who = contact_name(contact_id)
        if not who:
            raise ValueError("That contact is gone.")
        source = f"crm:contact:{contact_id}"
    else:
        raise ValueError("Pick a person or an item to follow up on.")
    if time_ and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_):
        raise ValueError("The time should look like 14:30.")
    title = f"Follow up: {who}" + (f" ({note.strip()[:60]})" if note.strip() else "")
    eid = store.add_event(title, d.isoformat(), time_, "follow-up", note[:500], source)
    log(contact_id=contact_id, item_id=item_id, kind="follow-up", text=f"Follow-up set for {dates.nice(d, time_)}" + (f": {note}" if note else ""))
    return {"event_id": eid, "title": title, "date": d.isoformat(), "time": time_}


def follow_ups(contact_id=None, item_id=None, include_done: bool = False) -> list[dict]:
    sql = "SELECT id, title, date, time, notes, done, source FROM events WHERE category = 'follow-up' AND source LIKE 'crm:%'"
    args: list = []
    if contact_id:
        sql += " AND (source = ? OR source IN (SELECT 'crm:item:' || id FROM crm_items WHERE contact_id = ?))"
        args += [f"crm:contact:{contact_id}", contact_id]
    if item_id:
        sql += " AND source = ?"
        args.append(f"crm:item:{item_id}")
    if not include_done:
        sql += " AND done = 0"
    sql += " ORDER BY date, time LIMIT 200"
    try:
        with db.connect() as conn:
            rows = db.rows(conn.execute(sql, args))  # nosec B608 (fixed text, placeholders only)
    except Exception:     # the schedule plugin's table isn't there
        return []
    today = date.today().isoformat()
    for r in rows:
        r["overdue"] = r["date"] < today and not r["done"]
        kind, _, rid = r["source"][4:].partition(":")
        r["for"] = {"kind": kind, "id": int(rid) if rid.isdigit() else None}
    return rows


def _next_follow_ups() -> dict:
    nxt = {}
    for f in follow_ups():
        key = (f["for"]["kind"], f["for"]["id"])
        nxt.setdefault(key, {"date": f["date"], "time": f["time"], "overdue": f["overdue"], "event_id": f["id"]})
    return nxt


def follow_up_done(event_id: int) -> bool:
    from plugins.schedule import store
    with db.connect() as conn:
        row = conn.execute("SELECT source, title FROM events WHERE id = ? AND category = 'follow-up'", (event_id,)).fetchone()
    if not row:
        return False
    store.set_done(event_id, True)
    kind, _, rid = row[0][4:].partition(":")
    if rid.isdigit():
        it = item(int(rid)) if kind == "item" else None
        log(contact_id=int(rid) if kind == "contact" else (it or {}).get("contact_id"), item_id=int(rid) if kind == "item" else None,
            kind="follow-up", text=f"Done: {row[1]}")
    return True


# ---------------------------------------------------------------- the chat's tools

def crm_lookup(name: str) -> ToolResult:
    c = find_contact(name)
    if not c:
        return ToolResult(f"I don't have {name} in your contacts. Add them on the People and pipeline page.", ok=False)
    full = contact(c["id"])
    bits = [x for x in (c["role"], c["org"]) if x]
    last = next((h for h in full["history"] if h["kind"] != "created"), None)
    nxt = full["follow_ups"][0] if full["follow_ups"] else None
    said = f"{c['name']}{', ' + ' at '.join(bits) if bits else ''}."
    if last:
        said += f" Last: {last['text'][:120]}."
    if nxt:
        said += f" Follow up {dates.spoken_day(date.fromisoformat(nxt['date']))}."
    screen = [f"{c['name']} ({c['kind']})" + (f": {', '.join(bits)}" if bits else "")]
    screen += [f"Email: {c['email']}"] * bool(c["email"]) + [f"Phone: {c['phone']}"] * bool(c["phone"])
    screen += [f"Items: " + ", ".join(f"{i['title']} ({i['stage']})" for i in full["items"])] * bool(full["items"])
    screen += ["History:"] + [f"- {h['at'][:10]} {h['kind']}: {h['text']}" for h in full["history"][:8]]
    return ToolResult(said, screen="\n".join(screen))


def crm_log(name: str, text: str, kind: str = "note") -> ToolResult:
    if len((name or "").strip()) < 2 or not (text or "").strip():
        return ToolResult("Say who it's for and what to note, like: note for Priya: sent my resume.", ok=False)
    c = find_contact(name)
    if not c:
        cid = add_contact({"name": name})
        c = {"id": cid, "name": " ".join(name.split())}
        made = f" I added {c['name']} to your contacts."
    else:
        made = ""
    log(contact_id=c["id"], kind=kind if kind in KINDS else "note", text=text)
    return ToolResult(f"Saved a {kind if kind in KINDS else 'note'} for {c['name']}.{made}", screen=f"{c['name']}: {text}")


def crm_follow_up(name: str, when: str, note: str = "") -> ToolResult:
    if len((name or "").strip()) < 2:
        return ToolResult("Who should I remind you to follow up with?", ok=False)
    c = find_contact(name)
    if not c:
        return ToolResult(f"I don't have {name} in your contacts yet. Add them first, or say: note for {name}, ...", ok=False)
    d, t, _ = dates.find_when(when)
    if not d:
        return ToolResult("I couldn't tell the date. Say it like: next Monday, or 14 Nov at 5 pm.", ok=False)
    f = add_follow_up(d.isoformat(), t, note, contact_id=c["id"])
    return ToolResult(f"I'll remind you to follow up with {c['name']} {dates.spoken_day(d)}"
                      f"{' at ' + dates.spoken_time(t) if t else ''}.", screen=f"{f['title']} on {dates.nice(d, t)}")


def crm_due(period: str = "week") -> ToolResult:
    today = date.today()
    rows = [f for f in follow_ups() if period == "all" or (date.fromisoformat(f["date"]) - today).days <= (0 if period == "today" else 7)]
    if not rows:
        return ToolResult("No follow-ups due." if period != "all" else "You have no follow-ups set.")
    late = [r for r in rows if r["overdue"]]
    said = f"{len(rows)} follow-up{'s' if len(rows) != 1 else ''}" + (f", {len(late)} overdue" if late else "") + \
           f". First: {rows[0]['title'].replace('Follow up: ', '')}, {dates.spoken_day(date.fromisoformat(rows[0]['date']))}."
    return ToolResult(said, screen="\n".join(f"{'!' if r['overdue'] else '-'} {dates.nice(date.fromisoformat(r['date']), r['time'])}: {r['title']}" for r in rows))


p.add(Tool("crm_lookup", "Look up a person or organisation in the user's contacts: who they are, items, notes and history.",
           schema({"name": {"type": "string"}}, required=("name",)), "local", crm_lookup))
p.add(Tool("crm_log", "Save a note, call, meeting, email or message with a person to their history in the user's contacts.",
           schema({"name": {"type": "string"}, "text": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)}},
                  required=("name", "text")), "local", crm_log))
p.add(Tool("crm_follow_up", "Set a reminder to follow up with a person on a date (goes on the user's schedule).",
           schema({"name": {"type": "string"}, "when": {"type": "string", "description": "e.g. next Monday, 14 Nov at 5 pm"},
                   "note": {"type": "string"}}, required=("name", "when")), "local", crm_follow_up))
p.add(Tool("crm_due", "List follow-ups that are due: today, this week, or all.",
           schema({"period": {"type": "string", "enum": ["today", "week", "all"]}}), "local", crm_due))


def _name_after(text: str, words: tuple) -> str:
    low = text.lower()
    for w in words:
        i = low.find(w)
        if i >= 0:
            rest = text[i + len(w):].strip(" :,.")
            return re.split(r"[:,.]| on | next | tomorrow| about ", rest, maxsplit=1)[0].strip()
    return ""


def _note_args(t: str, m) -> dict:
    name = _name_after(t, ("note for ", "notes for ", "log for "))
    text = t.split(":", 1)[1].strip() if ":" in t else t
    return {"name": name, "text": text}


def _follow_args(t: str, m) -> dict:
    return {"name": _name_after(t, ("follow up with ", "follow-up with ", "followup with ")), "when": t}


p.mock_rule(1.5, ("note for", "notes for"), "crm_log", _note_args)
p.mock_rule(1.4, ("follow up with", "follow-up with", "followup with"), "crm_follow_up", _follow_args)
p.mock_rule(1.3, ("follow-ups", "follow ups", "who should i follow up"), "crm_due", lambda t, m: {"period": "week"})


@p.routes
def api(router) -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel, Field

    class Contact(BaseModel):
        name: str = Field(min_length=1, max_length=80)
        kind: str = Field(default="person", max_length=10)
        org: str = Field(default="", max_length=80)
        role: str = Field(default="", max_length=80)
        email: str = Field(default="", max_length=120)
        phone: str = Field(default="", max_length=40)
        tags: str = Field(default="", max_length=120)
        notes: str = Field(default="", max_length=2000)

    class Item(BaseModel):
        title: str = Field(min_length=1, max_length=120)
        contact_id: int | None = None
        stage: str = Field(default="", max_length=30)
        value: str = Field(default="", max_length=60)
        due: str = Field(default="", max_length=10)
        notes: str = Field(default="", max_length=2000)

    class Move(BaseModel):
        stage: str = Field(min_length=1, max_length=30)
        before: int | None = None

    class Activity(BaseModel):
        kind: str = Field(default="note", max_length=20)
        text: str = Field(min_length=1, max_length=2000)

    class FollowUp(BaseModel):
        date: str = Field(min_length=10, max_length=10)
        time: str = Field(default="", max_length=5)
        note: str = Field(default="", max_length=200)

    class Stages(BaseModel):
        stages: list[str] = Field(min_length=2, max_length=10)

    def bad(e: Exception):
        return HTTPException(422, str(e))

    @router.get("")
    def overview():
        return {**board(), "contacts": contacts(), "follow_ups": follow_ups(), "kinds": list(KINDS)}

    @router.post("/stages")
    def change_stages(body: Stages):
        try:
            return {"stages": set_stages(body.stages)}
        except ValueError as e:
            raise bad(e)

    @router.get("/contacts")
    def people(q: str = ""):
        return {"contacts": contacts(q[:80])}

    @router.post("/contacts")
    def new_person(body: Contact):
        try:
            return contact(add_contact(body.model_dump()))
        except ValueError as e:
            raise bad(e)

    @router.get("/contacts/{cid}")
    def one_person(cid: int):
        c = contact(cid)
        if not c:
            raise HTTPException(404, "That contact is gone.")
        return c

    @router.post("/contacts/{cid}")
    def edit_person(cid: int, body: Contact):
        try:
            if not update_contact(cid, body.model_dump()):
                raise HTTPException(404, "That contact is gone.")
        except ValueError as e:
            raise bad(e)
        return contact(cid)

    @router.post("/contacts/{cid}/delete")
    def remove_person(cid: int):
        if not delete_contact(cid):
            raise HTTPException(404, "That contact is gone.")
        return {"deleted": cid}

    @router.post("/contacts/{cid}/activity")
    def person_activity(cid: int, body: Activity):
        if not contact_name(cid):
            raise HTTPException(404, "That contact is gone.")
        log(contact_id=cid, kind=body.kind if body.kind in KINDS else "note", text=body.text)
        return contact(cid)

    @router.post("/contacts/{cid}/follow-up")
    def person_follow_up(cid: int, body: FollowUp):
        try:
            add_follow_up(body.date, body.time, body.note, contact_id=cid)
        except ValueError as e:
            raise bad(e)
        return contact(cid)

    @router.post("/items")
    def new_item(body: Item):
        try:
            return item_detail(add_item(body.model_dump()))
        except ValueError as e:
            raise bad(e)

    @router.get("/items/{iid}")
    def one_item(iid: int):
        it = item_detail(iid)
        if not it:
            raise HTTPException(404, "That item is gone.")
        return it

    @router.post("/items/{iid}")
    def edit_item(iid: int, body: Item):
        try:
            if not update_item(iid, body.model_dump()):
                raise HTTPException(404, "That item is gone.")
        except ValueError as e:
            raise bad(e)
        return item_detail(iid)

    @router.post("/items/{iid}/move")
    def drag(iid: int, body: Move):
        if not move_item(iid, body.stage, body.before):
            raise HTTPException(404, "That item or stage is gone.")
        return board()

    @router.post("/items/{iid}/delete")
    def remove_item(iid: int):
        if not delete_item(iid):
            raise HTTPException(404, "That item is gone.")
        return {"deleted": iid}

    @router.post("/items/{iid}/activity")
    def item_activity(iid: int, body: Activity):
        it = item(iid)
        if not it:
            raise HTTPException(404, "That item is gone.")
        log(contact_id=it["contact_id"], item_id=iid, kind=body.kind if body.kind in KINDS else "note", text=body.text)
        return item_detail(iid)

    @router.post("/items/{iid}/follow-up")
    def item_follow_up(iid: int, body: FollowUp):
        try:
            add_follow_up(body.date, body.time, body.note, item_id=iid)
        except ValueError as e:
            raise bad(e)
        return item_detail(iid)

    @router.post("/follow-ups/{event_id}/done")
    def done(event_id: int):
        if not follow_up_done(event_id):
            raise HTTPException(404, "That follow-up is gone.")
        return {"follow_ups": follow_ups()}
