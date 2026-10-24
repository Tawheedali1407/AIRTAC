// The side panel, the command palette and the AI apps catalog. Started as one file for both versions;
// this Advance copy adds app logos (web/logos).
//
// - Side panel ("dock"), like VS Code's side bar: a strip of icons on the right edge opens a panel with a
//   part of everything: Ask (a quick question), Web, AI apps, Today, and Tokens (Advance). Ctrl+B shows or
//   hides it. On a phone the panel button in the top bar opens it full screen.
// - Command palette and Search everything: Ctrl+K (or Cmd+K, or the search button on a phone). Type to find
//   any mode, app, panel or AI app, and (Advance) your chats, notes, knowledge, schedule, files, apps and
//   settings. Filters narrow it; ↑ ↓ and Enter work; recent searches show when the box is empty.
// - AI apps: Claude, Gemini, Perplexity, NotebookLM, Manus, Higgsfield, Lovable and 20+ more. Star the
//   ones you want on Home. Add your own.
//
// Each version's app.js sets window.DOCK_HOST: where the data comes from and how links open (the Advance
// version opens them in AIRTAC's Chrome profile, so you stay signed in). This file needs nothing else.
(() => {
  const host = window.DOCK_HOST;
  if (!host) return;
  const $id = (id) => document.getElementById(id);

  // Icons this file needs, added to the page's icon set if missing.
  const ICONS = {
    dock: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M14.5 4.5v15"/>',
    ask: '<path d="M5 18.5V7a2.5 2.5 0 0 1 2.5-2.5h9A2.5 2.5 0 0 1 19 7v6a2.5 2.5 0 0 1-2.5 2.5H9z"/><path d="M9 9.5h6M9 12h4"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.4 3.5 5.3 3.5 8.5S14.4 18.1 12 20.5M12 3.5C9.6 5.9 8.5 8.8 8.5 12s1.1 6.1 3.5 8.5"/>',
    sparkles: '<path d="M10 3.5l1.6 4.4L16 9.5l-4.4 1.6L10 15.5l-1.6-4.4L4 9.5l4.4-1.6zM17.5 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
    today: '<rect x="4" y="5.5" width="16" height="15" rx="2"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4M8 14h4"/>',
    gauge: '<path d="M4.5 16.5a7.5 7.5 0 1 1 15 0"/><path d="M12 16.5l3.5-5"/><circle cx="12" cy="16.5" r="1.2"/>',
    star: '<path d="M12 4l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.4l-4.8 2.5.9-5.4-3.9-3.8 5.4-.8z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    command: '<path d="M8 4.5a3.5 3.5 0 1 0 0 7h8a3.5 3.5 0 1 0 0-7v15a3.5 3.5 0 1 1 0-7H8a3.5 3.5 0 1 1 0 7z"/>',
  };
  const defs = document.querySelector("svg defs");
  for (const [name, d] of Object.entries(ICONS)) {
    if (defs && !$id(`i-${name}`)) {
      const s = document.createElementNS("http://www.w3.org/2000/svg", "symbol");
      s.id = `i-${name}`;
      s.setAttribute("viewBox", "0 0 24 24");
      s.innerHTML = d;   // fixed shapes written above, never user text
      defs.append(s);
    }
  }

  const TABS = [
    { id: "ask", label: "Ask", icon: "ask", key: "A" },
    { id: "web", label: "Web", icon: "globe", key: "W" },
    { id: "ai", label: "AI apps", icon: "sparkles", key: "I" },
    { id: "today", label: "Today", icon: "today", key: "T" },
    { id: "tokens", label: "Tokens", icon: "gauge", key: "K" },
  ].filter((t) => host.tabs.includes(t.id));

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* fine */ } },
  };
  const state = { open: false, tab: store.get("airtac-dock-tab", "ask"), ai: null, aiGroup: "all", aiQuery: "" };
  if (!TABS.some((t) => t.id === state.tab)) state.tab = TABS[0].id;

  // ---------------------------------------------------------------- the frame: icon strip + panel

  const bar = el("nav", { class: "dock-bar", "aria-label": "Side panel" });
  const panel = el("aside", { class: "dock", id: "dock", "aria-label": "Side panel", hidden: "" });
  const title = el("h2", { class: "dock-title" });
  const close = el("button", { type: "button", class: "icon-btn small", "aria-label": "Close the side panel", title: "Close (Ctrl+B)" }, icon("close"));
  const chips = el("div", { class: "dock-chips", role: "tablist" });
  const body = el("div", { class: "dock-body" });
  panel.append(el("div", { class: "dock-head" }, title, close), chips, body);
  close.addEventListener("click", () => setOpen(false));

  for (const t of TABS) {
    const b = el("button", { type: "button", class: "dock-btn", "data-tab": t.id, "aria-label": t.label, title: `${t.label}` }, icon(t.icon));
    b.addEventListener("click", () => (state.open && state.tab === t.id ? setOpen(false) : openTab(t.id)));
    bar.append(b);
    const c = el("button", { type: "button", class: "chip-btn", role: "tab", "data-tab": t.id, text: t.label });
    c.addEventListener("click", () => openTab(t.id));
    chips.append(c);
  }
  const cmd = el("button", { type: "button", class: "dock-btn dock-cmd", "aria-label": "Command palette", title: "Command palette (Ctrl+K)" }, icon("command"));
  cmd.addEventListener("click", () => openPalette());
  bar.append(el("span", { class: "dock-gap" }), cmd);
  document.body.append(bar, panel);

  // The top bar gets a panel button (the only way in on a phone).
  const topBtn = el("button", { type: "button", class: "icon-btn dock-top", "aria-label": "Side panel", "aria-controls": "dock", title: "Side panel (Ctrl+B)" }, icon("dock"));
  topBtn.addEventListener("click", () => setOpen(!state.open));
  const spacer = document.querySelector(".topbar .spacer");
  if (spacer) spacer.after(topBtn);
  document.body.classList.add("has-dock");

  function setOpen(open) {
    state.open = open;
    panel.hidden = !open;
    document.body.classList.toggle("dock-open", open);
    topBtn.setAttribute("aria-expanded", String(open));
    for (const b of bar.querySelectorAll("[data-tab]")) b.setAttribute("aria-pressed", String(open && b.dataset.tab === state.tab));
    if (open) render();
  }

  function openTab(id) {
    state.tab = id;
    store.set("airtac-dock-tab", id);
    for (const c of chips.children) c.setAttribute("aria-selected", String(c.dataset.tab === id));
    setOpen(true);
    panel.classList.remove("swap");
    void panel.offsetWidth;
    panel.classList.add("swap");
  }

  function render() {
    const t = TABS.find((x) => x.id === state.tab);
    title.textContent = t.label;
    for (const c of chips.children) c.setAttribute("aria-selected", String(c.dataset.tab === state.tab));
    ({ ask: drawAsk, web: drawWeb, ai: drawAi, today: drawToday, tokens: drawTokens })[state.tab]();
  }

  // ---------------------------------------------------------------- Ask: a quick question from any page

  let askLog = [];
  function drawAsk() {
    const input = el("textarea", { class: "field", rows: "2", placeholder: "Ask AIRTAC anything", "aria-label": "Your question" });
    const send = el("button", { type: "submit", class: "primary", text: "Ask" });
    const form = el("form", { class: "dock-ask" }, input, send);
    const log = el("div", { class: "dock-log", "aria-live": "polite" });
    const draw = () => log.replaceChildren(...askLog.slice(-6).map((m) => el("div", { class: `dock-msg ${m.who}` },
      el("p", { text: m.text }), m.screen ? el("pre", { text: m.screen }) : "",
      m.pending ? el("a", { href: "#home", class: "conn-link", "data-view": "home", text: "It needs your OK: open Home" }) : "")));
    draw();
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (!q) return;
      input.value = "";
      askLog.push({ who: "me", text: q }, { who: "ai", text: "Working on it..." });
      draw();
      send.disabled = true;
      try {
        const r = await api("/api/chat", { message: q, session_id: sessionId }, 120000);
        askLog[askLog.length - 1] = { who: "ai", text: r.spoken, screen: r.screen && r.screen !== r.spoken ? r.screen : "", pending: !!r.pending };
      } catch (err) {
        askLog[askLog.length - 1] = { who: "ai", text: errorText(err) };
      }
      send.disabled = false;
      draw();
      log.lastElementChild?.scrollIntoView({ block: "nearest" });
    });
    body.replaceChildren(form, log, el("p", { class: "muted small", text: "Same chat as Home. Answers that need your OK wait on Home." }));
    setTimeout(() => input.focus(), 30);
  }

  // ---------------------------------------------------------------- Web

  const ENGINES = [
    { name: "Google", url: "https://www.google.com/search?q=" },
    { name: "Perplexity", url: "https://www.perplexity.ai/search?q=" },
    { name: "DuckDuckGo", url: "https://duckduckgo.com/?q=" },
    { name: "YouTube", url: "https://www.youtube.com/results?search_query=" },
    { name: "Wikipedia", url: "https://en.wikipedia.org/w/index.php?search=" },
  ];
  let webState = { q: "", results: null, note: "", page: null };

  function drawWeb() {
    const input = el("input", { type: "search", class: "field", placeholder: "Search the web", "aria-label": "Search the web", value: webState.q });
    const form = el("form", { class: "dock-search" }, input, el("button", { type: "submit", class: "primary", text: "Search" }));
    const engines = el("div", { class: "dock-engines" }, el("span", { class: "muted small", text: "Or open in" }),
      ...ENGINES.map((e) => {
        const b = el("button", { type: "button", class: "chip-btn", text: e.name });
        b.addEventListener("click", () => { const q = input.value.trim(); if (q) window.open(e.url + encodeURIComponent(q), "_blank", "noopener"); else input.focus(); });
        return b;
      }));
    const out = el("div", { class: "dock-web" });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (!q) return;
      webState = { q, results: null, note: "", page: null };
      if (!host.webSearch) { window.open(ENGINES[0].url + encodeURIComponent(q), "_blank", "noopener"); return; }
      out.replaceChildren(el("p", { class: "muted", text: "Searching..." }));
      try {
        const d = await host.webSearch(q);
        webState.results = d.results;
        webState.note = d.note || "";
      } catch (err) { webState.note = errorText(err); webState.results = []; }
      drawWebOut(out);
    });
    body.replaceChildren(form, engines, out);
    drawWebOut(out);
    if (!webState.q) setTimeout(() => input.focus(), 30);
  }

  function hostName(url) { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } }

  function drawWebOut(out) {
    if (webState.page) {
      const p = webState.page;
      const back = el("button", { type: "button", class: "quiet small", text: "Back to results" });
      back.addEventListener("click", () => { webState.page = null; drawWebOut(out); });
      const text = el("div", { class: "dock-reader" });
      for (const line of (p.text || "").split("\n")) {
        if (line.startsWith("## ")) text.append(el("h4", { text: line.slice(3) }));
        else if (line.trim()) text.append(el("p", { text: line.replace(/^- /, "• ") }));
      }
      out.replaceChildren(el("div", { class: "dock-row" }, back,
        el("a", { href: p.url, target: "_blank", rel: "noopener noreferrer", class: "conn-link" }, "Open the page", icon("external"))),
        el("h3", { class: "dock-h", text: p.title || hostName(p.url) }), text);
      return;
    }
    if (!webState.results) {
      out.replaceChildren(el("p", { class: "muted small", text: host.webSearch
        ? "Results show here, and Read opens a page right in the panel, as plain text."
        : "Search opens in a new tab. Websites can't be shown inside AIRTAC itself." }));
      return;
    }
    const list = el("ol", { class: "dock-results" }, ...webState.results.map((r) => {
      const read = el("button", { type: "button", class: "quiet small", text: "Read" });
      read.addEventListener("click", async () => {
        read.textContent = "Opening...";
        try { webState.page = await host.readPage(r.url); drawWebOut(out); out.scrollIntoView({ block: "start" }); }
        catch (err) { read.textContent = "Couldn't open it"; read.title = errorText(err); }
      });
      return el("li", {}, el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", text: r.title }),
        el("small", { class: "muted", text: hostName(r.url) }), r.snippet ? el("p", { text: r.snippet }) : "", el("div", { class: "dock-row" }, read));
    }));
    out.replaceChildren(webState.note ? el("p", { class: "muted small", text: webState.note }) : "", list);
  }

  // ---------------------------------------------------------------- AI apps

  async function loadAi(force = false) {
    if (state.ai && !force) return state.ai;
    state.ai = await host.aiApps();
    return state.ai;
  }

  function pins() {
    const saved = store.get("airtac-ai-pins", null);
    return Array.isArray(saved) ? saved : (state.ai ? state.ai.pinned : []);
  }

  function setPin(name, on) {
    const list = pins().filter((n) => n !== name);
    if (on) list.push(name);
    store.set("airtac-ai-pins", list);
    drawHomeAi();
  }

  const HUES = ["#5aa9e6", "#7fc8a9", "#e6b04a", "#c38fff", "#ef7d7d", "#6cc4c4", "#f29e6d", "#a3b8ff"];
  function hue(name) { let h = 0; for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return HUES[h % HUES.length]; }

  function mark(a) {
    // The app's logo (saved in web/logos, never fetched from the internet), or a two-letter badge.
    const badge = () => {
      const m = el("span", { class: "ai-mark", "aria-hidden": "true", text: a.mark || a.name.slice(0, 2) });
      m.style.setProperty("--hue", hue(a.name));
      return m;
    };
    const src = a.logo || a.favicon;    // the logo file, else the site's own icon (Advance), else the badge
    if (!src) return badge();
    const img = el("img", { src: `./${src}`, alt: "", loading: "lazy", decoding: "async" });
    const tile = el("span", { class: "ai-mark has-logo", "aria-hidden": "true" }, img);
    img.addEventListener("error", () => tile.replaceWith(badge()), { once: true });
    return tile;
  }

  function openAi(event, a) {
    if (host.openUrl) host.openUrl(event, a.url);
  }

  function aiTile(a) {
    const t = el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer", class: "ai-tile", title: a.about ? `${a.name}: ${a.about}` : a.name },
      mark(a), el("span", { text: a.name }));
    t.style.setProperty("--hue", hue(a.name));
    t.addEventListener("click", (e) => openAi(e, a));
    return t;
  }

  // Home: the starred apps, and a tile that opens the whole list in the panel.
  async function drawHomeAi() {
    const box = $id("ai-tools");
    if (!box) return;
    try { await loadAi(); } catch { box.closest("section").hidden = true; return; }
    const by = new Map(state.ai.apps.map((a) => [a.name, a]));
    const chosen = pins().map((n) => by.get(n)).filter(Boolean);
    const all = el("button", { type: "button", class: "ai-tile ai-all", title: "All AI apps (in the side panel)" },
      el("span", { class: "ai-mark", "aria-hidden": "true" }, icon("sparkles")), el("span", { text: `All ${state.ai.apps.length}` }));
    all.addEventListener("click", () => openTab("ai"));
    box.replaceChildren(...chosen.map(aiTile), all);
    box.closest("section").hidden = false;
  }

  async function drawAi() {
    body.replaceChildren(el("p", { class: "muted", text: "Loading..." }));
    try { await loadAi(); } catch (err) { body.replaceChildren(el("p", { class: "muted", text: errorText(err) })); return; }
    const d = state.ai;
    const search = el("input", { type: "search", class: "field", placeholder: `Find among ${d.apps.length} AI apps`, "aria-label": "Find an AI app", value: state.aiQuery });
    const groups = el("div", { class: "dock-engines" });
    const list = el("div", { class: "dock-ai" });
    const keys = ["all", ...Object.keys(d.groups).filter((g) => d.apps.some((a) => a.group === g))];
    const labels = { all: "All", ...d.groups };
    const drawGroups = () => groups.replaceChildren(...keys.map((g) => {
      const b = el("button", { type: "button", class: "chip-btn", "aria-pressed": String(state.aiGroup === g), text: labels[g] });
      b.addEventListener("click", () => { state.aiGroup = g; drawGroups(); drawList(); });
      return b;
    }));
    const pinned = new Set(pins());
    const drawList = () => {
      const q = state.aiQuery.toLowerCase();
      const shown = d.apps.filter((a) => (state.aiGroup === "all" || a.group === state.aiGroup)
        && (!q || `${a.name} ${a.about || ""}`.toLowerCase().includes(q)));
      list.replaceChildren(...(shown.length ? shown.map((a) => {
        const star = el("button", { type: "button", class: "icon-btn small ai-star", "aria-pressed": String(pinned.has(a.name)),
          "aria-label": `${pinned.has(a.name) ? "Remove" : "Add"} ${a.name} ${pinned.has(a.name) ? "from" : "to"} Home`, title: "Show on Home" }, icon("star"));
        star.addEventListener("click", () => {
          const on = !pinned.has(a.name);
          if (on) pinned.add(a.name); else pinned.delete(a.name);
          setPin(a.name, on);
          star.setAttribute("aria-pressed", String(on));
        });
        const row = el("div", { class: "ai-row" },
          el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer", class: "ai-row-link" }, mark(a),
            el("span", {}, el("strong", { text: a.name }), el("small", { text: a.about || hostName(a.url) }))), star);
        row.querySelector("a").addEventListener("click", (e) => openAi(e, a));
        if (a.mine && host.removeApp) {
          const rm = el("button", { type: "button", class: "icon-btn small", "aria-label": `Remove ${a.name}`, title: "Remove" }, icon("close"));
          rm.addEventListener("click", async () => { try { state.ai.apps = await host.removeApp(a); drawList(); drawHomeAi(); } catch { /* shown on retry */ } });
          row.append(rm);
        }
        return row;
      }) : [el("p", { class: "muted", text: "No app matches. Add it below." })]));
    };
    search.addEventListener("input", () => { state.aiQuery = search.value; drawList(); });
    // Add your own
    const name = el("input", { class: "field", placeholder: "Name", "aria-label": "App name", maxlength: "40" });
    const url = el("input", { class: "field", type: "url", placeholder: "https://", "aria-label": "App address" });
    const note = el("p", { class: "conn-msg", role: "status" });
    const add = el("form", { class: "dock-add" }, name, url, el("button", { type: "submit", class: "quiet", text: "Add" }));
    add.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!name.value.trim() || !/^https:\/\/[^\s<>"]+$/.test(url.value.trim())) {
        note.textContent = "Give a name and an address that starts with https://"; note.className = "conn-msg bad"; return;
      }
      try {
        state.ai.apps = await host.addApp({ name: name.value.trim(), url: url.value.trim() });
        setPin(name.value.trim(), true);
        pinned.add(name.value.trim());
        name.value = url.value = "";
        note.textContent = "Added, and starred for Home."; note.className = "conn-msg ok";
        drawList();
      } catch (err) { note.textContent = errorText(err); note.className = "conn-msg bad"; }
    });
    drawGroups();
    drawList();
    body.replaceChildren(search, groups, list,
      el("details", { class: "dock-more" }, el("summary", { text: "Add an app" }), add, note),
      el("p", { class: "muted small", text: host.linkNote || "Opens in a new tab. Star an app to keep it on Home." }));
  }

  // ---------------------------------------------------------------- Today

  async function drawToday() {
    body.replaceChildren(el("p", { class: "muted", text: "Loading..." }));
    let events = [];
    try { events = await host.schedule(); } catch (err) { body.replaceChildren(el("p", { class: "muted", text: errorText(err) })); return; }
    const now = new Date();
    const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const next = events.filter((e) => !e.done && e.date >= iso).sort((a, b) => (a.date + (a.time || "")).localeCompare(b.date + (b.time || ""))).slice(0, 8);
    const day = (d) => {
      const days = Math.round((new Date(`${d}T00:00`) - new Date(`${iso}T00:00`)) / 864e5);
      if (days === 0) return "Today";
      if (days === 1) return "Tomorrow";
      return new Date(`${d}T00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    };
    const list = next.length ? el("ol", { class: "dock-events" }, ...next.map((e) => el("li", { class: e.date === iso ? "now" : "" },
      el("span", { class: "dock-when", text: day(e.date) + (e.time ? `, ${e.time}` : "") }), el("strong", { text: e.title })))) :
      el("p", { class: "muted", text: "Nothing coming up. Add exams, deadlines and classes in the Schedule app." });
    body.replaceChildren(list, el("a", { href: "#app-schedule", class: "conn-link", text: "Open the schedule" }));
    body.querySelector("a[href='#app-schedule']").addEventListener("click", (e) => { e.preventDefault(); location.hash = "app-schedule"; });
  }

  // ---------------------------------------------------------------- Tokens (Advance)

  async function drawTokens() {
    body.replaceChildren(el("p", { class: "muted", text: "Loading..." }));
    let s;
    try { s = await host.tokens(); } catch (err) { body.replaceChildren(el("p", { class: "muted", text: errorText(err) })); return; }
    const fmt = (n) => Number(n || 0).toLocaleString();
    const max = Math.max(1, ...s.week.map((d) => d.tokens));
    const spark = el("div", { class: "tok-week", role: "img", "aria-label": `This week: ${fmt(s.week_total)} tokens` },
      ...s.week.map((d) => {
        const b = el("i", { title: `${d.day}: ${fmt(d.tokens)}` });
        b.style.height = `${Math.max(4, (d.tokens / max) * 100)}%`;
        return b;
      }));
    const note = el("p", { class: "conn-msg", role: "status" });
    const save = async (bodyJson) => {
      note.textContent = "Saving..."; note.className = "conn-msg";
      try { await host.saveTokens(bodyJson); note.textContent = "Saved."; note.className = "conn-msg ok"; }
      catch (err) { note.textContent = errorText(err); note.className = "conn-msg bad"; }
    };
    const len = el("div", { class: "tok-len", role: "radiogroup", "aria-label": "Answer length" }, ...Object.entries(s.lengths).map(([k, label]) => {
      const b = el("button", { type: "button", class: "chip-btn", role: "radio", "aria-checked": String(s.length === k), text: label });
      b.addEventListener("click", () => {
        for (const x of len.children) x.setAttribute("aria-checked", String(x === b));
        save({ length: k });
      });
      return b;
    }));
    const rows = s.tiers.map((t) => {
      const fill = el("i");
      fill.style.width = t.limit ? `${Math.min(100, (t.used / t.limit) * 100)}%` : t.used ? "100%" : "0%";
      const limit = el("input", { type: "number", class: "field", min: "0", step: "1000", value: t.limit || "", placeholder: "No limit",
        "aria-label": `Daily limit for ${t.label}` });
      limit.addEventListener("change", () => save({ limits: { [t.tier]: Number(limit.value || 0) } }));
      return el("div", { class: `tok-row ${t.paused ? "paused" : ""} ${t.local ? "local" : ""}` },
        el("div", { class: "tok-top" }, el("strong", { text: t.label }),
          el("span", { text: t.paused ? "paused till midnight" : `${fmt(t.used)}${t.limit ? ` of ${fmt(t.limit)}` : ""}` })),
        el("div", { class: "tok-track" }, fill),
        t.local ? el("small", { class: "muted", text: "Free and private: never limited." }) : el("label", { class: "tok-limit" }, el("span", { text: "Daily limit" }), limit));
    });
    body.replaceChildren(
      el("div", { class: "tok-sum" }, el("div", {}, el("strong", { text: fmt(s.today) }), el("span", { text: "tokens today" })),
        el("div", {}, el("strong", { text: fmt(s.cloud_today) }), el("span", { text: "in the cloud" })), spark),
      el("h3", { class: "dock-h", text: "Answer length" }), len,
      el("p", { class: "muted small", text: "Short saves the most. It applies to every brain." }),
      el("h3", { class: "dock-h", text: "Each brain today" }), ...rows, note,
      el("p", { class: "muted small", text: "A cloud brain that reaches its limit is skipped until midnight, and the next one answers. 0 or empty means no limit." }));
  }

  // ---------------------------------------------------------------- command palette (Ctrl+K)

  const pal = el("div", { class: "palette", hidden: "", role: "dialog", "aria-modal": "true", "aria-label": "Command palette" });
  const palInput = el("input", { class: "palette-input", type: "text", placeholder: host.find ? "Search everything: chats, notes, files, apps, settings" : "Open a mode, an app or an AI app", "aria-label": "Search commands",
    autocomplete: "off", spellcheck: "false", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-list" });
  const palList = el("ul", { class: "palette-list", id: "palette-list", role: "listbox" });
  // Search everything (when the host offers it): filters by kind of result.
  const FILTERS = host.find ? [["all", "All"], ["commands", "Commands"], ["chats", "Chats"], ["notes", "Notes"], ["knowledge", "Knowledge"],
    ["schedule", "Schedule"], ["files", "Files"], ["apps", "Apps"], ["settings", "Settings"]] : [];
  const KIND = { chats: ["Chat", "clock"], notes: ["Note", "steps"], knowledge: ["Knowledge", "library"], schedule: ["Schedule", "calendar"],
    files: ["File", "folder"], apps: ["App", "grid"], settings: ["Setting", "gear"] };
  const palFilters = el("div", { class: "palette-filters", role: "tablist", "aria-label": "Show only" });
  const palBox = el("div", { class: "palette-box" }, el("div", { class: "palette-field" }, icon("command"), palInput, el("kbd", { text: "Esc" })), palFilters, palList,
    el("p", { class: "palette-foot" }, el("kbd", { text: "↑" }), el("kbd", { text: "↓" }), " to move, ", el("kbd", { text: "Enter" }), " to open"));
  pal.append(palBox);
  document.body.append(pal);
  let items = [];
  let pick = 0;
  let filter = "all";
  let found = { q: "", results: [], error: "" };
  let findSeq = 0;
  let findTimer = 0;
  const RECENT = "airtac-recent-finds";
  const recents = () => { try { return JSON.parse(localStorage.getItem(RECENT) || "[]").slice(0, 8); } catch { return []; } };
  function remember(q) {
    q = q.trim();
    if (q.length < 2) return;
    try { localStorage.setItem(RECENT, JSON.stringify([q, ...recents().filter((x) => x !== q)].slice(0, 8))); } catch { /* fine without */ }
  }

  function drawFilters() {
    palFilters.replaceChildren(...FILTERS.map(([id, label]) => {
      const b = el("button", { type: "button", class: "chip-btn", role: "tab", "aria-selected": String(filter === id), text: label, tabindex: "-1" });
      b.addEventListener("click", () => { filter = id; drawFilters(); find(); palInput.focus(); });
      return b;
    }));
  }

  function find() {
    clearTimeout(findTimer);
    const q = palInput.value.trim();
    if (!host.find || q.length < 2 || filter === "commands") { found = { q, results: [], error: "" }; drawPalette(); return; }
    findTimer = setTimeout(async () => {
      const seq = ++findSeq;
      try {
        const r = await host.find(q, filter === "all" ? "" : filter);
        if (seq === findSeq) found = { q, results: r.results || [], error: "" };
      } catch (e) {
        if (seq === findSeq) found = { q, results: [], error: typeof errorText === "function" ? errorText(e) : "Search failed." };
      }
      if (seq === findSeq) drawPalette();
    }, 180);
  }

  function commands() {
    const out = [];
    let n = 0;
    for (const a of document.querySelectorAll(".drawer-nav:not(.drawer-apps) a[data-view]")) {
      n++;
      const name = a.dataset.label || a.textContent.trim();
      const ic = (a.querySelector("use")?.getAttribute("href") || "#i-home").slice(3);
      out.push({ kind: "Mode", name, icon: ic, hint: n <= 9 ? `Alt+${n}` : "", run: () => { setHash(a.dataset.view); showView(a.dataset.view); } });
    }
    for (const app of (typeof APPS !== "undefined" ? APPS : [])) {
      out.push({ kind: "App", name: app.name, icon: app.icon || "spark", run: () => { location.hash = `app-${app.id}`; } });
    }
    for (const t of TABS) out.push({ kind: "Panel", name: t.label, icon: t.icon, hint: t.id === state.tab ? "Ctrl+B" : "", run: () => openTab(t.id) });
    for (const a of (state.ai ? state.ai.apps : [])) out.push({ kind: "AI app", name: a.name, about: a.about, mark: a, run: (e) => {
      if (host.openUrl && host.here) host.openUrl(e, a.url); else window.open(a.url, "_blank", "noopener");
    } });
    return out;
  }

  function score(c, q) {
    const n = c.name.toLowerCase();
    if (!q) return c.kind === "AI app" ? 1 : 2;
    if (n.startsWith(q)) return 4;
    if (n.includes(q)) return 3;
    return `${c.kind} ${c.about || ""}`.toLowerCase().includes(q) ? 1 : 0;
  }

  function drawPalette() {
    const raw = palInput.value.trim();
    const q = raw.toLowerCase();
    const searching = host.find && q.length >= 2;
    let cmds = filter === "all" || filter === "commands"
      ? commands().filter((c) => !(searching && c.kind === "AI app"))     // apps come from Search everything instead
        .map((c) => ({ c, s: score(c, q) })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s).map((x) => x.c) : [];
    cmds = cmds.slice(0, !q ? 14 : searching && filter === "all" ? 5 : 12);
    const hits = searching && found.q === raw ? found.results.map((r) => ({
      kind: KIND[r.kind]?.[0] || r.kind, name: r.title, about: r.snippet, icon: KIND[r.kind]?.[1] || "search",
      mark: r.kind === "apps" && r.logo ? { name: r.title, logo: r.logo.startsWith("logos/") ? r.logo : "", favicon: r.logo.startsWith("logos/") ? "" : r.logo } : null,
      run: () => { remember(raw); host.go(r); } })) : [];
    const recent = !q && host.find ? recents().map((t) => ({ kind: "Recent search", name: t, icon: "clock", keep: true,
      run: () => { palInput.value = t; pick = 0; find(); drawPalette(); } })) : [];
    items = [...recent, ...cmds, ...hits];
    pick = Math.min(pick, Math.max(0, items.length - 1));
    const waiting = searching && found.q !== raw;
    const empty = found.error && searching ? found.error : waiting ? "Searching..." : "Nothing matches.";
    palList.replaceChildren(...(items.length ? items.map((c, i) => {
      const words = el("span", { class: "palette-words" }, el("span", { class: "palette-name" }, ...(typeof highlight === "function" ? highlight(c.name, raw) : [c.name])),
        c.about ? el("span", { class: "palette-about" }, ...(typeof highlight === "function" ? highlight(c.about, raw) : [c.about])) : "");
      const li = el("li", { role: "option", id: `pal-${i}`, "aria-selected": String(i === pick), class: "palette-item" },
        c.mark ? mark(c.mark) : el("span", { class: "palette-ic" }, icon(c.icon)),
        words, el("span", { class: "palette-kind", text: c.hint || c.kind }));
      li.addEventListener("pointerenter", () => { pick = i; mark2(); });
      li.addEventListener("click", (e) => runItem(i, e));
      return li;
    }) : [el("li", { class: "palette-empty", text: empty })]));
    if (waiting && items.length) palList.append(el("li", { class: "palette-empty", text: "Searching your chats, notes and files..." }));
    palInput.setAttribute("aria-activedescendant", items.length ? `pal-${pick}` : "");
  }
  function mark2() {
    [...palList.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === pick)));
    palInput.setAttribute("aria-activedescendant", `pal-${pick}`);
    palList.children[pick]?.scrollIntoView({ block: "nearest" });
  }

  function runItem(i, e) {
    const c = items[i];
    if (!c) return;
    if (c.keep) { c.run(); return; }    // a recent search fills the box and stays open
    if (palInput.value.trim()) remember(palInput.value);
    closePalette();
    c.run(e || new Event("click"));
  }

  let lastFocus = null;
  async function openPalette() {
    lastFocus = document.activeElement;
    pal.hidden = false;
    pal.classList.remove("on");
    void pal.offsetWidth;
    pal.classList.add("on");
    palInput.value = "";
    pick = 0;
    filter = "all";
    found = { q: "", results: [], error: "" };
    drawFilters();
    drawPalette();
    palInput.focus();
    if (!state.ai) { try { await loadAi(); drawPalette(); } catch { /* the rest still works */ } }
  }
  function closePalette() {
    pal.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  palInput.addEventListener("input", () => { pick = 0; drawPalette(); find(); });
  palInput.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); pick = Math.min(items.length - 1, pick + 1); mark2(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); pick = Math.max(0, pick - 1); mark2(); }
    else if (e.key === "Enter") { e.preventDefault(); runItem(pick, e); }
    else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
  });
  pal.addEventListener("pointerdown", (e) => { if (e.target === pal) closePalette(); });

  // ---------------------------------------------------------------- keys

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); if (pal.hidden) openPalette(); else closePalette(); }
    else if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") { e.preventDefault(); setOpen(!state.open); }
    else if (e.key === "Escape" && state.open && pal.hidden && !document.querySelector("dialog[open]")) {
      if (window.matchMedia("(max-width: 899px)").matches || panel.contains(document.activeElement)) setOpen(false);
    }
  });

  // The menu shows each mode's key, so they're easy to learn (not on touch screens).
  let n = 0;
  for (const a of document.querySelectorAll(".drawer-nav:not(.drawer-apps) a[data-view]")) {
    n++;
    a.dataset.label = a.textContent.trim();
    if (n <= 9) a.append(el("kbd", { class: "nav-key", text: `Alt+${n}`, "aria-hidden": "true" }));
  }

  $id("find-open")?.addEventListener("click", () => openPalette());

  window.AirtacDock = { open: openTab, toggle: () => setOpen(!state.open), palette: openPalette, refreshHome: drawHomeAi };
  drawHomeAi();
})();
