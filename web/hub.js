// AI apps, Apps, Social, Passwords and Settings pages, and the chat header (brain + tokens).
// Loaded before app.js; uses its helpers ($, el, icon, api, errorText, save, load, applyTheme, showView)
// only inside functions, which run after both files are in.
//
// Safety rules kept here:
// - a password is never put in the page's text: masked; Show (vault unlocked) puts it there for 10 seconds;
// - on Windows the PC copies a password itself and clears it after 20 seconds (it never reaches this page);
//   elsewhere this page copies it and tries to clear it after 20 seconds;
// - links open only the catalog's own addresses; a saved login is never put in an address.

const hub = { apps: null, group: { ai: "all", apps: "all", social: "all" }, q: { ai: "", apps: "", social: "" }, probing: false, pw: null, pwEdit: null, pwTick: null, choices: null, settings: null, social: {} };

const fmt = (n) => Number(n || 0).toLocaleString("en-IN");

// An app's logo: the file in web/logos, else the site's own icon (fetched once by AIRTAC), else a letter badge.
function appLogo(a, size = "md") {
  const badge = () => el("span", { class: `logo-tile ${size} badge-tile`, "aria-hidden": "true", text: (a && (a.mark || a.name.slice(0, 2))) || "?" });
  const src = a && (a.logo || a.favicon);
  if (!src) return badge();
  const img = el("img", { src: `./${src}`, alt: "", loading: "lazy", decoding: "async" });
  const tile = el("span", { class: `logo-tile ${size}${a.logo ? "" : " fav-tile"}`, "aria-hidden": "true" }, img);
  img.addEventListener("error", () => tile.replaceWith(badge()), { once: true });
  return tile;
}

function hubLoad(name) {
  if (name === "ai" || name === "apps" || name === "social") return loadCatalogPage(name);
  if (name === "tabs" && window.AirtacTabs) return window.AirtacTabs.show();
  if (name === "passwords") return loadPasswords();
  if (name === "settings") return loadSettings();
  if (name === "devtools") return loadDevTools();
}

// ---------------------------------------------------------------- Dev tools page

async function loadDevCcPlugins() {
  try {
    const d = await api("/api/plugins/ccplugins", undefined, 10000);
    const on = d.plugins.filter((c) => c.on);
    $("dev-ccplugins").replaceChildren(...(on.length ? [
      ...(on.some((c) => c.official) ? [el("li", {}, el("code", { text: d.marketplace }))] : []),
      ...on.map((c) => el("li", {}, el("strong", { text: `${c.name}: ` }), el("code", { text: c.how })))]
      : [el("li", { class: "muted", text: "None switched on yet (Brains and plugins)." })]));
  } catch { /* the rest of Dev tools works without it */ }
}

async function loadDevTools() {
  loadDevCcPlugins();
  let s;
  try { s = await api("/api/plugins/devtools/status", undefined, 15000); }
  catch (e) { return devMsg(errorText(e)); }
  devMsg("");
  if (!s.here) {
    $("dev-env").textContent = "";
    $("dev-cards").replaceChildren(el("div", { class: "panel pc-only" }, icon("code"), el("p", { text: s.message })));
    return;
  }
  const have = Object.entries(s.env).filter(([, v]) => v).map(([k]) => k);
  const miss = Object.entries(s.env).filter(([, v]) => !v).map(([k]) => k);
  $("dev-env").replaceChildren(el("span", { class: "muted small", text: `On this PC: ${have.join(", ") || "nothing detected"}` }),
    miss.length ? el("span", { class: "muted small", text: ` · not found: ${miss.join(", ")}` }) : "");
  $("dev-cards").replaceChildren(...s.tools.map(devCard));
}

// The AIRTAC doctor: every check with a light (green ok, amber warning, red fail) and what to do.
async function runDoctor() {
  const btn = $("airtac-doctor"), sum = $("airtac-doctor-sum"), out = $("airtac-doctor-out");
  btn.disabled = true; btn.textContent = "Checking..."; sum.hidden = false; sum.textContent = "Running the checks (about 15 seconds)...";
  try {
    const r = await api("/api/plugins/devtools/doctor", {}, 200000);
    if (r.error) { sum.textContent = r.error; out.hidden = true; return; }
    sum.textContent = r.failed ? `${r.failed} to fix, ${r.warnings} warning(s).` : `All good${r.warnings ? `, ${r.warnings} warning(s)` : ""}.`;
    sum.className = "small " + (r.failed ? "doctor-bad" : "doctor-good");
    const led = { ok: "connected", warn: "error", fail: "fail" };   // green, amber, red
    let group = "";
    const rows = [];
    for (const c of r.checks) {
      if (c.group !== group) { group = c.group; rows.push(el("li", { class: "doctor-group", text: group })); }
      rows.push(el("li", { class: `doctor-row ${c.state}` }, el("i", { class: `led ${led[c.state]}`, title: c.state }),
        el("div", {}, el("b", { text: c.name }), el("span", { class: "muted", text: c.detail ? ` ${c.detail}` : "" }),
          c.fix ? el("div", { class: "doctor-fix", text: c.fix }) : "")));
    }
    out.replaceChildren(...rows); out.hidden = false;
  } catch (e) {
    sum.textContent = errorText(e); out.hidden = true;
  } finally {
    btn.disabled = false; btn.textContent = "Run doctor";
  }
}

