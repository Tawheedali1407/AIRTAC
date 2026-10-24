// Tabs: sites open inside AIRTAC, like editor tabs, but only sites that allow it.
//
// - A site opens here only when its own security headers allow being shown inside another page
//   (the server's embed check, plugins/desktop/embed.py). The page's Content-Security-Policy lets only
//   those sites load in a frame. AIRTAC never strips or proxies a site's headers to force it in.
// - Every tab is a sandboxed frame (scripts, forms and popups; no top-level navigation of AIRTAC) that
//   sends no referrer.
// - At most 5 tabs stay loaded; the others are unloaded and load again when you open them.
// - Tabs are kept in this browser (localStorage), so they come back after a restart. Groups: "My tabs"
//   and "College view" (Canvas, Gmail, Drive, DigiLocker, YouTube, LinkedIn: the ones that allow tabs).
// Loaded after app.js and hub.js; uses their helpers ($, el, icon, api, errorText, load, save, catalog).
(() => {
  const MAX_LIVE = 5;
  const SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups";
  const COLLEGE = ["canvas", "gmail", "drive", "digilocker", "apps-youtube", "apps-linkedin"];
  const KEY = "airtac-tabs";

  let saved = null;
  try { saved = JSON.parse(load(KEY) || "null"); } catch { saved = null; }   // load() and save() keep strings
  const state = {
    tabs: Array.isArray(saved?.tabs) ? saved.tabs.filter((t) => t && typeof t.id === "string").slice(0, 30) : [],
    active: saved?.active || "",
    group: saved?.group || "all",
    live: [],          // ids with a loaded frame, most recent last
  };
  const frames = new Map();   // id -> iframe

  function persist() {
    save(KEY, JSON.stringify({ tabs: state.tabs.map(({ id, group }) => ({ id, group })), active: state.active, group: state.group }));
  }

  async function apps() {
    if (!hub.apps) await catalog();
    return allCatalogApps();
  }

  function info(id, list) {
    return list.find((a) => a.id === id);
  }

  // ------------------------------------------------------------ frames

  function frameFor(a) {
    let f = frames.get(a.id);
    if (!f) {
      f = el("iframe", { src: a.url, title: a.name, sandbox: SANDBOX, referrerpolicy: "no-referrer", class: "tab-frame",
        allow: "clipboard-write", loading: "eager" });
      frames.set(a.id, f);
      $("tab-frames").append(f);
    }
    state.live = state.live.filter((x) => x !== a.id).concat(a.id);
    while (state.live.length > MAX_LIVE) {     // unload the tab used longest ago
      const old = state.live.shift();
      frames.get(old)?.remove();
      frames.delete(old);
    }
    return f;
  }

  function closeFrame(id) {
    frames.get(id)?.remove();
    frames.delete(id);
    state.live = state.live.filter((x) => x !== id);
  }

  // ------------------------------------------------------------ drawing

  async function draw() {
    let list = [];
    try { list = await apps(); } catch (e) { $("tab-note").textContent = errorText(e); }
    // A tab whose site no longer passes the check (or was removed) closes, with a note.
    const gone = state.tabs.filter((t) => { const a = info(t.id, list); return !a || a.open_as !== "tab"; });
    if (gone.length && list.length) {
      for (const t of gone) closeFrame(t.id);
      state.tabs = state.tabs.filter((t) => !gone.includes(t));
      $("tab-note").textContent = `${gone.length} tab${gone.length === 1 ? "" : "s"} closed: the site no longer allows being opened as a tab. Open it from its page in your browser.`;
    }
    const groups = ["all", ...new Set(state.tabs.map((t) => t.group))];
    if (!groups.includes(state.group)) state.group = "all";
    $("tab-groups").replaceChildren(...(groups.length > 2 ? groups.map((g) => {
      const b = el("button", { type: "button", class: "chip-btn", role: "tab", "aria-selected": String(state.group === g), text: g === "all" ? "All tabs" : g });
      b.addEventListener("click", () => { state.group = g; persist(); draw(); });
      return b;
    }) : []));
    const shown = state.tabs.filter((t) => state.group === "all" || t.group === state.group);
    if (!shown.some((t) => t.id === state.active)) state.active = shown[0]?.id || "";
    $("tab-list").replaceChildren(...shown.map((t) => tabButton(t, info(t.id, list))));
    $("tab-empty").hidden = shown.length > 0;
    if (!shown.length) drawPicks(list);
    const a = info(state.active, list);
    for (const [id, f] of frames) f.hidden = id !== state.active;
    if (a) frameFor(a).hidden = false;
    const ext = $("tab-external");
    ext.hidden = !a;
    if (a) { ext.href = a.url; ext.lastChild.textContent = `Open ${a.name} in browser ↗`; }
    persist();
  }

  function tabButton(t, a) {
    const on = t.id === state.active;
    const close = el("span", { class: "tab-close", role: "button", tabindex: "-1", "aria-label": `Close ${a?.name || t.id}`, title: "Close (Ctrl+W)" }, icon("close"));
    const b = el("button", { type: "button", class: `site-tab${frames.has(t.id) ? "" : " unloaded"}`, role: "tab", "aria-selected": String(on),
      draggable: "true", "data-id": t.id, title: frames.has(t.id) || on ? a?.name || t.id : `${a?.name || t.id} (unloaded; opens again when you pick it)` },
    a ? appLogo(a, "sm") : "", el("span", { class: "tab-name", text: a?.name || t.id }), close);
    b.addEventListener("click", (e) => { if (close.contains(e.target)) closeTab(t.id); else activate(t.id); });
    b.addEventListener("auxclick", (e) => { if (e.button === 1) { e.preventDefault(); closeTab(t.id); } });
    b.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.shiftKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); move(t.id, e.key === "ArrowLeft" ? -1 : 1); }
      else if (e.key === "Delete") { e.preventDefault(); closeTab(t.id); }
    });
    b.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", t.id); e.dataTransfer.effectAllowed = "move"; });
    b.addEventListener("dragover", (e) => { e.preventDefault(); b.classList.add("drop"); });
    b.addEventListener("dragleave", () => b.classList.remove("drop"));
    b.addEventListener("drop", (e) => {
      e.preventDefault();
      b.classList.remove("drop");
      const from = e.dataTransfer.getData("text/plain");
      if (from && from !== t.id) reorder(from, t.id);
    });
    return b;
  }

  function drawPicks(list) {
    const can = list.filter((a) => a.open_as === "tab");
    $("tab-picks").replaceChildren(...(can.length ? can.slice(0, 24).map((a) => {
      const b = el("button", { type: "button", class: "quiet small-btn" }, appLogo(a, "sm"), a.name);
      b.addEventListener("click", () => open(a));
      return b;
    }) : [el("p", { class: "muted small", text: hub.apps?.here
      ? "No site in your lists allows tabs yet, or they haven't been checked. Press Re-probe tabs on the AI apps, Apps or Social page."
      : "No site in your lists allows tabs yet." })]));
  }

  // ------------------------------------------------------------ actions

  function activate(id) {
    state.active = id;
    draw();
  }

  function open(a, group = "My tabs") {
    if (!a || a.open_as !== "tab") return false;
    const have = state.tabs.find((t) => t.id === a.id);
    if (!have) state.tabs.push({ id: a.id, group });
    else if (group === "College view") have.group = group;       // College view gathers its sites into one group
    state.active = a.id;
    if (state.group !== "all" && state.group !== group) state.group = "all";
    persist();
    if (!$("view-tabs").hidden) draw();
    return true;
  }

  function closeTab(id) {
    const at = state.tabs.findIndex((t) => t.id === id);
    if (at < 0) return;
    state.tabs.splice(at, 1);
    closeFrame(id);
    if (state.active === id) state.active = (state.tabs[at] || state.tabs[at - 1] || {}).id || "";
    draw();
  }

  function reorder(fromId, toId) {
    const from = state.tabs.findIndex((t) => t.id === fromId);
    const to = state.tabs.findIndex((t) => t.id === toId);
    const [item] = state.tabs.splice(from, 1);
    state.tabs.splice(to, 0, item);   // dropped on a tab to the right: lands after it; to the left: before it
    draw();
  }

  function move(id, step) {
    const i = state.tabs.findIndex((t) => t.id === id);
    const j = i + step;
    if (i < 0 || j < 0 || j >= state.tabs.length) return;
    [state.tabs[i], state.tabs[j]] = [state.tabs[j], state.tabs[i]];
    draw().then(() => document.querySelector(`.site-tab[data-id="${CSS.escape(id)}"]`)?.focus());
  }

  async function college() {
    let list = [];
    try { list = await apps(); } catch (e) { $("tab-note").textContent = errorText(e); return; }
    const picked = COLLEGE.map((id) => info(id, list)).filter(Boolean);
    const inTabs = picked.filter((a) => a.open_as === "tab");
    for (const a of inTabs) open(a, "College view");
    state.group = inTabs.length ? "College view" : state.group;
    const outside = picked.filter((a) => a.open_as !== "tab").map((a) => a.name);
    $("tab-note").textContent = (inTabs.length ? `Opened ${inTabs.map((a) => a.name).join(", ")} as tabs. ` : "")
      + (outside.length ? `${outside.join(", ")} don't allow tabs, so they open in your browser from the Apps page.` : "");
    draw();
  }

  $("tab-college").addEventListener("click", college);
  $("tab-search").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("tab-q").value.trim();
    if (q) openWeb(q);
  });
  document.addEventListener("keydown", (e) => {
    if ($("view-tabs").hidden || !(e.ctrlKey || e.metaKey) || e.altKey) return;
    if (e.key.toLowerCase() === "w" && state.active) { e.preventDefault(); closeTab(state.active); }
    else if (e.key === "Tab") {                       // Ctrl+Tab / Ctrl+Shift+Tab: next and previous tab
      const shown = state.tabs.filter((t) => state.group === "all" || t.group === state.group);
      const i = shown.findIndex((t) => t.id === state.active);
      if (shown.length > 1) { e.preventDefault(); activate(shown[(i + (e.shiftKey ? -1 : 1) + shown.length) % shown.length].id); }
    }
  });

  window.AirtacTabs = { open, show: draw, close: closeTab, state };
  if (!$("view-tabs").hidden) draw();   // the page opened straight on #tabs, before this file loaded
})();
