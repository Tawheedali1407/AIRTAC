// People and pipeline (plugins/crm): the schedule as a CRM. Board (drag cards between stages), People, Follow-ups,
// and a side sheet with the details, history, a note box and a follow-up picker for a person or an item.
"use strict";

const Crm = (() => {
  const API_ = "/api/plugins/crm";
  let data = { stages: [], columns: [], contacts: [], follow_ups: [], kinds: [] };
  let tab = "board";
  let wired = false;

  const fmtDay = (iso, time) => {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const diff = Math.round((d - today) / 864e5);
    const word = diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : diff === -1 ? "Yesterday"
      : d.toLocaleDateString(undefined, { weekday: diff > 0 && diff < 7 ? "short" : undefined, day: "numeric", month: "short" });
    return word + (time ? ` ${time}` : "");
  };
  const initials = (n) => (n || "?").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

  async function load() {
    try { data = await api(API_, undefined, 15000); }
    catch (e) { $("crm-board").replaceChildren(el("p", { class: "muted", text: errorText(e) })); return; }
    draw();
  }

  function draw() {
    drawBoard();
    drawPeople();
    drawFollow();
    const due = data.follow_ups.filter((f) => f.overdue || f.date <= new Date().toISOString().slice(0, 10)).length;
    $("crm-due-count").textContent = String(due);
    $("crm-due-count").hidden = !due;
  }

  function setTab(name) {
    tab = name;
    for (const b of document.querySelectorAll("[data-crm-tab]")) b.setAttribute("aria-selected", String(b.dataset.crmTab === name));
    for (const p of document.querySelectorAll("[data-crm-pane]")) p.hidden = p.dataset.crmPane !== name;
  }

  // ---------------------------------------------------------------- board

  function card(it) {
    const c = el("button", { type: "button", class: "crm-card", draggable: "true", "data-id": String(it.id) },
      el("strong", { text: it.title }));
    const meta = [];
    if (it.contact) meta.push(el("span", { class: "crm-chip" }, icon("user"), it.contact));
    if (it.value) meta.push(el("span", { class: "crm-chip", text: it.value }));
    if (it.due) meta.push(el("span", { class: "crm-chip", text: `Due ${fmtDay(it.due)}` }));
    if (it.follow_up) meta.push(el("span", { class: `crm-chip follow${it.follow_up.overdue ? " late" : ""}` }, icon("clock"), fmtDay(it.follow_up.date, it.follow_up.time)));
    if (meta.length) c.append(el("span", { class: "crm-meta" }, ...meta));
    c.addEventListener("click", () => openItem(it.id));
    c.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", String(it.id)); c.classList.add("dragging"); });
    c.addEventListener("dragend", () => c.classList.remove("dragging"));
    return c;
  }

  function drawBoard() {
    const cols = data.columns.map((col, i) => {
      const list = el("div", { class: "crm-col-list", "data-stage": col.stage }, ...col.items.map(card));
      const add = el("button", { type: "button", class: "quiet crm-col-add" }, icon("plus"), "Add");
      add.addEventListener("click", () => editItem({ stage: col.stage }));
      const column = el("section", { class: "crm-col", "aria-label": col.stage, style: `--hue:${(i * 47) % 360}` },
        el("header", {}, el("span", { class: "crm-dot" }), el("h3", { text: col.stage }), el("span", { class: "muted small", text: String(col.items.length) })),
        list, add);
      column.addEventListener("dragover", (e) => {
        e.preventDefault();
        column.classList.add("over");
        const after = [...list.querySelectorAll(".crm-card:not(.dragging)")].find((x) => e.clientY < x.getBoundingClientRect().top + x.offsetHeight / 2);
        list.dataset.before = after ? after.dataset.id : "";
      });
      column.addEventListener("dragleave", () => column.classList.remove("over"));
      column.addEventListener("drop", async (e) => {
        e.preventDefault();
        column.classList.remove("over");
        const id = e.dataTransfer.getData("text/plain");
        if (!id) return;
        try {
          const b = await api(`${API_}/items/${id}/move`, { stage: col.stage, before: Number(list.dataset.before) || null }, 10000);
          data.columns = b.columns; data.stages = b.stages;
          drawBoard();
        } catch (err) { toast({ title: "Couldn't move it", body: errorText(err) }); }
      });
      return column;
    });
    $("crm-board").replaceChildren(...cols);
  }

  // ---------------------------------------------------------------- people and follow-ups

  function drawPeople() {
    const q = ($("crm-q").value || "").toLowerCase();
    const shown = data.contacts.filter((c) => !q || [c.name, c.org, c.role, c.tags, c.email].join(" ").toLowerCase().includes(q));
    $("crm-people").replaceChildren(...shown.map((c) => {
      const b = el("button", { type: "button", class: "crm-person" },
        el("span", { class: "crm-avatar", text: initials(c.name) }),
        el("span", { class: "crm-person-text" }, el("strong", { text: c.name }),
          el("small", { class: "muted", text: [c.role, c.org].filter(Boolean).join(" at ") || (c.kind === "org" ? "Organisation" : "") }),
          c.last ? el("small", { class: "muted", text: `${c.last.kind}: ${c.last.text}`.slice(0, 90) }) : ""),
        c.follow_up ? el("span", { class: `crm-chip follow${c.follow_up.overdue ? " late" : ""}` }, icon("clock"), fmtDay(c.follow_up.date, c.follow_up.time)) : "");
      b.addEventListener("click", () => openPerson(c.id));
      return el("li", {}, b);
    }), ...(shown.length ? [] : [el("li", { class: "muted", text: q ? "Nobody matches." : "No people yet. Press + Person, or say \"note for Priya: ...\" in a chat." })]));
  }

  function drawFollow() {
    const today = new Date().toISOString().slice(0, 10);
    const week = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
    const groups = { Overdue: [], Today: [], "This week": [], Later: [] };
    for (const f of data.follow_ups) groups[f.overdue ? "Overdue" : f.date === today ? "Today" : f.date <= week ? "This week" : "Later"].push(f);
    const out = [];
    for (const [name, rows] of Object.entries(groups)) {
      if (!rows.length) continue;
      out.push(el("h3", { class: `crm-follow-h${name === "Overdue" ? " late" : ""}`, text: `${name} (${rows.length})` }));
      out.push(el("ul", { class: "crm-follow-list" }, ...rows.map((f) => {
        const done = el("button", { type: "button", class: "quiet", text: "Done" });
        done.addEventListener("click", async () => {
          try { await api(`${API_}/follow-ups/${f.id}/done`, {}, 10000); await load(); } catch (e) { toast({ title: "Couldn't mark it", body: errorText(e) }); }
        });
        const open = el("button", { type: "button", class: "crm-follow-open", text: f.title.replace(/^Follow up: /, "") });
        open.addEventListener("click", () => (f.for.kind === "item" ? openItem(f.for.id) : openPerson(f.for.id)));
        return el("li", {}, el("span", { class: "crm-when", text: fmtDay(f.date, f.time) }), open, done);
      })));
    }
    $("crm-follow").replaceChildren(...(out.length ? out : [el("p", { class: "muted", text: "No follow-ups. Open a person or an item and set one: it goes on your schedule too." })]));
  }

  // ---------------------------------------------------------------- the side sheet

  function sheet(title, ...body) {
    $("crm-sheet-title").textContent = title;
    $("crm-sheet-body").replaceChildren(...body);
    const d = $("crm-sheet");
    if (!d.open) { if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", ""); }
  }
  const closeSheet = () => { const d = $("crm-sheet"); if (d.open) d.close(); };

  function field(label, name, value = "", attrs = {}) {
    const input = attrs.rows ? el("textarea", { class: "field", name, ...attrs }) : el("input", { class: "field", name, ...attrs });
    input.value = value || "";
    return el("label", { class: "setting" }, label, input);
  }

  function historyList(rows) {
    return el("ol", { class: "crm-history" }, ...rows.map((h) =>
      el("li", { class: `k-${h.kind}` }, el("span", { class: "crm-h-kind", text: h.kind }),
        el("span", { class: "crm-h-text", text: h.text }), el("small", { class: "muted", text: (h.at || "").slice(0, 16).replace("T", " ") }))));
  }

  function noteBox(url, after) {
    const kind = el("select", { class: "field", "aria-label": "Kind" }, ...data.kinds.map((k) => el("option", { value: k, text: k[0].toUpperCase() + k.slice(1) })));
    const text = el("textarea", { class: "field", rows: "2", maxlength: "2000", placeholder: "What happened? (e.g. Sent my resume)", "aria-label": "Note" });
    const save = el("button", { type: "button", class: "primary", text: "Add to history" });
    save.addEventListener("click", async () => {
      if (!text.value.trim()) return;
      try { after(await api(url, { kind: kind.value, text: text.value.trim() }, 10000)); load(); } catch (e) { toast({ title: "Couldn't save it", body: errorText(e) }); }
    });
    return el("div", { class: "crm-box" }, el("h3", { text: "Add a note" }), el("div", { class: "row" }, kind), text, save);
  }

  function followBox(url, after) {
    const d = el("input", { class: "field", type: "date", "aria-label": "Date" });
    d.value = new Date(Date.now() + 2 * 864e5).toISOString().slice(0, 10);
    const t = el("input", { class: "field", type: "time", "aria-label": "Time (optional)" });
    const note = el("input", { class: "field", maxlength: "200", placeholder: "About what? (optional)", "aria-label": "About" });
    const save = el("button", { type: "button", class: "primary", text: "Set follow-up" });
    save.addEventListener("click", async () => {
      try { after(await api(url, { date: d.value, time: t.value, note: note.value.trim() }, 10000)); load(); toast({ title: "Follow-up set", body: "It's on your schedule; AIRTAC will remind you." }); }
      catch (e) { toast({ title: "Couldn't set it", body: errorText(e) }); }
    });
    return el("div", { class: "crm-box" }, el("h3", { text: "Follow up" }), el("div", { class: "row" }, d, t), note, save);
  }

  function followList(rows) {
    if (!rows.length) return "";
    return el("ul", { class: "crm-follow-list" }, ...rows.map((f) => el("li", {}, el("span", { class: `crm-when${f.overdue ? " late" : ""}`, text: fmtDay(f.date, f.time) }), el("span", { text: f.title }))));
  }

  async function openPerson(id) {
    let c;
    try { c = await api(`${API_}/contacts/${id}`, undefined, 10000); } catch (e) { toast({ title: "Couldn't open it", body: errorText(e) }); return; }
    const showP = (p) => {
      const edit = el("button", { type: "button", class: "quiet", text: "Edit" });
      edit.addEventListener("click", () => editPerson(p));
      const del = el("button", { type: "button", class: "quiet danger", text: "Delete" });
      del.addEventListener("click", async () => {
        if (!confirm(`Delete ${p.name}? Their items stay on the board.`)) return;
        await api(`${API_}/contacts/${p.id}/delete`, {}, 10000).catch((e) => toast({ title: "Couldn't delete", body: errorText(e) }));
        closeSheet(); load();
      });
      const newItem = el("button", { type: "button", class: "quiet", text: "New item for them" });
      newItem.addEventListener("click", () => editItem({ contact_id: p.id }));
      const lines = [p.role && p.org ? `${p.role} at ${p.org}` : p.role || p.org, p.email, p.phone, p.tags && `Tags: ${p.tags}`].filter(Boolean);
      sheet(p.name,
        el("div", { class: "crm-who" }, el("span", { class: "crm-avatar big", text: initials(p.name) }), el("div", {}, ...lines.map((l) => el("p", { class: "small", text: l })))),
        p.notes ? el("p", { class: "crm-notes", text: p.notes }) : "",
        el("div", { class: "row" }, edit, newItem, del),
        p.items.length ? el("div", { class: "crm-box" }, el("h3", { text: "Items" }), ...p.items.map((i) => {
          const b = el("button", { type: "button", class: "chip-btn", text: `${i.title} · ${i.stage}` });
          b.addEventListener("click", () => openItem(i.id));
          return b;
        })) : "",
        followList(p.follow_ups),
        followBox(`${API_}/contacts/${p.id}/follow-up`, showP),
        noteBox(`${API_}/contacts/${p.id}/activity`, showP),
        el("h3", { text: "History" }), historyList(p.history));
    };
    showP(c);
  }

  async function openItem(id) {
    let it;
    try { it = await api(`${API_}/items/${id}`, undefined, 10000); } catch (e) { toast({ title: "Couldn't open it", body: errorText(e) }); return; }
    const showI = (x) => {
      const edit = el("button", { type: "button", class: "quiet", text: "Edit" });
      edit.addEventListener("click", () => editItem(x));
      const del = el("button", { type: "button", class: "quiet danger", text: "Delete" });
      del.addEventListener("click", async () => {
        if (!confirm(`Delete "${x.title}" and its history?`)) return;
        await api(`${API_}/items/${x.id}/delete`, {}, 10000).catch((e) => toast({ title: "Couldn't delete", body: errorText(e) }));
        closeSheet(); load();
      });
      const stage = el("select", { class: "field", "aria-label": "Stage" }, ...data.stages.map((s) => el("option", { value: s, text: s })));
      stage.value = x.stage;
      stage.addEventListener("change", async () => {
        try { await api(`${API_}/items/${x.id}/move`, { stage: stage.value }, 10000); openItem(x.id); load(); } catch (e) { toast({ title: "Couldn't move it", body: errorText(e) }); }
      });
      const who = x.contact ? el("button", { type: "button", class: "chip-btn" }, icon("user"), x.contact) : "";
      if (who) who.addEventListener("click", () => openPerson(x.contact_id));
      sheet(x.title,
        el("label", { class: "setting" }, "Stage", stage),
        el("div", { class: "row" }, who, x.value ? el("span", { class: "crm-chip", text: x.value }) : "", x.due ? el("span", { class: "crm-chip", text: `Due ${fmtDay(x.due)}` }) : ""),
        x.notes ? el("p", { class: "crm-notes", text: x.notes }) : "",
        el("div", { class: "row" }, edit, del),
        followList(x.follow_ups),
        followBox(`${API_}/items/${x.id}/follow-up`, showI),
        noteBox(`${API_}/items/${x.id}/activity`, showI),
        el("h3", { text: "History" }), historyList(x.history));
    };
    showI(it);
  }

  function editPerson(p = {}) {
    const kind = el("select", { class: "field", name: "kind" }, el("option", { value: "person", text: "Person" }), el("option", { value: "org", text: "Organisation" }));
    kind.value = p.kind || "person";
    const form = el("form", { class: "crm-form" },
      field("Name", "name", p.name, { maxlength: "80", required: "" }), el("label", { class: "setting" }, "Kind", kind),
      field("Role", "role", p.role, { maxlength: "80", placeholder: "Recruiter, mentor, professor..." }),
      field("Company or college", "org", p.org, { maxlength: "80" }),
      field("Email", "email", p.email, { maxlength: "120", type: "email" }), field("Phone", "phone", p.phone, { maxlength: "40" }),
      field("Tags", "tags", p.tags, { maxlength: "120", placeholder: "gsoc, internship" }),
      field("Notes", "notes", p.notes, { rows: "3", maxlength: "2000" }),
      el("button", { type: "submit", class: "primary", text: p.id ? "Save" : "Add person" }));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(form));
      try {
        const saved = await api(p.id ? `${API_}/contacts/${p.id}` : `${API_}/contacts`, body, 10000);
        await load(); openPerson(saved.id);
      } catch (err) { toast({ title: "Couldn't save", body: errorText(err) }); }
    });
    sheet(p.id ? `Edit ${p.name}` : "New person", form);
    form.elements.name.focus();
  }

  function editItem(it = {}) {
    const stage = el("select", { class: "field", name: "stage" }, ...data.stages.map((s) => el("option", { value: s, text: s })));
    stage.value = it.stage || data.stages[0];
    const person = el("select", { class: "field", name: "contact_id" }, el("option", { value: "", text: "Nobody" }),
      ...data.contacts.map((c) => el("option", { value: String(c.id), text: c.name })));
    person.value = it.contact_id ? String(it.contact_id) : "";
    const form = el("form", { class: "crm-form" },
      field("Title", "title", it.title, { maxlength: "120", required: "", placeholder: "Acme ML internship" }),
      el("label", { class: "setting" }, "Stage", stage), el("label", { class: "setting" }, "Person", person),
      field("Value", "value", it.value, { maxlength: "60", placeholder: "Stipend, prize, amount... (optional)" }),
      field("Due", "due", it.due, { type: "date" }), field("Notes", "notes", it.notes, { rows: "3", maxlength: "2000" }),
      el("button", { type: "submit", class: "primary", text: it.id ? "Save" : "Add item" }));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(form));
      body.contact_id = body.contact_id ? Number(body.contact_id) : null;
      try {
        const saved = await api(it.id ? `${API_}/items/${it.id}` : `${API_}/items`, body, 10000);
        await load(); openItem(saved.id);
      } catch (err) { toast({ title: "Couldn't save", body: errorText(err) }); }
    });
    sheet(it.id ? `Edit ${it.title}` : "New item", form);
    form.elements.title.focus();
  }

  function editStages() {
    const box = el("textarea", { class: "field", rows: "7", "aria-label": "Stages, one per line" });
    box.value = data.stages.join("\n");
    const save = el("button", { type: "button", class: "primary", text: "Save stages" });
    save.addEventListener("click", async () => {
      try { await api(`${API_}/stages`, { stages: box.value.split("\n").map((s) => s.trim()).filter(Boolean) }, 10000); closeSheet(); load(); }
      catch (e) { toast({ title: "Couldn't save", body: errorText(e) }); }
    });
    sheet("Stages", el("p", { class: "muted small", text: "One per line, 2 to 10. Cards in a stage you remove move to the first one." }), box, save);
  }

  function wire() {
    wired = true;
    for (const b of document.querySelectorAll("[data-crm-tab]")) b.addEventListener("click", () => setTab(b.dataset.crmTab));
    $("crm-add-item").addEventListener("click", () => editItem({}));
    $("crm-add-person").addEventListener("click", () => editPerson({}));
    $("crm-stages").addEventListener("click", editStages);
    $("crm-q").addEventListener("input", drawPeople);
    $("crm-sheet-close").addEventListener("click", closeSheet);
  }

  function open() {
    if (!wired) wire();
    setTab(tab);
    load();
  }

  if (document.body.dataset.view === "crm") open();
  return { open, openPerson, openItem };
})();
window.Crm = Crm;