function devMsg(t) { $("dev-msg").textContent = t; $("dev-msg").hidden = !t; }

function devCard(t) {
  const state = el("span", { class: `api-state ${t.installed ? "connected" : "off"}` },
    el("i", { class: `led ${t.installed ? "connected" : "off"}` }), t.installed ? "Installed" : "Not set up");
  const cmd = el("code", { class: "dev-cmd", text: t.command });
  const out = el("pre", { class: "dev-out", hidden: "" });
  const run = el("button", { type: "button", class: "primary small-btn" }, icon("code"), t.installed ? "Run again" : "Run");
  run.disabled = !t.ready;
  run.title = t.ready ? "Runs the command above on this PC" : `Set up first: ${t.needs.join(", ")}`;
  let armed = false;
  run.addEventListener("click", async () => {
    if (!armed) { armed = true; run.lastChild.textContent = "Press again to run"; setTimeout(() => { armed = false; run.lastChild.textContent = t.installed ? "Run again" : "Run"; }, 4000); return; }
    armed = false; run.disabled = true; run.lastChild.textContent = "Running..."; out.hidden = false; out.textContent = "";
    // Live output: the PC runs the command; this page reads the new lines every second.
    try {
      let r = await api("/api/plugins/devtools/run", { tool: t.id }, 20000);
      for (let n = 0; ; n++) {
        if (r.lines.length) { out.textContent += (out.textContent ? "\n" : "") + r.lines.join("\n"); out.scrollTop = out.scrollHeight; }
        if (r.done || n > 1800) break;
        await sleep(1000);
        r = await api(`/api/plugins/devtools/run?after=${r.next}`, undefined, 15000);
      }
      out.textContent += r.ok ? `\n\nDone.${r.after ? " Next: " + r.after : ""}` : "\n\nIt didn't finish. The output above says why.";
      out.scrollTop = out.scrollHeight;
      if (r.done) loadDevTools();
    } catch (e) { out.textContent += `\n${errorText(e)}`; run.disabled = false; run.lastChild.textContent = "Run"; }
  });
  const links = el("div", { class: "card-actions" }, run);
  if (t.get) links.append(el("a", { href: t.get, target: "_blank", rel: "noopener noreferrer", class: "quiet small-btn" }, icon("external"), "About"));
  return el("article", { class: "app-card dev-card" },
    el("div", { class: "app-card-head" }, el("div", {}, el("h3", { text: t.name }), state)),
    el("p", { class: "app-card-about", text: t.about }),
    el("p", { class: "muted tiny", text: "Command:" }), cmd, links, out);
}

async function catalog() {
  const d = await api("/api/plugins/desktop/tiles", undefined, 10000);
  launch = { ...launch, ...d };
  hub.apps = d;
  return d;
}

const SECTION_LIST = { ai: "ai_all", apps: "apps", social: "social" };
const sectionApps = (section) => (hub.apps ? hub.apps[SECTION_LIST[section]] || [] : []);
const allCatalogApps = () => ["ai", "apps", "social"].flatMap(sectionApps);

// ---------------------------------------------------------------- opening an app

async function openCatalogApp(event, a, page = "home") {
  if (a.app) { event.preventDefault(); location.hash = `app-${a.app}`; return; }   // one of AIRTAC's own pages
  if (!launch.here) return;                 // the link opens it on this device (phone)
  event.preventDefault();
  try { await api("/api/plugins/desktop/open", { id: a.id, page }, 10000); }
  catch { window.open(page === "login" && a.login ? a.login : a.url, "_blank", "noopener,noreferrer"); }
}

function openInTab(a) {
  if (!window.AirtacTabs) return;
  window.AirtacTabs.open(a);
  setHash("tabs");
  showView("tabs");
}

function appCard(a, kind) {
  const actions = el("div", { class: "card-actions" });
  if (a.app) {
    const go = el("a", { href: `#app-${a.app}`, class: "primary small-btn", text: "Open" });
    actions.append(go);
  } else {
    if (a.open_as === "tab") {
      const tab = el("button", { type: "button", class: "primary small-btn" }, icon("tabs"), "Open in tab");
      tab.addEventListener("click", () => openInTab(a));
      actions.append(tab);
    }
    const label = kind === "ai" && launch.here && launch.chrome ? "Open in Chrome ↗" : "Opens in browser ↗";
    const open = el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer", class: `${a.open_as === "tab" ? "quiet" : "primary"} small-btn`, text: label });
    open.addEventListener("click", (e) => openCatalogApp(e, a));
    actions.append(open);
    if (launch.here && kind === "social") {   // AI apps: logins are on the Passwords page
      const signin = el("button", { type: "button", class: "quiet small-btn" }, icon("key"), "Sign in with saved login");
      signin.addEventListener("click", () => signInSaved(a, signin));
      actions.append(signin);
    }
  }
  if (launch.here && !a.fixed) {
    const edit = el("button", { type: "button", class: "icon-btn small card-edit", "aria-label": `Edit ${a.name}`, title: "Edit" }, icon("edit"));
    edit.addEventListener("click", () => editApp(a));
    actions.append(edit);
  }
  // Why it opens where it does: the site's own answer to the embed check, shown so nothing is a mystery.
  const how = a.app ? "" : a.open_as === "tab" ? "Opens as a tab inside AIRTAC." : a.why || "";
  return el("article", { class: "app-card", "data-open": a.open_as || "" },
    el("div", { class: "app-card-head" }, appLogo(a),
      el("div", {}, el("h3", { text: a.name }), el("p", { class: "muted small", text: hub.apps?.groups?.[a.group] || "" }))),
    el("p", { class: "app-card-about", text: a.about || "" }),
    a.app ? "" : el("p", { class: `muted tiny embed-why ${a.open_as === "tab" ? "ok" : ""}`, title: a.why || "", text: how }),
    actions);
}

