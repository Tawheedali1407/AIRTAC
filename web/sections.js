// Settings > Sections (plugins/sections): your own sections like Research. Each gets a menu entry that opens the
// Chat page with that section picked; its instructions, plugins and documents go with every message there.
"use strict";

const Sections = (() => {
  let data = { sections: [], icons: [], plugins: [], starters: [] };
  let docs = [];   // the form's documents: {id, name, status}

  async function load() {
    try { data = await api("/api/plugins/sections", undefined, 10000); } catch { /* the plugin is off */ }
    drawNav();
    return data;
  }

  function drawNav() {
    const nav = $("nav-sections");
    if (!nav) return;
    nav.replaceChildren(...data.sections.map((s) => {
      const a = el("a", { href: "#chat", "data-section": s.id }, icon(s.icon || "spark"), s.name);
      a.addEventListener("click", (e) => { e.preventDefault(); openSection(s.id); });
      return a;
    }));

  }

  function openSection(id) {
    try { localStorage.setItem("airtac-chat-section", id); } catch { /* fine */ }
    if (typeof setDrawer === "function") setDrawer(false);
    if (window.Chat && Chat.state.started) Chat.newChat();   // a section starts a fresh chat
    if (location.hash === "#chat") Chat.open(); else location.hash = "#chat";   // open() picks the remembered section
  }

  async function drawSettings() {
    await load();
    const list = $("sec-list");
    list.replaceChildren(...data.sections.map((s) => {
      const edit = el("button", { type: "button", class: "quiet", text: "Edit" });
      edit.addEventListener("click", () => openForm(s));
      const del = el("button", { type: "button", class: "quiet danger", text: "Delete" });
      del.addEventListener("click", async () => {
        if (!confirm(`Delete the section "${s.name}"? Its chats stay in History and its documents stay on the PC.`)) return;
        try { await api(`/api/plugins/sections/${encodeURIComponent(s.id)}/delete`, {}, 10000); } catch (e) { toast({ title: "Couldn't delete it", body: errorText(e) }); }
        drawSettings();
      });
      const go = el("button", { type: "button", class: "primary", text: "Open" });
      go.addEventListener("click", () => openSection(s.id));
      const bits = [s.plugins.length ? `${s.plugins.length} plugin${s.plugins.length === 1 ? "" : "s"}` : "AIRTAC picks plugins",
        `${(s.documents || []).length} document${(s.documents || []).length === 1 ? "" : "s"}`];
      return el("li", { class: "sec-item" }, el("span", { class: "sec-icon" }, icon(s.icon || "spark")),
        el("span", { class: "sec-text" }, el("strong", { text: s.name }), el("small", { class: "muted", text: (s.prompt || "No instructions").slice(0, 120) }),
          el("small", { class: "muted", text: bits.join(" · ") })), go, edit, del);
    }));
    $("sec-starters").replaceChildren(...(data.sections.length ? [] : [el("span", { class: "muted small", text: "Start from: " }),
      ...data.starters.map((st) => {
        const b = el("button", { type: "button", class: "chip-btn" }, icon(st.icon), st.name);
        b.addEventListener("click", () => openForm({ ...st, id: "", docs: [] }));
        return b;
      })]));
  }

  function openForm(s) {
    const f = $("sec-form");
    f.hidden = false;
    f.elements.id.value = s.id || "";
    f.elements.name.value = s.name || "";
    f.elements.prompt.value = s.prompt || "";
    $("sec-icons").replaceChildren(...data.icons.map((n) => {
      const r = el("input", { type: "radio", name: "sec-icon", value: n, "aria-label": n });
      r.checked = n === (s.icon || "spark");
      return el("label", { class: "sec-icon-pick", title: n }, r, icon(n));
    }));
    $("sec-plugins").replaceChildren(...data.plugins.map((pl) => {
      const box = el("input", { type: "checkbox", value: pl.id });
      box.checked = (s.plugins || []).includes(pl.id);
      return el("label", { class: "side-plugin" }, box, icon(pl.icon || "puzzle"), el("span", { text: pl.name }));
    }));
    docs = (s.documents || []).map((d) => ({ ...d }));
    drawDocs();
    $("sec-note").textContent = "";
    f.scrollIntoView({ behavior: "smooth", block: "nearest" });
    f.elements.name.focus();
  }

  function drawDocs() {
    $("sec-docs").replaceChildren(...docs.map((d) => {
      const x = el("button", { type: "button", class: "icon-btn small", "aria-label": `Remove ${d.name}` }, icon("close"));
      x.addEventListener("click", () => { docs = docs.filter((y) => y.id !== d.id); drawDocs(); });
      return el("li", {}, icon("paper"), el("span", { text: d.name }), el("small", { class: "muted", text: d.status === "ready" ? "Ready" : d.status === "error" ? (d.message || "Couldn't read it") : "Reading..." }), x);
    }));
  }

  function asBase64(file) {
    return new Promise((ok, fail) => {
      const r = new FileReader();
      r.onload = () => ok(String(r.result).split(",", 2)[1] || "");
      r.onerror = () => fail(new Error("Couldn't read that file."));
      r.readAsDataURL(file);
    });
  }

  async function addFiles(files) {
    for (const file of [...files].slice(0, 10 - docs.length)) {
      $("sec-note").textContent = `Adding ${file.name}...`;
      try {
        const up = await api("/api/upload", { section: "sections", name: file.name, data_base64: await asBase64(file) }, 120000);
        docs.push({ id: up.id, name: up.name, status: up.status, message: up.message });
      } catch (e) { toast({ title: `Couldn't add ${file.name}`, body: errorText(e) }); }
    }
    $("sec-note").textContent = "";
    drawDocs();
  }

  async function save(e) {
    e.preventDefault();
    const f = $("sec-form");
    const body = {
      name: f.elements.name.value.trim(), prompt: f.elements.prompt.value.trim(),
      icon: document.querySelector('input[name="sec-icon"]:checked')?.value || "spark",
      plugins: [...document.querySelectorAll("#sec-plugins input:checked")].map((b) => b.value),
      docs: docs.map((d) => d.id),
    };
    const id = f.elements.id.value;
    try {
      await api(id ? `/api/plugins/sections/${encodeURIComponent(id)}` : "/api/plugins/sections", body, 15000);
    } catch (err) { $("sec-note").textContent = errorText(err); return; }
    f.hidden = true;
    toast({ title: `Saved "${body.name}"`, body: "It's in the menu under Your sections, and in the Chat page's section list." });
    drawSettings();
  }

  function wire() {
    $("sec-add").addEventListener("click", () => openForm({ id: "", icon: "spark", plugins: [], documents: [] }));
    $("sec-cancel").addEventListener("click", () => { $("sec-form").hidden = true; });
    $("sec-form").addEventListener("submit", save);
    $("sec-file").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  }

  wire();
  load();
  if (document.body.dataset.view === "settings") drawSettings();
  return { load, drawSettings, openSection };
})();
window.Sections = Sections;