async function loadCatalogPage(section) {
  const note = $(`${section}-note`);
  try { await catalog(); } catch (e) { note.textContent = errorText(e); return; }
  const d = hub.apps;
  const list = sectionApps(section);
  const groups = ["all", ...Object.keys(d.groups).filter((g) => list.some((a) => a.group === g))];
  if (!groups.includes(hub.group[section])) hub.group[section] = "all";
  $(`${section}-groups`).replaceChildren(...groups.map((g) => {
    const b = el("button", { type: "button", class: "chip-btn", role: "tab", "aria-selected": String(hub.group[section] === g), text: g === "all" ? "All" : d.groups[g] });
    b.addEventListener("click", () => { hub.group[section] = g; loadCatalogPage(section); });
    return b;
  }));
  for (const id of [`${section}-add`, `${section}-probe`]) $(id).hidden = !d.here;
  const tabs = list.filter((a) => a.open_as === "tab").length;
  const where = section === "ai" ? (d.here ? (d.chrome ? "open in AIRTAC's Chrome profile" : "open in your usual browser") : "open on this device")
    : (d.here ? `open in ${hub.settings?.social_browser === "chrome" ? "AIRTAC's Chrome profile" : "your default browser"} (change it in Settings)` : "open on this device");
  note.textContent = `${list.length} apps. ${tabs ? `${tabs} can open as tabs inside AIRTAC; the others ` : "They "}${where}.`
    + (d.offline ? "" : list.some((a) => a.probe === "due") && d.here ? " Checking which sites allow tabs..." : "");
  drawCatalogCards(section);
  if (d.here && !d.offline && list.some((a) => a.probe === "due") && !hub.probing) probeTabs(section, false);
}

function drawCatalogCards(section) {
  const q = (hub.q[section] || "").toLowerCase();
  const g = hub.group[section];
  const shown = sectionApps(section).filter((a) => (g === "all" || a.group === g) && (!q || `${a.name} ${a.about}`.toLowerCase().includes(q)));
  if (!shown.length) { $(`${section}-cards`).replaceChildren(el("p", { class: "muted", text: "No app matches." })); return; }
  // Grouped like a college portal: a heading per group, cards under it.
  const order = Object.keys(hub.apps.groups);
  const byGroup = new Map();
  for (const a of shown) { if (!byGroup.has(a.group)) byGroup.set(a.group, []); byGroup.get(a.group).push(a); }
  const blocks = [...byGroup.keys()].sort((x, y) => order.indexOf(x) - order.indexOf(y)).map((grp) =>
    el("section", { class: "catalog-group" }, el("h3", { class: "catalog-group-h", text: hub.apps.groups[grp] || grp }),
      el("div", { class: "app-cards" }, ...byGroup.get(grp).map((a) => appCard(a, section)))));
  $(`${section}-cards`).replaceChildren(...blocks);
}

async function probeTabs(section, force) {
  hub.probing = true;
  const btn = $(`${section}-probe`);
  btn.disabled = true;
  $(`${section}-note`).textContent = "Checking which sites allow being opened as a tab (their own security headers decide)...";
  try {
    const r = await api("/api/plugins/desktop/probe", { force }, 120000);
    await loadCatalogPage(section);
    if (force) toast({ title: `Checked ${r.checked} site${r.checked === 1 ? "" : "s"}`, body: `${r.tabs || 0} can open as tabs.` });
  } catch (e) { $(`${section}-note`).textContent = errorText(e); }
  finally { hub.probing = false; btn.disabled = false; }
}

// ---------------------------------------------------------------- Add, Edit, Remove (this PC only; writes apps.json)

function editApp(a, section) {
  const f = $("app-form");
  const sec = a ? a.section : section;
  f.reset();
  f.elements.id.value = a ? a.id : "";
  f.elements.name.value = a ? a.name : "";
  f.elements.url.value = a ? a.url || "" : "";
  f.elements.section.value = sec;
  f.elements.about.value = a ? a.about || "" : "";
  f.elements.tabs.value = a ? a.tabs || "auto" : "auto";
  f.elements.group.replaceChildren(...Object.entries(hub.apps.groups).map(([k, v]) => el("option", { value: k, text: v })));
  f.elements.group.value = a ? a.group : "mine";
  $("app-url-row").hidden = !!(a && a.app);
  f.elements.url.required = !(a && a.app);
  f.elements.section.disabled = !!(a && a.app);
  $("app-dialog-title").textContent = a ? `Edit ${a.name}` : "Add an app";
  $("app-remove").hidden = !a;
  $("app-form-msg").textContent = "";
  $("app-dialog").showModal();
}

async function saveAppForm(event) {
  event.preventDefault();
  const f = $("app-form");
  const body = Object.fromEntries(["id", "name", "url", "section", "group", "about", "tabs"].map((k) => [k, f.elements[k].value.trim()]));
  try {
    await api("/api/plugins/desktop/catalog", body, 15000);
    $("app-dialog").close();
    hub.apps = null;
    await loadCatalogPage(body.section);
    if (window.AirtacDock?.refreshHome) window.AirtacDock.refreshHome();
  } catch (e) { $("app-form-msg").textContent = errorText(e); }
}

async function removeAppForm() {
  const f = $("app-form");
  const name = f.elements.name.value;
  if (!confirm(`Remove ${name} from the list? A copy of the old list is kept in data\\backups\\apps.`)) return;
  try {
    await api("/api/plugins/desktop/catalog/remove", { id: f.elements.id.value }, 15000);
    $("app-dialog").close();
    await loadCatalogPage(f.elements.section.value);
  } catch (e) { $("app-form-msg").textContent = errorText(e); }
}

// "Sign in with saved login": first press copies the user name and opens the sign-in page, the next
// press copies the password. Nothing is typed into the website for you.
async function signInSaved(a, button) {
  try {
    const found = await api(`/api/plugins/passwords/for/${encodeURIComponent(a.id)}`, undefined, 10000);
    if (!found.exists) return note(`Set up the Passwords vault first, then save your ${a.name} login there.`, "passwords");
    if (!found.id) return note(`No ${a.name} login in the vault yet. Add it on the Passwords page (choose "${a.name}").`, "passwords");
    if (!found.unlocked && !(await unlockPrompt())) return;
    const step = hub.social[a.id];
    if (step && step.until > Date.now()) {
      await copySecret(found.id, "password");
      delete hub.social[a.id];
      button.lastChild.textContent = "Sign in with saved login";
      return;
    }
    await copySecret(found.id, "username");
    hub.social[a.id] = { until: Date.now() + 120000 };
    button.lastChild.textContent = "Copy password";
    openCatalogApp({ preventDefault() {} }, a, "login");
  } catch (e) { note(errorText(e)); }
}

function note(text, view) {
  toast({ title: text, body: view ? "" : "" });
  if (view) { setHash(view); showView(view); }
}

// ---------------------------------------------------------------- clipboard

async function copySecret(id, field) {
  const r = await api(`/api/plugins/passwords/entries/${id}/copy`, { field }, 10000);
  const what = field === "password" ? "Password" : "User name";
  if (r.copied === "pc") {
    toast({ title: `${what} copied`, body: field === "password" ? `Cleared from the clipboard in ${r.clear_after} seconds.` : "Paste it on the sign-in page." });
    return;
  }
  try { await navigator.clipboard.writeText(r.value); }
  catch { toast({ title: "The browser didn't allow copying.", body: "Click on the page and try again." }); return; }
  toast({ title: `${what} copied`, body: field === "password" ? `Cleared in ${r.clear_after} seconds if this tab can.` : "" });
  if (field === "password") {
    const value = r.value;
    setTimeout(async () => {
      try { if ((await navigator.clipboard.readText()) === value) await navigator.clipboard.writeText(""); }
      catch { try { await navigator.clipboard.writeText(""); } catch { /* the tab isn't focused: nothing more it can do */ } }
    }, r.clear_after * 1000);
  }
}

// The vault's gate, the Passwords page, Show and Change super password live in vault.js.

// ---------------------------------------------------------------- Settings page

const THEME_INFO = {
  crimson: ["Dark", "Gunmetal and crimson (default)"], day: ["Light", "Plain and bright"],
  contrast: ["High contrast", "Black, white and yellow; strongest outlines"], arc: ["Arc", "Navy and cyan"], gilded: ["Gilded", "Black, silver and gold"],
  aurora: ["Aurora", "Deep violet with teal light"],
  forest: ["Forest", "Pine green and moss"],
  sunset: ["Sunset", "Warm orange and pink"],
  ocean: ["Ocean", "Light sea blue (light)"],
  midnight: ["Midnight", "Ink blue with electric indigo"],
  sakura: ["Sakura", "Soft blossom pink (light)"],
};

async function loadSettingsData() {
  try { hub.settings = await api("/api/plugins/settings", undefined, 10000); } catch { hub.settings = hub.settings || null; }
  return hub.settings;
}

async function saveSetting(changes) {
  try {
    hub.settings = await api("/api/plugins/settings", changes, 10000);
    $("set-saved").textContent = "Saved.";
    setTimeout(() => { $("set-saved").textContent = ""; }, 2000);
  } catch (e) { $("set-saved").textContent = errorText(e); }
}

async function loadSettings() {
  const s = await loadSettingsData() || { theme: load("airtac-theme") || "crimson", session_token_limit: 20000, social_browser: "default", brain: "" };
  const current = document.documentElement.dataset.skin || s.theme;
  $("set-themes").replaceChildren(...Object.entries(THEME_INFO).map(([id, [name, about]]) => {
    const input = el("input", { type: "radio", name: "set-theme", value: id });
    input.checked = id === current;
    input.addEventListener("change", () => { applyTheme(id); saveSetting({ theme: id }); });
    return el("label", { class: "theme-card" }, input, el("span", { class: `swatch big ${id}` }), el("strong", { text: name }), el("small", { class: "muted", text: about }));
  }));
  loadAbout();
  $("set-limit").value = s.session_token_limit;
  $("set-browser").value = s.social_browser;
  $("set-engine").value = s.search_engine || "google";
  $("set-tone").value = s.tone || "friendly";
  await loadBrainChoices();
  $("set-brain").value = s.brain || "";
  drawApiTable();
  loadMemoryCache();
  loadAccount();
  if (typeof loadDevices === "function") loadDevices();
  loadWeatherSetting();
  try {
    const pw = await api("/api/plugins/passwords/status", undefined, 10000);
    $("set-pw-state").textContent = !pw.here ? "Vault works on this PC only." : !pw.exists ? "Not set up yet: create your super password." : `${pw.count} login${pw.count === 1 ? "" : "s"} saved. ${pw.unlocked ? "Unlocked." : "Locked."}`;
    $("set-pw-change").hidden = !pw.here;
    $("set-pw-change").textContent = pw.exists && !pw.migrate ? "Change super password" : "Create super password";
  } catch { $("set-pw-state").textContent = ""; }
}

// About you: a few lines every chat gets (memory.profile on the server).
async function loadAbout() {
  try {
    const r = await api("/api/plugins/memory/profile", undefined, 10000);
    for (const [k, v] of Object.entries(r.profile)) if ($("about-form").elements[k]) $("about-form").elements[k].value = v;
  } catch (e) { $("about-saved").textContent = errorText(e); }
}

async function saveAbout(event) {
  event.preventDefault();
  const f = $("about-form");
  const body = Object.fromEntries(["name", "studies", "college", "interests", "goals", "other"].map((k) => [k, f.elements[k].value.trim()]));
  try { await api("/api/plugins/memory/profile", body, 10000); $("about-saved").textContent = "Saved. Every chat knows this now."; }
  catch (e) { $("about-saved").textContent = errorText(e); }
}

async function drawApiTable() {
  let d;
  try { d = await api("/api/connections", undefined, 15000); }
  catch (e) {
    $("set-apis").replaceChildren(el("p", { class: "muted", text: e.kind === "locked" ? "Locked. Open APIs and keys and type your super password to see them." : errorText(e) }));
    return;
  }
  const rows = d.connections.filter((c) => c.fields.length);
  $("set-apis").replaceChildren(...rows.map((c) => {
    const f = c.fields[0];
    const state = el("span", { class: `api-state ${c.status}` }, el("i", { class: `led ${c.status}` }),
      c.status === "connected" ? "Connected" : c.status === "error" ? "Needs a look" : c.status === "saved" ? "Saved, not tested" : "No key");
    const msg = el("span", { class: "muted small api-msg", text: c.test ? c.test.message : "" });
    const test = el("button", { type: "button", class: "quiet small-btn", text: "Test" });
    test.disabled = !d.here || c.status === "off" || !c.can_test;
    test.title = !d.here ? "Keys can only be tested on the PC itself" : c.status === "off" ? "Add a key first" : "Make one real call with the saved key";
    test.addEventListener("click", async () => {
      test.disabled = true; msg.textContent = "Testing...";
      try { const r = await api(`/api/connections/${c.id}/test`, {}, 30000); msg.textContent = r.test ? r.test.message : ""; drawApiTable(); }
      catch (e) { msg.textContent = errorText(e); test.disabled = false; }
    });
    return el("div", { class: "api-row", role: "listitem" },
      c.logo ? appLogo({ logo: c.logo }, "sm") : appLogo({ name: c.name, mark: c.name.slice(0, 2) }, "sm"),
      el("div", { class: "api-name" }, el("strong", { text: c.name }), el("span", { class: "muted small", text: c.group })),
      el("code", { class: "api-key", text: f.set ? (f.shown || "••••") : "not set" }), state, test, msg);
  }));
}

// ---------------------------------------------------------------- the chat header: brain and tokens

// The brain menu (Part D): Automatic, then groups (Local, AIRTAC 1, Google, Groq, xAI, OpenRouter free,
// Nebius, Hugging Face, any other with a key). A group whose key isn't set shows "key needed" and can't
// be picked; one with a key says "key set". An empty Local group says "no models found — check Ollama".
// The pick is remembered per chat (this browser), and the server says when it fell back.
// Ali, 20 Oct: the picker looked empty because a slow or failed /api/brain_choices was swallowed; now it says so.
const brainKey = (sid = sessionId) => `airtac-brain:${sid}`;

function groupLabel(g) {
  const key = g.key === "needed" ? " · key needed" : g.key === "set" ? " · key set" : "";
  return `${g.label}${key}`;
}

function brainOptions(withAuto = "Automatic") {
  const out = [el("option", { value: "", text: withAuto })];
  if (hub.choicesError) {
    out.push(Object.assign(el("option", { value: "none:error", text: `Couldn't load the models: ${hub.choicesError}` }), { disabled: true }));
    return out;
  }
  for (const g of hub.choices?.groups || []) {
    const og = el("optgroup", { label: groupLabel(g) });
    for (const item of g.items) {
      const o = el("option", { value: item.id, text: `${shortModel(item.label)}${g.paid ? " · paid" : ""}` });
      o.disabled = g.key === "needed";
      og.append(o);
    }
    // Never a blank group: say why it's empty (Local: "no models found — check Ollama").
    const why = g.note || (g.id === "local" ? "no models found — check Ollama" : "no models listed yet");
    if (!g.items.length || (g.note && g.id === "local")) og.append(Object.assign(el("option", { value: `none:${g.id}`, text: why }), { disabled: true }));
    out.push(og);
  }
  return out;
}

function pickable(id) {
  return Boolean(id) && (hub.choices?.groups || []).some((g) => g.key !== "needed" && g.items.some((i) => i.id === id));
}

// Every picker on the page: Home's header, Settings, the side panel, and the Chat page's header (chat.js).
const brainSelects = () => ["brain-pick", "set-brain", "side-brain", "cp-brain"].map((id) => document.getElementById(id)).filter(Boolean);

async function loadBrainChoices(refresh = false) {
  try {
    hub.choices = refresh ? await api("/api/brain_choices/refresh", {}, 60000) : await api("/api/brain_choices", undefined, 20000);
    hub.choicesError = "";
  } catch (e) {
    hub.choicesError = errorText(e);
  }
  const keep = $("brain-pick").value || load(brainKey()) || hub.settings?.brain || "";
  for (const s of brainSelects()) {
    const was = s.value;
    s.replaceChildren(...brainOptions(s.id === "brain-pick" || s.id === "cp-brain" ? "Automatic" : "Automatic (the failover order)"));
    s.value = was;
  }
  $("brain-pick").value = pickable(keep) ? keep : "";
  if (window.Chat?.syncBrain) Chat.syncBrain();
  if (hub.choicesError) $("chat-model").textContent = "Couldn't load the model list";
  else if (!lastUsage.model) showExpectedBrain();
}

function rememberBrain(value) {
  $("brain-pick").value = value;
  save(brainKey(), value);
  showExpectedBrain();
}

// ---------------------------------------------------------------- the chat's side panel: model and plugins

function pluginChoice() {
  try { const v = JSON.parse(load("airtac-plugins") || "null"); return v && typeof v === "object" ? v : { mode: "auto", ids: [] }; }
  catch { return { mode: "auto", ids: [] }; }
}

// What the chat sends: undefined = automatic, [] = no tools, ["web", ...] = only those.
function chosenPlugins() {
  const c = pluginChoice();
  return c.mode === "none" ? [] : c.mode === "only" ? c.ids.slice(0, 40) : undefined;
}

function pluginLabel() {
  const c = pluginChoice();
  $("chat-plugins-label").textContent = c.mode === "none" ? "Plugins: off" : c.mode === "only" ? `Plugins: ${c.ids.length}` : "Plugins: Auto";
}

async function openChatSide() {
  const c = pluginChoice();
  for (const r of document.querySelectorAll('input[name="plugin-mode"]')) r.checked = r.value === c.mode;
  if (!hub.plugins) {
    try { hub.plugins = (await api("/api/plugins", undefined, 10000)).filter((p) => p.state === "on" && p.tools && p.tools.length); }
    catch { hub.plugins = []; }
  }
  $("side-plugins").replaceChildren(...hub.plugins.map((p) => {
    const box = el("input", { type: "checkbox", value: p.id });
    box.checked = c.ids.includes(p.id);
    box.disabled = c.mode !== "only";
    box.addEventListener("change", savePluginChoice);
    return el("label", { class: "side-plugin" }, box, icon(p.icon || "puzzle"),
      el("span", {}, el("strong", { text: p.name }), el("small", { class: "muted", text: `${p.tools.length} tool${p.tools.length === 1 ? "" : "s"}` })));
  }));
  await loadBrainChoices();
  $("side-brain").value = window.Chat?.visible?.() ? Chat.brain() : $("brain-pick").value;
  $("side-brain-note").textContent = hub.choices && !hub.choices.choices.length
    ? "No cloud brain has a key yet: add one on the APIs and keys page. Your PC's model answers until then."
    : "Remembered for this chat. If it's busy or out of free requests, the next one answers and the chat says so.";
  const d = $("chat-side");
  if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
}

function savePluginChoice() {
  const mode = document.querySelector('input[name="plugin-mode"]:checked')?.value || "auto";
  const ids = [...document.querySelectorAll("#side-plugins input:checked")].map((b) => b.value);
  for (const b of document.querySelectorAll("#side-plugins input")) b.disabled = mode !== "only";
  save("airtac-plugins", JSON.stringify({ mode, ids }));
  pluginLabel();
}

function chosenBrain() {
  return ($("brain-pick") && $("brain-pick").value) || undefined;
}

function brainLabel(tier) {
  if (!tier) return "";
  if (tier === "ollama") return "This PC (Ollama)";
  const g = hub.choices?.groups?.find((x) => x.id === tier);
  return g ? g.label.replace(/ \(.*\)$/, "") : (TIER_NAMES[tier] || tier);
}

function showExpectedBrain() {
  const ch = hub.choices;
  if (!ch) return;
  if (ch.mock) { $("chat-model").textContent = "Keyword rules (no model yet)"; return; }
  const pick = chosenBrain() || ch.order[0] || "";
  const [provider, ...rest] = pick.split(":");
  if (provider === "ollama") { $("chat-model").textContent = `This PC (Ollama) · ${shortModel(rest.join(":"))}`; return; }
  const c = ch.choices.find((x) => x.id === provider);
  $("chat-model").textContent = c ? `${brainLabel(c.id)} · ${shortModel(rest.join(":") || c.model)}` : `This PC (Ollama) · ${shortModel(ch.local)}`;
}

const lastUsage = { model: "" };

function chatMeta(data, where = "home") {
  const b = data.brain || {};
  const where_ = brainLabel(b.tier) || (hub.choices && b.model === hub.choices.local ? "This PC (Ollama)" : "Model");
  const who = (b.model === "keyword rules" ? "Keyword rules" : `${where_} · ${shortModel(b.model)}`) + (b.fallback ? " (fallback)" : "");
  if (b.fallback) toast({ title: b.fallback, body: "Your pick is tried first again next time." });
  const u = data.usage?.session;
  const limit = Number(hub.settings?.session_token_limit || 0);
  const over = u && limit && u.total > limit;
  const tokens = u ? `This session: ${fmt(u.input)} in · ${fmt(u.output)} out · ${fmt(u.total)} total` : "";
  if (where === "home") {
    lastUsage.model = who;
    $("chat-model").textContent = who;
    $("chat-tokens").textContent = tokens + (over ? ` · over your ${fmt(limit)}-token warning` : "");
    $("chat-meta").classList.toggle("over", Boolean(over));
  } else {
    $("app-chat-meta").textContent = `${who}${tokens ? ` · ${tokens}` : ""}${over ? " · over your token warning" : ""}`;
    $("app-chat-meta").classList.toggle("over", Boolean(over));
  }
  if (over && !hub.warned?.[where]) {
    hub.warned = { ...(hub.warned || {}), [where]: true };
    toast({ title: "This chat passed your token warning", body: `${fmt(u.total)} tokens so far (warning at ${fmt(limit)}). Start a new chat or pick a cheaper brain. Change the warning in Settings.` });
  }
}

function hubInit() {
  for (const section of ["ai", "apps", "social"]) {
    $(`${section}-q`).addEventListener("input", () => { hub.q[section] = $(`${section}-q`).value; if (hub.apps) drawCatalogCards(section); });
    $(`${section}-add`).addEventListener("click", () => editApp(null, section));
    $(`${section}-probe`).addEventListener("click", () => probeTabs(section, true));
  }
  $("app-form").addEventListener("submit", saveAppForm);
  $("app-cancel").addEventListener("click", () => $("app-dialog").close());
  $("app-remove").addEventListener("click", removeAppForm);
  $("about-form").addEventListener("submit", saveAbout);
  $("set-engine").addEventListener("change", () => saveSetting({ search_engine: $("set-engine").value }));
  $("set-tone").addEventListener("change", () => saveSetting({ tone: $("set-tone").value }));
  $("pw-lock").addEventListener("click", () => { vault.open = true; vaultLockNow(); loadPasswords(); });
  $("set-limit").addEventListener("change", () => saveSetting({ session_token_limit: Math.max(0, parseInt($("set-limit").value || "0", 10)) }));
  $("set-browser").addEventListener("change", () => saveSetting({ social_browser: $("set-browser").value }));
  $("set-brain").addEventListener("change", () => { saveSetting({ brain: $("set-brain").value }); rememberBrain($("set-brain").value); });
  $("brain-pick").addEventListener("change", () => rememberBrain($("brain-pick").value));
  $("set-pw-change").addEventListener("click", changePasscode);
  $("chat-setup").addEventListener("click", openChatSide);
  $("airtac-doctor").addEventListener("click", runDoctor);
  $("dev-doctor").addEventListener("click", async () => {
    const out = $("dev-doctor-out"); out.hidden = false; out.textContent = "Checking...";
    try { const r = await api("/api/plugins/devtools/reach/doctor", {}, 120000); out.textContent = r.installed ? (r.report || "No report.") : "Agent Reach isn't installed yet. Set it up above first."; }
    catch (e) { out.textContent = errorText(e); }
  });
  for (const r of document.querySelectorAll('input[name="plugin-mode"]')) r.addEventListener("change", savePluginChoice);
  // The side panel opens from Home and from the Chat page: the pick goes to whichever chat is open.
  $("side-brain").addEventListener("change", () => {
    const v = $("side-brain").value;
    if (window.Chat?.visible?.()) Chat.setBrain(v); else rememberBrain(v);
  });
  $("side-brain-refresh")?.addEventListener("click", async () => { await loadBrainChoices(true); $("side-brain").value = $("brain-pick").value; });
  pluginLabel();
  // The theme saved on the server wins on a new device; this browser's own choice shows first.
  loadSettingsData().then((s) => {
    if (s && !load("airtac-theme") && s.theme) applyTheme(s.theme);
    loadBrainChoices();
  });
}


// ---------------------------------------------------------------- Settings: Account (plugins/account)

async function loadAccount() {
  const box = $("set-account");
  let a;
  try { a = await api("/api/plugins/account", undefined, 10000); }
  catch (e) { box.replaceChildren(el("p", { class: "muted small", text: errorText(e) })); return; }
  const msg = el("p", { class: "conn-msg", role: "status", text: a.error || "" });
  const act = (label, ic, path, body, ok) => button(label, ic, async () => {
    msg.textContent = "Working...";
    try { const r = await api(`/api/plugins/account/${path}`, body, 120000); msg.textContent = ok(r); setTimeout(loadAccount, 800); }
    catch (e) { msg.textContent = errorText(e); }
  });
  const signIn = (features, label) => button(label, "user", async () => {
    try {
      const r = await api("/api/plugins/account/signin", { features }, 15000);
      window.open(r.url, "_blank", "noopener");
      msg.textContent = "Finish signing in on Google's page (it opened in a new tab)...";
      const until = Date.now() + 300000;
      const poll = setInterval(async () => {
        const s = await api("/api/plugins/account", undefined, 10000).catch(() => null);
        if (s && (s.signed_in && !s.pending || s.error || Date.now() > until)) { clearInterval(poll); loadAccount(); }
      }, 2000);
    } catch (e) { msg.textContent = errorText(e); }
  }, "primary");
  const parts = [];
  if (!a.ready) {
    parts.push(el("p", { class: "muted", text: "Not set up yet: make a free Google \"Desktop app\" client ID (docs\\GOOGLE_SETUP.md), then save it on APIs and keys > Google sign-in." }));
  } else if (!a.signed_in || a.sign_in_again) {
    parts.push(el("p", { text: a.sign_in_again ? "Google needs you to sign in again." : "Not signed in." }), el("div", { class: "row learn-actions" }, signIn([], "Sign in with Google")));
  } else {
    const has = (s) => a.scopes.some((x) => x.includes(s));
    parts.push(el("p", { text: `Signed in as ${a.email}${a.last_sync ? ` · last sync ${a.last_sync}` : ""}` }),
      el("div", { class: "row learn-actions" },
        has("drive.appdata") ? act("Upload to Drive now", "up", "sync", {}, (r) => `Uploaded (version ${r.version}): your encrypted vault and settings.`)
          : signIn(["drive"], "Switch on Drive sync"),
        has("drive.appdata") ? act("Restore from Drive", "install", "restore", {}, (r) => `Restored ${r.restored.join(", ")}. Old files kept in ${r.backup}.`) : "",
        has("drive.appdata") ? act("Restore settings (after unlocking)", "refresh", "restore/settings", {}, (r) => `Restored ${r.restored.join(", ")}.`) : "",
        has("calendar") ? act("Import Google Calendar", "calendar", "calendar", {}, (r) => `Added ${r.added} events (${r.skipped} already there).`)
          : signIn(["calendar"], "Switch on Google Calendar"),
        act("Sign out", "close", "signout", {}, () => "Signed out. Google's access was revoked.")));
  }
  box.replaceChildren(...parts, msg);
}

// Settings > Weather: the town for the morning greeting's forecast (plugins/weather, Open-Meteo).
async function loadWeatherSetting() {
  const note = $("wx-note");
  if (!note) return;
  try {
    const r = await api("/api/plugins/weather", undefined, 10000);
    $("wx-town").value = r.place.name;
    const w = r.weather;
    note.textContent = w ? `${w.place}: ${Math.round(w.now.temp)}°C, ${w.now.sky}. Today ${Math.round(w.today.low)} to ${Math.round(w.today.high)}°C, ${Math.round(w.today.rain_chance)}% chance of rain. ${r.credit}.`
      : `No forecast yet (AIRTAC fetches it every 30 minutes when online). ${r.credit}.`;
  } catch (e) { note.textContent = errorText(e); }
}

document.addEventListener("DOMContentLoaded", () => {
  const f = $("wx-form");
  if (!f) return;
  f.addEventListener("submit", async (event) => {
    event.preventDefault();
    $("wx-note").textContent = "Looking up the town...";
    try { await api("/api/plugins/weather/place", { name: $("wx-town").value.trim() }, 20000); loadWeatherSetting(); }
    catch (e) { $("wx-note").textContent = errorText(e); }
  });
});

// Restart AIRTAC (Settings and Dev tools; this PC only): press twice. The page's offline banner shows while it
// restarts and clears by itself when the new server answers.
document.addEventListener("DOMContentLoaded", () => {
  for (const b of document.querySelectorAll(".restart-airtac")) {
    b.addEventListener("click", async () => {
      const note = b.parentElement.querySelector(".restart-note") || $("airtac-doctor-sum");
      if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Press again to restart"; setTimeout(() => { delete b.dataset.armed; b.textContent = "Restart AIRTAC"; }, 4000); return; }
      b.disabled = true;
      try { const r = await api("/api/restart", {}, 10000); if (note) { note.hidden = false; note.textContent = r.detail; } }
      catch (e) { if (note) { note.hidden = false; note.textContent = errorText(e); } b.disabled = false; return; }
      setTimeout(() => location.reload(), 12000);
    });
  }
});
