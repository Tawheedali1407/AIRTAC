// AIRTAC front end: plain JavaScript, no framework, no build step.
// Server text is always inserted with textContent (never innerHTML), so it can't inject HTML.

const API = "";          // the page and the API come from the same AIRTAC on your PC
const LANG = "en-IN";     // speech recognition and the voice
const $ = (id) => document.getElementById(id);

// localStorage can throw (private windows, blocked storage), so every use is wrapped.
function load(key) { try { return localStorage.getItem(key); } catch { return null; } }
function save(key, value) { try { localStorage.setItem(key, value); } catch { /* not important */ } }

const sessionId = load("airtac-session") || Math.random().toString(36).slice(2, 12);
save("airtac-session", sessionId);

let busy = false;
let pendingId = null;
let brain = { provider: "mock", model: "keyword rules", ready: true };
let voiceCfg = { stt: "browser", tts: "browser" };
let currentView = "home";
let currentApp = null;   // id of the open app page, e.g. "practice"
let lastSpoken = "";

// What AIRTAC is doing right now, shown by the logo (see "the AIRTAC mark, alive" in style.css).
// Speaking wins over listening, which wins over thinking.
const live = { speaking: false, listening: false, thinking: false };
const STATE_WORDS = { speaking: "Speaking", listening: "Listening", thinking: "Thinking", idle: "" };

function setLive(key, on) {
  live[key] = on;
  const state = live.speaking ? "speaking" : live.listening ? "listening" : live.thinking ? "thinking" : "idle";
  document.body.dataset.state = state;
  const line = document.getElementById("state-line");
  if (line) line.textContent = STATE_WORDS[state];
}

// Things worth asking, used for the search suggestions.
const EXAMPLES = [
  "Brief me on my day", "What's on my schedule this week?", "Search the web for free GPU options for students",
  "Read https://fastapi.tiangolo.com/", "In work mode, research free GPU options for students and write me a PDF",
  "Search my library for big O", "Explain the solution step by step", "Make slides about two pointers",
  "Make a spreadsheet of my monthly budget", "What's today's LeetCode problem?", "Top Hacker News stories",
  "Remember my exam is on 12 Nov", "Remind me to pay the exam fee tomorrow at 6 pm", "Draw a binary tree 8 4 12 2 6 10 14",
  "How's my laptop doing?", "Play some music", "Back up my data", "Weekly review",
  "What are the symptoms of dengue?", "Derive the formula for the equivalent resistance of two resistors in parallel",
];

let APPS = [];  // from /api/apps: one per plugin with tools

// Friendly names for the route trace.
const TOOL_NAMES = {
  github_summary: "GitHub", list_issues: "GitHub issues", repo_readme: "README", create_issue: "New issue",
  leetcode_daily: "LeetCode daily", leetcode_profile: "LeetCode stats", leetcode_pick: "Practice pick",
  accounts_stats: "Accounts", accounts_link: "Sign-in link", accounts_username: "Username",
  solve_problem: "Solver", explain_solution: "Explainer", make_solutions_pdf: "Solutions PDF",
  hn_top: "Hacker News", arxiv_search: "arXiv", cf_contests: "Codeforces", web_search: "Web search",
  memory_save: "Memory", memory_recall: "Memory", reminder: "Reminder", make_pdf: "PDF",
  set_syllabus: "Syllabus", make_questions: "Questions", make_study_pdf: "Study PDF", make_image: "Diagram",
  schedule_add: "Schedule", schedule_list: "Schedule", schedule_done: "Schedule",
  read_page: "Reader", start_work: "Work mode", daily_briefing: "Your day",
  knowledge_search: "Library", knowledge_index: "Library", make_document: "Word", make_slides: "Slides",
  make_spreadsheet: "Excel", system_status: "System", open_app: "Open app", open_website: "Open site",
  play_music: "Music", backup_now: "Backup", backup_upload: "Cloud backup", weekly_review: "Review",
  export_training_data: "Export",
};


function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

// Links and images only ever point at web pages, this app's own files, or in-page anchors: an address
// like "javascript:..." from a news feed, a website or the model is dropped, so it can't run code.
const SAFE_URL = /^(https?:\/\/|\/|\.\/|#|data:image\/|data:audio\/|blob:)/i;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if ((k === "href" || k === "src") && !SAFE_URL.test(String(v).trim())) node.setAttribute(k, "#");
    else if (k.startsWith("on")) continue;   // never inline event handlers
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

// Text with the searched words marked (<mark>), built as nodes: the text itself is never parsed as HTML.
function highlight(text, query) {
  const words = (query || "").trim().split(/\s+/).filter((w) => w.length > 1)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return [text];
  const out = [];
  let last = 0;
  for (const m of text.matchAll(new RegExp(words.join("|"), "gi"))) {
    out.push(text.slice(last, m.index), el("mark", { text: m[0] }));
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

// ---------------------------------------------------------------- server calls

// Every call to AIRTAC goes through api(), so every page fails the same readable way:
// - GET, POST, PUT and DELETE (opts.method; with no body it's a GET, with one a POST);
// - a timeout, and a GET is retried twice when the network fails (a POST isn't: a chat or a
//   "save" sent twice could run twice);
// - when the server can't be reached, the error names the address and the browser's own words
//   instead of a bare "Failed to fetch", and the offline banner opens (see below).
class ApiError extends Error {
  constructor(message, kind, cause) { super(message); this.kind = kind; this.cause = cause; }
}

function offlineText(cause) {
  return `Can't reach the AIRTAC server at ${location.host || "this PC"}` + (cause && cause.message ? ` (the browser said: ${cause.message}).` : ".");
}

async function apiOnce(path, body, method, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    let res;
    try {
      res = await fetch(API + path, {
        method,
        // A file (a custom key's logo) goes as it is; everything else as JSON.
        headers: body === undefined || body instanceof Blob ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body),
        signal: ctrl.signal,
      });
    } catch (e) {
      if (e.name === "AbortError") throw new ApiError(`AIRTAC didn't answer within ${Math.round(timeoutMs / 1000)} seconds. Try again.`, "timeout", e);
      throw new ApiError(offlineText(e), "offline", e);
    }
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { location.href = "login.html"; throw new ApiError("Please sign in.", "auth"); }
    if (res.status === 423) throw new ApiError(typeof data.detail === "string" ? data.detail : "Locked.", "locked");   // the vault is locked
    if (!res.ok) {
      // Errors from AIRTAC carry a readable sentence in "detail" (never what you typed).
      throw new ApiError(typeof data.detail === "string" ? data.detail
        : `AIRTAC had a problem (error ${res.status}${res.statusText ? " " + res.statusText : ""}). The details are in data\\logs\\server.log on the PC.`, "server");
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

async function api(path, body, timeoutMs = 60000, opts = {}) {
  const method = opts.method || (body === undefined ? "GET" : "POST");
  const retries = opts.retries ?? (method === "GET" ? 2 : 0);
  for (let attempt = 0; ; attempt++) {
    try {
      const data = await apiOnce(path, body, method, timeoutMs);
      connectionBack();
      return data;
    } catch (e) {
      if (e.kind !== "offline") { if (e.kind !== "timeout") connectionBack(); throw e; }
      if (attempt >= retries) { connectionLost(e.cause); throw e; }
      await sleep(400 * 3 ** attempt);   // 0.4 s, then 1.2 s
    }
  }
}

function errorText(e) {
  if (!e) return "Something went wrong.";
  if (e.name === "AbortError") return "That took too long. Try again.";
  // A plain fetch() (voice, uploads) that couldn't connect: say so, and open the banner.
  if (e instanceof TypeError && /fetch|network|load failed/i.test(e.message || "")) { connectionLost(e); return offlineText(e); }
  return e.message || "Something went wrong.";
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- the offline banner
// The installed app opens even when the server is off (the service worker keeps the page), so
// without this every section would just say it couldn't load. The banner says what's wrong, offers
// "Start AIRTAC" on this PC (the airtac://start link that scripts\start.ps1 registers with Windows),
// checks every few seconds, and brings the page back by itself when the server answers.
const link = { down: false, timer: null, startedAt: 0, pageReady: false };
const ON_THIS_PC = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);

function connectionLost(cause) {
  const detail = offlineText(cause);
  $("conn-banner-detail").textContent = link.startedAt ? $("conn-banner-detail").textContent : detail;
  if (link.down) return;
  link.down = true;
  $("conn-start").hidden = !ON_THIS_PC;
  $("conn-banner-title").textContent = ON_THIS_PC ? "AIRTAC's server isn't running on this PC."
    : "AIRTAC on your PC can't be reached. Start it there (start_airtac.bat), and check the PC is on and online.";
  $("conn-banner").hidden = false;
  document.body.classList.add("offline");
  clearInterval(link.timer);
  link.timer = setInterval(checkBack, 4000);
}

function connectionBack() {
  if (!link.down) return;
  link.down = false;
  link.startedAt = 0;
  clearInterval(link.timer);
  $("conn-banner").hidden = true;
  document.body.classList.remove("offline");
  // The page never finished starting: start it properly. Otherwise refresh what's on screen.
  if (!link.pageReady) { location.reload(); return; }
  toast({ title: "AIRTAC is back." });
  showView(location.hash.slice(1) || "home");
  if ((location.hash.slice(1) || "home") === "home") { loadNews(); loadProgress(); }
}

async function checkBack() {
  try {
    const res = await fetch(API + "/api/health", { cache: "no-store", signal: AbortSignal.timeout(3500) });
    if (res.ok) {
      const h = await res.json().catch(() => ({}));
      if (newerBuild(h)) return;
      connectionBack();
      return;
    }
  } catch { /* still off */ }
  if (link.startedAt && Date.now() - link.startedAt > 30000) {
    $("conn-banner-detail").textContent = "It still isn't answering. Double-click start_airtac.bat in the AIRTAC Advance folder: it shows what went wrong. The log is data\\logs\\server.log.";
  }
}

$("conn-start").addEventListener("click", startServer);
$("conn-retry").addEventListener("click", checkBack);

function startServer() {
  link.startedAt = Date.now();
  $("conn-banner-title").textContent = "Starting AIRTAC...";
  $("conn-banner-detail").textContent = "If the browser asks to open \"AIRTAC start\", choose Open (tick \"Always allow\" to skip this next time). This page reconnects by itself.";
  location.href = "airtac://start";   // registered by scripts\start.ps1; starts the server, nothing else
}

// AIRTAC was updated (restarted with new page files): load the new page instead of running the old one.
// Not in the middle of an answer; the Chat page keeps its chats on the PC, so nothing is lost.
let pageBuild = "";
function newerBuild(h) {
  if (!h || !h.build) return false;
  if (!pageBuild) { pageBuild = h.build; return false; }
  if (h.build === pageBuild || busy || (window.Chat && Chat.state.busy)) return false;
  location.reload();
  return true;
}

document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible") return;
  try {
    const res = await fetch(API + "/api/health", { cache: "no-store", signal: AbortSignal.timeout(3500) });
    if (res.ok) newerBuild(await res.json());
  } catch { /* the offline banner handles it */ }
});

async function wakeServer() {
  try {
    const health = await api("/api/health", undefined, 15000);
    newerBuild(health);
    showStatus("");
    setBadge(health);
    window.dispatchEvent(new CustomEvent("airtac:health", { detail: health }));   // the start-up screen reports it
    if (health.missing_packages && health.missing_packages.length && !window.__toldMissing) {
      window.__toldMissing = true;   // once per page: some parts (the vault, the QR code...) need these packages
      toast({ title: "Some parts of AIRTAC aren't installed yet", body: `Missing: ${health.missing_packages.join(", ")}. Double-click repair_airtac.cmd in the AIRTAC Advance folder, then restart AIRTAC.` });
    }
    return true;
  } catch (e) {
    // Offline: the banner is showing and reloads the page when the server answers.
    if (e.kind !== "offline") showStatus(errorText(e));
    return false;
  }
}

function showStatus(msg) {
  $("status").textContent = msg;
  $("status").hidden = !msg;
}

function setBadge(health) {
  brain = health.brain || brain;
  voiceCfg = health.voice || voiceCfg;
  const badge = $("badge");
  const mock = brain.provider === "mock";
  // No badge in the keyword-rules mode; a real model shows its name (or that it's starting).
  badge.hidden = mock;
  badge.textContent = brain.ready ? shortModel(brain.model) : "Model off";
  badge.className = "badge " + (brain.ready ? "live" : "warn");
  badge.title = brain.ready ? `${brain.model} on this PC (${brain.provider})` : (brain.reason || "The model isn't ready");
}

function shortModel(name) {
  return (name || "").split("/").pop().replace(/-GGUF.*$/i, "").replace(/:latest$/, "");
}

// ---------------------------------------------------------------- views and menu

const VIEWS = ["home", "chat", "crm", "connectors", "web", "work", "research", "ai", "apps", "social", "tabs", "passwords", "devtools", "settings", "connections", "plugins", "history", "files", "app"];

// Views are named by the address hash: #home, #history, #files, or #app-practice for one app.
// Switching modes: the new view opens like an iris from where you tapped (boot.css), and a small
// readout names the mode. Browsers without View Transitions (and reduced motion) switch instantly.
let lastKey = null;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

function showView(name) {
  const key = name || "home";
  const instant = lastKey === null || key === lastKey || reduceMotion.matches || !document.startViewTransition
    || document.getElementById("boot") || document.hidden;   // a hidden page can't animate (the browser aborts it)
  lastKey = key;
  if (instant) return renderView(name);
  try {
    // A transition cut short (a quick second click, the window hidden) rejects its promises: that's fine, the view
    // is still drawn, so don't let it show up as an error.
    const t = document.startViewTransition(() => { renderView(name); });
    for (const p of [t.ready, t.finished, t.updateCallbackDone]) if (p) p.catch(() => {});
  } catch { renderView(name); }
  modeHud(key);
}

function modeHud(key) {
  let label = "", ic = "home";
  if (key.startsWith("app-")) {
    const a = APPS.find((x) => x.id === key.slice(4));
    if (a) { label = a.name; ic = a.icon || "spark"; }
  } else {
    const link = document.querySelector(`.drawer-nav a[data-view="${key}"]`);
    if (link) { label = link.dataset.label || link.textContent.trim(); ic = (link.querySelector("use")?.getAttribute("href") || "#i-home").slice(3); }
  }
  if (!label) return;
  const hud = $("mode-hud");
  hud.replaceChildren(icon(ic), el("span", { text: label }));
  hud.classList.remove("show");
  void hud.offsetWidth;   // restart the animation
  hud.classList.add("show");
}

// Remember where a tap landed, so the next view opens from there.
document.addEventListener("pointerdown", (event) => {
  const r = document.querySelector("main").getBoundingClientRect();
  document.documentElement.style.setProperty("--tap-x", `${Math.round(event.clientX - r.left)}px`);
  document.documentElement.style.setProperty("--tap-y", `${Math.round(event.clientY - r.top)}px`);
}, { capture: true, passive: true });

function renderView(name) {
  hideTip();
  if (name === "app-crm") name = "crm";
  if (name === "reverse" || name === "app-reverse") {   // Research > Reverse engineering (reverse.js)
    renderView("research");
    if (window.Reverse) Reverse.mode("reverse");
    markCurrent("reverse");
    return;
  }   // the People and pipeline plugin has its own page (crm.js)
  if (name.startsWith("app-")) return openApp(name.slice(4));
  if (!VIEWS.includes(name) || name === "app") name = "home";
  currentView = name;
  currentApp = null;
  document.body.dataset.view = name;   // the phone's Start button floats on Home only
  for (const v of VIEWS) $(`view-${v}`).hidden = v !== name;
  markCurrent(name);
  if (name === "research") { loadResearch(); loadBrains(); if (window.Reverse) Reverse.mode("notes"); }
  if (name === "chat" && window.Chat) Chat.open();
  if (name === "settings" && window.Sections) Sections.drawSettings();
  if (name === "settings" && window.Dictation) Dictation.load();
  if (name === "crm" && window.Crm) Crm.open();
  if (name === "connectors" && window.Connectors) Connectors.open();
  if (name === "settings" && window.Extras) Extras.open();
  if (name === "history") loadHistory();
  if (name === "files") loadFiles();
  if (name === "work") loadWork();
  if (name === "plugins") loadPlugins();
  if (name === "connections") loadConnections();
  if (name !== "passwords") clearInterval(hub.pwTick);
  if (name !== "research") document.body.classList.remove("focus-write");
  hubLoad(name);   // AI apps, Social, Passwords, Settings (hub.js)
  if (name === "web") { drawEngines(); if (!$("web-q").value) setTimeout(() => $("web-q").focus(), 50); }
  window.scrollTo(0, 0);
}

function markCurrent(key) {
  for (const a of document.querySelectorAll(".drawer-nav a")) {
    const mine = a.dataset.view === key || a.dataset.app === key.replace(/^app-/, "") && key.startsWith("app-");
    if (mine) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
}

function setHash(key) {
  try { history.replaceState(null, "", `#${key}`); } catch { /* some embedded views refuse it */ }
}

function setDrawer(open) {
  $("drawer").classList.toggle("open", open);
  $("drawer").setAttribute("aria-hidden", String(!open));
  $("scrim").hidden = !open;
  $("menu-open").setAttribute("aria-expanded", String(open));
  if (open) $("menu-close").focus();
}

function applyTheme(name) {
  document.documentElement.dataset.skin = name;
  for (const r of document.querySelectorAll('input[name="theme"]')) r.checked = r.value === name;
  save("airtac-theme", name);
}

// ---------------------------------------------------------------- the route trace

// The route trace was taken off Main chat (17 Oct): Main chat holds only the chat. These stay as no-ops
// when the element isn't on the page, so the code that calls them needs no change.
function routeNodes(labels) {
  const route = $("route");
  if (!route) return;
  route.classList.remove("idle");
  route.replaceChildren();
  labels.forEach((label, i) => {
    if (i > 0) route.append(el("li", { "aria-hidden": "true" }, el("span", { class: "wire" })));
    route.append(el("li", {}, el("span", { class: "node", text: label })));
  });
  route.hidden = false;
}

const modelName = () => (brain.provider === "mock" ? "Keyword rules" : shortModel(brain.model) || "Model");

// Before the first question, show the path a request takes, dimmed.
function routeIdle() {
  routeNodes(["You", modelName(), "Tools", "Reply"]);
  $("route")?.classList.add("idle");
}

function routeWorking() {
  if (!$("route")) return;
  routeNodes(["You", modelName()]);
  const [you, , model] = $("route").children;
  you.firstChild.classList.add("lit");
  $("route").children[1].firstChild.classList.add("busy");
  model.firstChild.classList.add("lit");
}

// Once the answer is in, draw the whole path and light it up hop by hop.
async function routeDone(steps) {
  if (!$("route")) return;
  const labels = ["You", modelName(), ...steps.map((s) => TOOL_NAMES[s.tool] || s.tool), "Reply"];
  routeNodes(labels);
  const items = [...$("route").children];
  const stepByNode = {};
  steps.forEach((s, i) => { stepByNode[(i + 2) * 2] = s; });  // node index in the list of <li>
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  for (let i = 0; i < items.length; i++) {
    const part = items[i].firstChild;
    part.classList.add("lit");
    const step = stepByNode[i];
    if (step) {
      part.title = `${step.tool} (${step.status})`;
      if (step.status === "error") part.classList.add("error");
      if (step.status === "pending" || step.status === "disabled") part.classList.add("held");
    }
    if (!still) await sleep(90);
  }
}

// ---------------------------------------------------------------- asking

function addTranscript(who, text) {
  $("transcript").append(el("li", {}, el("span", { class: "who", text: who === "you" ? "You" : "AIRTAC" }), text));
  $("session-box").hidden = $("transcript").children.length < 3;
}

function render(data, asked) {
  $("answer").hidden = false;
  // On a phone the answer can land below the screen, which looks like nothing happened: bring it into view.
  requestAnimationFrame(() => {
    const top = $("answer").getBoundingClientRect().top;
    if (top > innerHeight * 0.55 || top < 0) $("answer").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("spoken").textContent = data.spoken;
  $("spoken").hidden = Boolean(data.text);
  $("answer-md").hidden = !data.text;
  if (data.text) $("answer-md").replaceChildren(renderMd(data.text));
  lastSpoken = data.spoken;
  speak(data.spoken);
  $("screen").replaceChildren(...linkify(data.screen || "Nothing more to show for this reply."));
  $("details-box").open = Boolean(data.screen) && !data.pending && data.screen.length < 500;
  // Image tools add a line "Image: /files/images/<name>.png"; show it under the answer.
  const image = (data.screen || "").match(/^Image: (\/files\/[\w\/.-]+\.png)$/m);
  $("preview").hidden = !image;
  if (image) $("preview").src = API + image[1];
  playAudio(data.screen);
  drawExtras(data);
  drawNoticed($("noticed"), data.noticed);
  pendingId = data.pending ? data.pending.id : null;
  $("pending").hidden = !data.pending;
  if (data.pending) {
    $("pending-text").textContent = `This changes something outside your laptop, so it needs your OK. It will send exactly this:`;
    $("pending-args").textContent = pendingArgs(data.pending);
  }
  if (asked) addTranscript("you", asked);
  addTranscript("airtac", data.text || data.spoken);
  routeDone(data.steps || []);
  if ((data.steps || []).some((s) => /solve|questions|memory|pdf|image|syllabus/.test(s.tool))) loadProgress();
}

// ---------------------------------------------------------------- under an answer: brain, sources, audio, feedback

let lastMessageId = null;
const ROLE_NAMES = { general: "", coding: "Coding brain", reasoning: "Engineering brain", medical: "Medicine brain" };

const TIER_NAMES = { groq: "Groq", nebius: "Nebius", nvidia: "NVIDIA build", openrouter: "OpenRouter", gemini: "Gemini",
  huggingface: "Hugging Face", mistral: "Mistral", deepseek: "DeepSeek", anthropic: "Claude", openai: "OpenAI", xai: "Grok" };

function brainText(b) {
  if (!b || b.model === "keyword rules") return "";
  const via = b.tier && TIER_NAMES[b.tier] ? ` via ${TIER_NAMES[b.tier]}` : "";
  return `${ROLE_NAMES[b.role] || "Answered"}${ROLE_NAMES[b.role] ? ", " : ""}${shortModel(b.model)}${via}`;
}

// Web addresses in a reply become links that open in a new tab. Text stays text (no HTML is parsed).
const URL_RE = /https:\/\/[^\s<>"')]+[^\s<>"').,;:!?]/g;
function linkify(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(URL_RE)) {
    out.push(text.slice(last, m.index), el("a", { href: m[0], target: "_blank", rel: "noopener noreferrer", text: m[0] }));
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

// Things a plugin did by itself from what you said (a date put on your schedule), each with Undo.
function drawNoticed(box, noticed) {
  box.replaceChildren();
  box.hidden = !(noticed || []).length;
  for (const n of noticed || []) {
    if (!n.undo) continue;
    const b = el("button", { type: "button", class: "chip-btn", text: n.undo.label || "Undo" });
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api(n.undo.url, undefined, 15000, { method: n.undo.method || "POST" });
        b.textContent = "Undone.";
        if (currentApp === "schedule") loadApp("schedule", false);
      } catch (e) { b.textContent = `Couldn't undo it: ${errorText(e)}`; }
    });
    box.append(b);
  }
}

function sourceItems(sources) {
  return (sources || []).map((s) => el("li", {}, icon("library"),
    el("span", { text: s.title + (s.where ? `, ${s.where}` : "") })));
}

function drawExtras(data) {
  chatMeta(data, "home");   // the chat header: provider, model and this session's tokens (hub.js)
  const line = brainText(data.brain);
  $("brain-line").textContent = line ? `${line}${data.brain.role === "general" ? "" : " (chosen for this question)"}` : "";
  $("brain-line").hidden = !line;
  $("sources").replaceChildren(...sourceItems(data.sources));
  $("sources").hidden = !(data.sources || []).length;
  lastMessageId = data.message_id || null;
  $("feedback").hidden = !lastMessageId;
  $("fb-form").hidden = true;
  $("fb-done").hidden = true;
  for (const b of ["fb-good", "fb-bad"]) $(b).disabled = false;
}

async function sendFeedback(messageId, good, note = "") {
  return api("/api/feedback", { message_id: messageId, good, note }, 10000);
}

async function rateAnswer(good) {
  if (!lastMessageId) return;
  if (!good) {  // ask what was wrong first; the correction becomes a lesson
    $("fb-form").hidden = false;
    $("fb-note").focus();
    return;
  }
  try {
    await sendFeedback(lastMessageId, true);
    $("fb-done").textContent = "Thanks. Good answers can become training examples later.";
  } catch (e) {
    $("fb-done").textContent = errorText(e);
  }
  $("fb-done").hidden = false;
  for (const b of ["fb-good", "fb-bad"]) $(b).disabled = true;
}

async function saveCorrection(event) {
  event.preventDefault();
  if (!lastMessageId) return;
  try {
    const r = await sendFeedback(lastMessageId, false, $("fb-note").value.trim());
    $("fb-done").textContent = r.lesson ? "Saved as a lesson. I'll follow it next time a question like this comes up."
      : "Noted. Add what the right answer was next time, and it becomes a lesson.";
  } catch (e) {
    $("fb-done").textContent = errorText(e);
  }
  $("fb-form").hidden = true;
  $("fb-note").value = "";
  $("fb-done").hidden = false;
  for (const b of ["fb-good", "fb-bad"]) $(b).disabled = true;
}

// Music plays in the page, so it also works on your phone: the reply carries "Audio: /api/plugins/music/play/<id>".
function playAudio(screen) {
  const m = (screen || "").match(/^Audio: (\/api\/plugins\/music\/play\/\w+)$/m);
  const player = $("player");
  if (!m) { return; }
  player.src = API + m[1];
  player.hidden = false;
  player.play().catch(() => { /* the browser wants a tap first; the controls are there */ });
}

function setBusy(value) {
  busy = value;
  setLive("thinking", value);
  for (const id of ["send", "solve", "save-syllabus"]) $(id).disabled = value;
  $("mic").disabled = value || !Recognition;
}

// Files attached with the paperclip (attach.js) go with the message. A file still being read makes the
// message wait, so the answer can use it; a message with only files asks for a summary.
function withFiles(key, text, extra) {
  if (!window.Attach) return { text, extra };
  const waiting = Attach.waiting(key);
  if (waiting.length) { toast({ title: `Still reading ${waiting[0]}`, body: "Send again in a moment, when its chip says Ready." }); return null; }
  const ids = Attach.ids(key);
  if (!ids.length) return { text, extra };
  return { text: text.trim() || "Summarise the attached file.", extra: { ...extra, attachments: ids }, files: ids.length };
}

async function send(text, extra = {}) {
  if (busy) return;
  const withF = withFiles("main", text, extra);
  if (!withF) return;
  ({ text, extra } = withF);
  text = text.trim();
  if (!text) return;
  showView("home");
  closeSuggest();
  $("text").value = "";
  setBusy(true);
  routeWorking();
  $("answer").hidden = false;
  $("spoken").textContent = "Working on it...";
  $("spoken").hidden = false;
  $("answer-md").hidden = true;
  $("preview").hidden = true;
  $("pending").hidden = true;
  try {
    render(await streamChat({ message: text, session_id: sessionId, brain: chosenBrain(), plugins: chosenPlugins(), mode: "chat", ...extra }),
      text + (extra.problem_text ? " (with a pasted problem)" : "") + (withF.files ? ` (with ${withF.files} file${withF.files === 1 ? "" : "s"})` : ""));
    if (withF.files) Attach.clear("main");
  } catch (e) {
    $("spoken").textContent = errorText(e);
    routeIdle();
  } finally {
    setBusy(false);
  }
}

// Main chat answers like ChatGPT (18 Oct): the answer types out live, with headings, lists, tables and code.
// POST /api/chat/stream sends newline-delimited JSON: start, token, step, done, error.
async function streamChat(payload) {
  const box = $("answer-md");
  $("spoken").hidden = true;
  box.hidden = false;
  box.replaceChildren(el("span", { class: "cp-dots", "aria-label": "Thinking" }, el("i"), el("i"), el("i")));
  let buffer = "", raf = 0, reply = null;
  const paint = () => { raf = 0; box.replaceChildren(renderMd(buffer), el("span", { class: "cp-caret", "aria-hidden": "true" })); };
  const res = await fetch(API + "/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (res.status === 401) { location.href = "login.html"; throw new ApiError("Please sign in.", "auth"); }
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new ApiError(typeof d.detail === "string" ? d.detail : `AIRTAC had a problem (error ${res.status}).`, "server");
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let rest = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    rest += dec.decode(value, { stream: true });
    let cut;
    while ((cut = rest.indexOf("\n")) >= 0) {
      const line = rest.slice(0, cut).trim();
      rest = rest.slice(cut + 1);
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; }
      if (ev.type === "start") buffer = "";
      else if (ev.type === "token") { buffer += ev.text; if (!raf) raf = requestAnimationFrame(paint); }
      else if (ev.type === "done") reply = ev.reply;
      else if (ev.type === "error") throw new ApiError(ev.text, "server");
    }
  }
  if (raf) cancelAnimationFrame(raf);
  if (!reply) throw new ApiError("The answer was cut off. Ask again.", "server");
  return reply;
}

async function answerPending(approve) {
  if (!pendingId) return;
  const id = pendingId;
  pendingId = null;
  $("pending").hidden = true;
  setBusy(true);
  try {
    render(await api("/api/confirm", { pending_id: id, approve }));
  } catch (e) {
    $("spoken").textContent = errorText(e);
  } finally {
    setBusy(false);
  }
}

async function stopAll() {
  stopSpeaking();
  stopListening();
  try {
    await api("/api/kill", {}, 10000);
    $("answer").hidden = false;
    $("spoken").textContent = "Stopped.";
  } catch (e) {
    $("spoken").textContent = errorText(e);
  }
}

// ---------------------------------------------------------------- search suggestions

let suggestions = [];
let active = -1;
let suggestTimer = null;
let suggestGen = 0;  // bumped on close, so a slow history lookup can't reopen the list afterwards

function closeSuggest() {
  suggestGen += 1;
  clearTimeout(suggestTimer);
  $("suggest").hidden = true;
  $("text").setAttribute("aria-expanded", "false");
  active = -1;
}

function drawSuggest() {
  const list = $("suggest");
  list.replaceChildren();
  suggestions.forEach((s, i) => {
    const li = el("li", { role: "option", id: `sg-${i}`, "aria-selected": String(i === active) },
      icon(s.kind === "history" ? "clock" : "search"), el("span", {}, ...highlight(s.text, $("text").value)),
      el("span", { class: "kind", text: s.kind === "history" ? "Asked before" : "" }));
    li.addEventListener("mousedown", (e) => { e.preventDefault(); send(s.text); });
    list.append(li);
  });
  const q = $("text").value.trim();
  if (!suggestions.length && q.length > 2) {
    // Nothing earlier matches: say so (Enter still asks it).
    list.append(el("li", { class: "suggest-empty", role: "presentation" }, icon("search"),
      el("span", { text: "Nothing you asked before matches. Press Enter to ask it." })));
  }
  list.hidden = !list.children.length;
  $("text").setAttribute("aria-expanded", String(!list.hidden));
  if (active >= 0) $("text").setAttribute("aria-activedescendant", `sg-${active}`);
  else $("text").removeAttribute("aria-activedescendant");
}

async function updateSuggest() {
  if (busy) return;
  const gen = suggestGen;
  const q = $("text").value.trim().toLowerCase();
  const fromExamples = EXAMPLES.filter((e) => !q || e.toLowerCase().includes(q)).slice(0, q ? 4 : 3)
    .map((text) => ({ text, kind: "example" }));
  let fromHistory = [];
  try {
    const rows = await api(`/api/history?limit=20&q=${encodeURIComponent(q)}`, undefined, 8000);
    const seen = new Set(fromExamples.map((s) => s.text.toLowerCase()));
    for (const r of rows) {
      const key = r.text.toLowerCase();
      if (!seen.has(key)) { seen.add(key); fromHistory.push({ text: r.text, kind: "history" }); }
      if (fromHistory.length >= 4) break;
    }
  } catch { /* suggestions are optional */ }
  if (gen !== suggestGen || busy) return;
  suggestions = [...fromHistory, ...fromExamples].slice(0, 7);
  active = -1;
  if (document.activeElement === $("text")) drawSuggest();
}

function onSearchKey(event) {
  if ($("suggest").hidden || !suggestions.length) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const n = suggestions.length;
    active = event.key === "ArrowDown" ? (active + 1) % n : (active - 1 + n) % n;
    drawSuggest();
  } else if (event.key === "Enter" && active >= 0) {
    event.preventDefault();
    send(suggestions[active].text);
  } else if (event.key === "Escape") {
    closeSuggest();
  }
}

// ---------------------------------------------------------------- apps

// Every app is in the menu. Social and AI apps have their own pages (hub.js); these open on this PC in
// AIRTAC's own Chrome profile, so you stay signed in, and on your phone on the phone.
let launch = { tiles: [], here: false };

function appLink(app) {
  const link = el("a", { href: `#app-${app.id}`, "data-app": app.id }, icon(app.icon), app.name);
  link.addEventListener("click", (event) => { event.preventDefault(); setDrawer(false); openApp(app.id); });
  return link;
}

// Every app is listed in the menu (Main chat holds only the chat, 17 Oct).
function drawApps() {
  $("drawer-apps").replaceChildren(...APPS.map(appLink));
  drawLaunch();
}

function drawLaunch() {
  const row = $("launch");
  if (!row) return;   // the Open tiles moved to the Social page
  row.replaceChildren(...launch.tiles.map((t) => {
    const b = el("a", { href: t.url, target: "_blank", rel: "noopener noreferrer", class: "launch-tile" },
      el("span", { class: "launch-mark", text: t.name.slice(0, 1) }), el("span", { text: t.name }), icon("external"));
    b.addEventListener("click", (event) => openSite(event, t.url));
    return b;
  }));
  $("launch-block").hidden = !launch.tiles.length;
  $("launch-note").textContent = launch.here
    ? (launch.chrome ? "Opens in AIRTAC's Chrome profile, so you stay signed in." : "Chrome wasn't found, so these open in your usual browser.")
    : "Opens on this device.";
}

// On the PC, open a site in AIRTAC's Chrome profile; anywhere else, as a normal link.
async function openSite(event, url) {
  if (!launch.here) return;  // the link opens it on this device
  event.preventDefault();
  try { await api("/api/plugins/desktop/launch", { url }, 10000); } catch { window.open(url, "_blank", "noopener"); }
}

async function loadLaunch() {
  try { launch = await api("/api/plugins/desktop/tiles", undefined, 10000); } catch { launch = { tiles: [], here: false }; }
  drawLaunch();
}

// ---------------------------------------------------------------- Research: notes + co-pilot

// A small, safe Markdown view: the page builds every element itself (never innerHTML), so text in
// your notes or from the model can't turn into script. Headings, lists, quotes, code, bold, italics, links.
function mdInline(text) {
  const out = [];
  const re = /(\*\*([^*]+)\*\*|\*([^*]+)\*|_([^_]+)_|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    out.push(text.slice(last, m.index));
    if (m[2]) out.push(el("strong", { text: m[2] }));
    else if (m[3] || m[4]) out.push(el("em", { text: m[3] || m[4] }));
    else if (m[5]) out.push(el("code", { text: m[5] }));
    else out.push(el("a", { href: m[7], target: "_blank", rel: "noopener noreferrer", text: m[6] }));
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

function renderMd(md) {
  const root = document.createDocumentFragment();
  const lines = (md || "").replace(/\r/g, "").split("\n");
  let list = null, para = [], code = null;
  const flush = () => {
    if (para.length) { root.append(el("p", {}, ...mdInline(para.join(" ")))); para = []; }
    list = null;
  };
  for (const line of lines) {
    if (code !== null) {
      if (/^```/.test(line)) { root.append(el("pre", {}, el("code", { text: code.join("\n") }))); code = null; }
      else code.push(line);
      continue;
    }
    if (/^```/.test(line)) { flush(); code = []; continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const li = line.match(/^\s*(?:[-*+]|(\d+)\.)\s+(.*)$/);
    if (h) { flush(); root.append(el(`h${Math.min(h[1].length + 1, 5)}`, {}, ...mdInline(h[2]))); }
    else if (li) {
      if (para.length) flush();
      const kind = li[1] ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== kind) { list = el(kind); root.append(list); }
      list.append(el("li", {}, ...mdInline(li[2])));
    } else if (/^>\s?/.test(line)) { flush(); root.append(el("blockquote", {}, ...mdInline(line.replace(/^>\s?/, "")))); }
    else if (!line.trim()) flush();
    else { list = null; para.push(line.trim()); }
  }
  if (code !== null) root.append(el("pre", {}, el("code", { text: code.join("\n") })));
  flush();
  return root;
}

let rsNote = null;
let rsTimer = null;

async function loadResearch(pick) {
  let data;
  try { data = await api("/api/plugins/research/notes", undefined, 15000); }
  catch (e) { $("rs-saved").textContent = errorText(e); return; }
  const sel = $("rs-notes");
  sel.replaceChildren(...data.notes.map((n) => el("option", { value: String(n.id), text: n.title })));
  const id = pick || (rsNote && data.notes.some((n) => n.id === rsNote.id) ? rsNote.id : data.notes[0].id);
  sel.value = String(id);
  drawRecent(data.notes, id);
  await openNote(id);
}

// The recent notes list on the left (newest first), like a writing app's sidebar.
function drawRecent(notes, current) {
  $("rs-recent").replaceChildren(...notes.slice(0, 12).map((n) => {
    const b = el("button", { type: "button", "aria-current": String(n.id === current) },
      n.title || "Untitled notes", el("small", { text: (n.updated_at || "").slice(0, 16).replace("T", " ") }));
    b.addEventListener("click", async () => { $("rs-notes").value = String(n.id); await openNote(n.id); drawRecent(notes, n.id); });
    return el("li", {}, b);
  }));
}

// Focus: only your title and text, full width, in a reading font. Esc or the Focus button leaves it.
function setFocusWrite(on) {
  document.body.classList.toggle("focus-write", on);
  $("rs-focus").setAttribute("aria-pressed", String(on));
  if (on) { setPreview(false); $("rs-body").focus(); }
}

async function openNote(id) {
  await saveNow();
  try { rsNote = await api(`/api/plugins/research/notes/${id}`, undefined, 15000); }
  catch (e) { $("rs-saved").textContent = errorText(e); return; }
  $("rs-title").value = rsNote.title;
  $("rs-body").value = rsNote.body;
  $("rs-saved").textContent = "Saved";
  if (!$("rs-preview").hidden) $("rs-preview").replaceChildren(renderMd(rsNote.body));
}

function saveSoon() {
  $("rs-saved").textContent = "Saving...";
  clearTimeout(rsTimer);
  rsTimer = setTimeout(saveNow, 700);
  if (!$("rs-preview").hidden) $("rs-preview").replaceChildren(renderMd($("rs-body").value));
}

async function saveNow() {
  clearTimeout(rsTimer);
  if (!rsNote) return;
  const title = $("rs-title").value.trim(), body = $("rs-body").value;
  if (title === rsNote.title && body === rsNote.body) return;
  try {
    await putNote(rsNote.id, { title, body });
    rsNote = { ...rsNote, title, body };
    $("rs-saved").textContent = "Saved";
    const opt = $("rs-notes").querySelector(`option[value="${rsNote.id}"]`);
    if (opt) opt.textContent = title || "Untitled notes";
  } catch (e) { $("rs-saved").textContent = errorText(e); }
}

// A PUT through the one API client, so a save that fails says why (and opens the banner if offline).
function putNote(id, body) {
  return api(`/api/plugins/research/notes/${id}`, body, 15000, { method: "PUT" });
}

function setPreview(on) {
  $("rs-tab-write").setAttribute("aria-selected", String(!on));
  $("rs-tab-preview").setAttribute("aria-selected", String(on));
  $("rs-body").hidden = on;
  $("rs-preview").hidden = !on;
  if (on) $("rs-preview").replaceChildren(renderMd($("rs-body").value));
}

function selectedText() {
  const t = $("rs-body");
  return t.selectionStart !== t.selectionEnd ? t.value.slice(t.selectionStart, t.selectionEnd) : "";
}

function insertIntoNotes(md, heading) {
  const t = $("rs-body");
  const block = `\n\n### ${heading}\n\n${md.trim()}\n`;
  const at = t.selectionEnd || t.value.length;
  t.value = t.value.slice(0, at) + block + t.value.slice(at);
  setPreview(false);
  t.focus();
  t.selectionStart = t.selectionEnd = at + block.length;
  saveSoon();
}

const RS_LABEL = { summarise: "Summary", outline: "Outline", analyse: "Analysis", ask: "Answer", sources: "Sources" };

async function copilot(action, question = "") {
  if (busy) return;
  const part = selectedText();
  const item = el("li", { class: "rs-result busy" }, el("strong", { text: `${RS_LABEL[action]}: working...` }));
  $("rs-results").prepend(item);
  try {
    const files = window.Attach ? Attach.ids("research") : [];
    if (window.Attach && Attach.waiting("research").length) throw new Error(`Still reading ${Attach.waiting("research")[0]}. Ask again in a moment.`);
    const r = await api("/api/plugins/research/copilot", { action, question, title: $("rs-title").value,
      text: part || $("rs-body").value, attachments: files, collection: $("rs-brain").value }, 180000);
    const body = el("div", { class: "md" }, renderMd(r.markdown));
    const add = el("button", { type: "button", class: "chip-btn primary", text: "Insert into notes" });
    add.addEventListener("click", () => insertIntoNotes(r.markdown, `${RS_LABEL[action]} (AI${question ? `: ${question.slice(0, 60)}` : ""})`));
    const copy = el("button", { type: "button", class: "chip-btn", text: "Copy" });
    copy.addEventListener("click", () => navigator.clipboard && navigator.clipboard.writeText(r.markdown));
    item.className = "rs-result";
    item.replaceChildren(el("div", { class: "rs-result-head" }, el("strong", { text: RS_LABEL[action] + (part ? " of your selection" : "") }),
      el("span", { class: "muted small", text: r.model || "" })), body, el("div", { class: "chips" }, add, copy));
  } catch (e) {
    item.className = "rs-result";
    item.replaceChildren(el("p", { class: "muted", text: errorText(e) }));
  }
}

// ---------------------------------------------------------------- news

let newsSections = [];
let newsTab = load("airtac-news-tab") || "tech";

async function loadNews() {
  try {
    newsSections = (await api("/api/feed", undefined, 20000)).sections || [];
  } catch {
    newsSections = [];
  }
  drawNews();
}

function drawNews() {
  const tabs = $("news-tabs");
  tabs.replaceChildren();
  if (!newsSections.length) {
    $("news-list").replaceChildren(el("p", { class: "news-note", text: "News didn't load. Press refresh to try again." }));
    return;
  }
  if (!newsSections.some((s) => s.id === newsTab)) newsTab = newsSections[0].id;
  for (const s of newsSections) {
    const tab = el("button", { type: "button", class: "tab", role: "tab", "aria-selected": String(s.id === newsTab),
      text: s.id === "local" ? "Around you" : s.title.split(" on ")[0] });
    tab.addEventListener("click", () => { newsTab = s.id; save("airtac-news-tab", s.id); drawNews(); });
    tabs.append(tab);
  }
  const s = newsSections.find((x) => x.id === newsTab);
  const list = $("news-list");
  list.replaceChildren();
  if (!s.items.length) list.append(el("p", { class: "news-note", text: s.note || "Nothing here right now." }));
  else if (s.note) list.append(el("p", { class: "news-note small", text: s.note }));   // e.g. an older copy, and why
  for (const item of s.items) {
    list.append(el("a", { class: "news-item", href: item.url, target: "_blank", rel: "noopener" },
      el("strong", { text: item.title }), el("span", { text: item.meta })));
  }
  const foot = el("div", { class: "source" }, el("span", { text: `${s.title}, from ${s.source}` }));
  list.append(foot);
}

// ---------------------------------------------------------------- progress

function dayLabel(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short" });
}

// A tooltip whose bar disappears (the page changed under the pointer) must not stay on screen.
function hideTip() { $("tip").hidden = true; }
window.addEventListener("scroll", hideTip, { passive: true });

function showTip(target, text) {
  const tip = $("tip");
  tip.textContent = text;
  tip.hidden = false;
  const r = target.getBoundingClientRect();
  const w = tip.offsetWidth;
  tip.style.left = `${Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8)}px`;
  tip.style.top = `${Math.max(8, r.top - tip.offsetHeight - 8)}px`;
}

function withTip(node, text) {
  node.dataset.tip = text;
  node.setAttribute("aria-label", text);
  node.tabIndex = 0;
  node.addEventListener("pointerenter", () => showTip(node, text));
  node.addEventListener("focus", () => showTip(node, text));
  node.addEventListener("pointerleave", () => { $("tip").hidden = true; });
  node.addEventListener("blur", () => { $("tip").hidden = true; });
  return node;
}

async function loadProgress() {
  let p;
  try {
    p = await api("/api/progress", undefined, 20000);
  } catch (e) {
    $("progress").replaceChildren(el("p", { class: "muted", text: `Progress didn't load: ${errorText(e)}` }));
    return;
  }
  const box = $("progress");
  box.replaceChildren();

  // Your own numbers first: the day streak, chats this week, schedule tasks. Real counts from this PC.
  const y = p.you;
  if (y) {
    const tile = (value, label, tip) => withTip(el("div", { class: "total you-num" }, el("b", { text: String(value) }), el("span", { text: label })), tip);
    box.append(el("div", { class: "totals you" },
      tile(y.streak, "day streak", y.asked_today ? "You asked something today." : "Ask something today to keep it going."),
      tile(y.chats_week, y.chats_week === 1 ? "chat this week" : "chats this week", `${y.questions_week} questions in the last 7 days`),
      tile(y.tasks_total ? `${y.tasks_done}/${y.tasks_total}` : "0", "tasks done", "Items ticked off on your Schedule")));
    if (!y.ever) box.append(el("p", { class: "muted small", text: "Nothing yet. Ask your first question and your streak starts today." }));
  }

  // One hero number from a plugin (LeetCode solved, by default).
  if (p.hero) {
    box.append(el("p", { class: "hero-num", text: Number(p.hero.value).toLocaleString() }),
      el("p", { class: "hero-label", text: p.hero.label.toLowerCase() }));
    if (p.hero.sub) box.append(el("p", { class: "muted hero-sub", text: p.hero.sub }));
  }
  if (p.bars && p.bars.items.length) {
    const max = Math.max(1, ...p.bars.items.map((i) => i.value));
    for (const it of p.bars.items) {
      const fill = el("div", { class: "fill" });
      fill.style.width = `${(it.value / max) * 100}%`;
      box.append(withTip(el("div", { class: "hbar" }, el("span", { text: it.label }), el("div", { class: "track" }, fill),
        el("span", { class: "val", text: String(it.value) })), `${it.label}: ${it.value}`));
    }
  }

  // Columns: requests per day this week.
  box.append(el("p", { class: "chart-title", text: "Requests per day, last 7 days" }));
  const cols = el("div", { class: "cols" });
  const days = el("div", { class: "days", "aria-hidden": "true" });
  const top = Math.max(1, ...p.week.map((d) => d.asks));
  const peak = p.week.reduce((a, b) => (b.asks > a.asks ? b : a), p.week[0]);
  for (const d of p.week) {
    const bar = el("div", { class: "bar" });
    bar.style.height = `${(d.asks / top) * 100}%`;
    const showCap = d === peak || d === p.week[p.week.length - 1];
    const col = el("div", { class: "col" }, showCap ? el("span", { class: "cap", text: String(d.asks) }) : "", bar);
    cols.append(withTip(col, `${dayLabel(d.date)}: ${d.asks} requests`));
    days.append(el("span", { text: dayLabel(d.date) }));
  }
  box.append(cols, days);

  if (p.next_event) {
    const e = p.next_event;
    const when = new Date(e.date + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    const link = el("a", { href: "#app-schedule", class: "next-up" },
      el("span", { class: "muted", text: "Next on your schedule" }),
      el("strong", { text: e.title }), el("span", { text: when + (e.estimated ? " (estimated)" : "") }));
    link.addEventListener("click", (event) => { event.preventDefault(); openApp("schedule"); });
    box.append(link);
  }
  if (p.counts.length) {
    box.append(el("div", { class: "totals" }, ...p.counts.slice(0, 4).map((c) =>
      el("div", { class: "total" }, el("b", { text: String(c.value) }), el("span", { text: c.label })))));
  }

  // The same numbers as a table, for screen readers and anyone who prefers rows.
  const table = el("table", { class: "data-table" },
    el("thead", {}, el("tr", {}, el("th", { text: "Day" }), el("th", { text: "Requests" }))),
    el("tbody", {}, ...p.week.map((d) => el("tr", {}, el("td", { text: dayLabel(d.date) }), el("td", { text: String(d.asks) })))));
  table.hidden = true;
  const toggle = el("button", { type: "button", class: "table-toggle", text: "Show as a table" });
  toggle.addEventListener("click", () => {
    table.hidden = !table.hidden;
    toggle.textContent = table.hidden ? "Show as a table" : "Hide the table";
  });
  box.append(toggle, table);
}

// ---------------------------------------------------------------- your day, read out on open

// Browsers only let a page talk after you've clicked or typed on it. The desktop launcher
// (scripts\start_airtac.ps1) opens AIRTAC in a window that's allowed to talk straight away;
// in a normal tab, AIRTAC reads your day at your first tap or key press instead.
// "Read out my day" in the menu: every time AIRTAC opens, the first time each day, or off.
let dayText = "";
let daySpoken = "";

function announceMode() {
  const old = load("airtac-announce");  // the earlier on/off switch
  return load("airtac-announce-mode") || (old === "off" ? "off" : "every");
}

async function loadBriefing(readAloud = true) {
  let b;
  try {
    // getTimezoneOffset: so a server on UTC (free hosts) still knows when your "today" is.
    b = await api(`/api/briefing?offset=${new Date().getTimezoneOffset()}`, undefined, 15000);
  } catch {
    return;  // the rest of the page works without it
  }
  dayText = b.text;
  daySpoken = b.spoken || b.text;
  $("today-text").textContent = b.text;
  const items = $("today-items");
  items.replaceChildren();
  const chip = (e, kind, when) => el("li", { class: kind, title: e.title,
    text: `${when}: ${e.title.length > 38 ? e.title.slice(0, 36) + "…" : e.title}` });
  // At most four chips (two on a phone); the rest are one tap away in the Schedule app.
  const max = window.matchMedia("(max-width: 560px)").matches ? 2 : 4;
  const all = [...(b.overdue || []).map((e) => [e, "overdue", "Overdue"]),
    ...(b.today || []).map((e) => [e, "now", e.time ? `Today ${e.time}` : "Today"]),
    ...(b.tomorrow || []).map((e) => [e, "", "Tomorrow"])];
  for (const [e, kind, when] of all.slice(0, max)) items.append(chip(e, kind, when));
  if (all.length > max) items.append(el("li", { text: `+${all.length - max} more` }));
  items.hidden = !items.children.length;
  $("today").hidden = false;
  if (!readAloud) return;
  const mode = announceMode();
  if (mode === "off") return;
  if (mode === "daily" && load("airtac-announced") === b.date) return;
  announceDay(b.date);
}

function announceDay(day) {
  if (!canSpeak || !daySpoken) return;
  // A phone never lets a page speak before a tap: show the Start button instead of trying.
  if (!audioUnlocked && isPhone()) { showStart(day); return; }
  let started = false;
  const blocked = () => {
    if (started) return;
    // Blocked until you interact: say so, and read it at the first tap or key press.
    $("today-tap").hidden = false;
    if (isPhone()) showStart(day);
    $("today-listen").querySelector("span").textContent = "Play";
    const once = (event) => {
      // A tap on a mic or a text box means you want to talk, not listen: don't talk over you.
      if (event.target.closest && event.target.closest("#mic, #chat-mic, input, textarea")) return;
      document.removeEventListener("pointerup", once, true);
      document.removeEventListener("keydown", once, true);
      if (!$("start-airtac").hidden && event.target.closest && event.target.closest("#start-airtac")) return;
      $("today-tap").hidden = true;
      unlockAudio();
      playDay(day);
    };
    // pointerup, not pointerdown: phones only allow sound from a finished tap.
    document.addEventListener("pointerup", once, true);
    document.addEventListener("keydown", once, true);
  };
  const u = utter(daySpoken, { onBlocked: blocked });
  const onstart = u.onstart;
  u.onstart = () => { started = true; markAnnounced(day); onstart(); $("today").classList.add("speaking"); };
  const onend = u.onend;
  u.onend = () => { onend(); $("today").classList.remove("speaking"); };
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
  // Some browsers refuse silently instead of reporting "not-allowed". If nothing has started
  // after two seconds, show the tap hint anyway.
  setTimeout(() => { if (!started && !speechSynthesis.speaking) blocked(); }, 2000);
}

function playDay(day) {
  const u = speak(daySpoken, true);
  if (!u) return;
  const onstart = u.onstart;
  u.onstart = () => { markAnnounced(day); onstart(); $("today").classList.add("speaking"); };
  const onend = u.onend;
  u.onend = () => { onend(); $("today").classList.remove("speaking"); };
}

function markAnnounced(day) {
  if (day) save("airtac-announced", day);
}

function toggleDay() {
  if (speakingText && speakingText === daySpoken) { stopSpeaking(); $("today").classList.remove("speaking"); return; }
  $("today-tap").hidden = true;
  playDay(load("airtac-announced"));
}

// ---------------------------------------------------------------- history

let historyTimer = null;

function dayHeading(date) {
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
}

const hist = { mode: "q", picked: new Set() };

async function loadHistory() {
  const box = $("history-list");
  hist.picked.clear();
  $("history-del").hidden = true;
  $("history-by-q").setAttribute("aria-selected", String(hist.mode === "q"));
  $("history-by-chat").setAttribute("aria-selected", String(hist.mode === "chat"));
  if (hist.mode === "chat") return loadHistoryChats();
  let rows = [];
  try {
    rows = await api(`/api/history?limit=150&q=${encodeURIComponent($("history-q").value.trim())}`, undefined, 15000);
  } catch (e) {
    box.replaceChildren(el("p", { class: "empty", text: errorText(e) }));
    return;
  }
  box.replaceChildren();
  if (!rows.length) {
    box.append(el("p", { class: "empty", text: $("history-q").value.trim()
      ? "Nothing you asked matches all of those words. Try fewer or shorter words."
      : "Nothing yet. Ask something on the home screen and it shows up here." }));
    return;
  }
  let heading = "";
  for (const r of rows) {
    const when = new Date(r.at);
    const h = dayHeading(when);
    if (h !== heading) { heading = h; box.append(el("h3", { text: h })); }
    const again = el("button", { type: "button", text: "Ask again" });
    again.addEventListener("click", () => send(r.text));
    const pick = historyPick(`q:${r.id}`, r.text);
    const del = historyDelete(() => api("/api/history/delete", { questions: [r.id] }, 15000));
    box.append(el("div", { class: "history-row" }, pick, el("span", { class: "text" }, ...highlight(r.text, $("history-q").value)),
      el("time", { datetime: r.at, text: when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) }),
      again, del));
  }
}

async function loadHistoryChats() {
  const box = $("history-list");
  let chats = [];
  try { chats = (await api("/api/history/chats?limit=200", undefined, 15000)).chats; }
  catch (e) { box.replaceChildren(el("p", { class: "empty", text: errorText(e) })); return; }
  const words = $("history-q").value.toLowerCase().split(/\s+/).filter(Boolean);
  chats = chats.filter((c) => words.every((w) => c.title.toLowerCase().includes(w)));
  box.replaceChildren(...(chats.length ? [] : [el("p", { class: "empty", text: "No chats." })]));
  for (const c of chats) {
    const kind = c.id.startsWith("app:") ? `${c.id.slice(4)} app` : c.id === "mcp" ? "Claude Code (MCP)" : "chat";
    box.append(el("div", { class: "history-row" }, historyPick(`c:${c.id}`, c.title),
      el("span", { class: "text" }, el("strong", { text: c.title || "(no question)" }),
        el("small", { class: "muted", text: ` · ${c.questions} question${c.questions === 1 ? "" : "s"} · ${kind}` })),
      el("time", { datetime: c.at, text: new Date(c.at).toLocaleDateString(undefined, { day: "numeric", month: "short" }) }),
      historyDelete(() => api("/api/history/delete", { chats: [c.id] }, 15000))));
  }
}

function historyPick(key, label) {
  const box = el("input", { type: "checkbox", "aria-label": `Select: ${label.slice(0, 60)}` });
  box.addEventListener("change", () => {
    if (box.checked) hist.picked.add(key); else hist.picked.delete(key);
    $("history-del").hidden = !hist.picked.size;
    $("history-del").lastChild.textContent = `Delete selected (${hist.picked.size})`;
  });
  return box;
}

// Delete buttons ask twice ("Press again"), so one stray tap can't remove anything.
function historyDelete(run) {
  const b = el("button", { type: "button", class: "icon-btn", "aria-label": "Delete" }, icon("close"));
  b.addEventListener("click", async () => {
    if (!b.dataset.armed) { b.dataset.armed = "1"; b.classList.add("armed"); b.title = "Press again to delete"; setTimeout(() => { delete b.dataset.armed; b.classList.remove("armed"); }, 4000); return; }
    try { await run(); loadHistory(); } catch (e) { toast({ title: errorText(e) }); }
  });
  return b;
}

async function deletePickedHistory() {
  const questions = [...hist.picked].filter((k) => k.startsWith("q:")).map((k) => Number(k.slice(2)));
  const chats = [...hist.picked].filter((k) => k.startsWith("c:")).map((k) => k.slice(2));
  const b = $("history-del");
  if (!b.dataset.armed) { b.dataset.armed = "1"; b.lastChild.textContent = `Press again to delete ${hist.picked.size}`; setTimeout(() => { delete b.dataset.armed; }, 4000); return; }
  delete b.dataset.armed;
  try { const r = await api("/api/history/delete", { questions, chats }, 30000); toast({ title: `Deleted ${r.deleted} message${r.deleted === 1 ? "" : "s"}` }); }
  catch (e) { toast({ title: errorText(e) }); }
  loadHistory();
}

function clearAllHistory() {
  const word = el("input", { class: "field", maxlength: "10", "aria-label": "Type DELETE", placeholder: "DELETE", autocomplete: "off" });
  const go = el("button", { type: "submit", class: "primary", text: "Clear History" });
  const cancel = el("button", { type: "button", class: "quiet", text: "Cancel" });
  const msg = el("p", { class: "conn-msg bad", role: "status" });
  const form = el("form", { class: "sheet-body" }, el("h2", { text: "Clear all of History?" }),
    el("p", { text: "Every chat goes, with its summary and token count. Lessons, files, the schedule, knowledge and the vault stay." }),
    el("label", { class: "conn-field" }, el("span", { text: "Type DELETE to go ahead" }), word), msg,
    el("div", { class: "row" }, el("span", { class: "spacer" }), cancel, go));
  const dialog = el("dialog", { class: "sheet conn-sheet pass-sheet" }, form);
  document.body.append(dialog);
  const close = () => { dialog.close(); dialog.remove(); };
  cancel.addEventListener("click", close);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const r = await api("/api/history/delete", { everything: word.value.trim() }, 30000); close(); toast({ title: `History cleared (${r.deleted} messages)` }); loadHistory(); }
    catch (err) { msg.textContent = errorText(err); }
  });
  dialog.showModal();
  word.focus();
}

// ---------------------------------------------------------------- Become an AI expert (plugins/learn)

function drawLearn(L) {
  const box = $("app-learn");
  const msg = el("p", { class: "conn-msg", role: "status" });
  const act = (label, ic, path, body, done) => button(label, ic, async () => {
    msg.textContent = "Working...";
    try { const r = await api(`/api/plugins/learn/${path}`, body, 30000); msg.textContent = done(r); loadApp("learn", false); }
    catch (e) { msg.textContent = errorText(e); }
  });
  const head = el("section", { class: "panel learn-now" },
    el("h3", { text: L.current ? `This week (week ${L.current})` : "Plan finished" }),
    el("p", { text: L.this_week || "Every week of the plan is behind you. Year 3 is in docs\\ROADMAP_AGI.md." }),
    el("div", { class: "row learn-actions" },
      act("Add the plan to Schedule", "calendar", "schedule", {}, (r) => `Added ${r.added} items to Schedule${r.skipped ? `, ${r.skipped} were already there` : ""}.`),
      act("I fell behind: move a week", "refresh", "behind", { weeks: 1 }, (r) => `Moved ${r.moved} unfinished items by a week. No guilt: pick up from here.`),
      button("Level check", "question", () => drawLevelCheck(L))), msg,
    el("div", { id: "learn-level" }));
  const stages = el("section", { class: "panel" }, el("h3", { text: "Stages" }),
    ...L.plan.stages.map((s) => el("details", { class: "learn-stage", ...(L.current && L.current >= s.weeks[0] && L.current <= s.weeks[1] ? { open: "" } : {}) },
      el("summary", {}, el("strong", { text: `${s.id}. ${s.title}` }), el("small", { class: "muted", text: ` · weeks ${s.weeks[0]}–${s.weeks[1]}` })),
      el("p", { class: "small", text: `Goals: ${s.goals.join("; ")}. Practice: ${s.practice.join("; ")}. Checkpoint: ${s.checkpoint}.` }),
      el("ul", { class: "learn-res" }, ...s.resources.map((r) => el("li", {}, el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", text: r.title })))))));
  const weeks = el("section", { class: "panel" }, el("h3", { text: "Weeks" }),
    el("ul", { class: "learn-weeks" }, ...L.plan.weeks.map((w) => {
      const tick = el("input", { type: "checkbox", "aria-label": `Week ${w.week} done` });
      tick.checked = L.done.includes(w.week);
      tick.addEventListener("change", () => api("/api/plugins/learn/done", { week: w.week, done: tick.checked }, 10000).catch((e) => { msg.textContent = errorText(e); }));
      const start = new Date(L.starts[w.week] + "T00:00");
      return el("li", { class: `learn-week${w.week === L.current ? " now" : ""}${w.light ? " light" : ""}` }, tick,
        el("span", {}, el("strong", { text: `Week ${w.week} · ${start.toLocaleDateString(undefined, { day: "numeric", month: "short" })}${w.light ? " · Light Mode" : ""}` }),
          el("small", { class: "muted", text: ` ${w.lab || w.prep}` })));
    })));
  box.replaceChildren(head, stages, weeks);
}

function drawLevelCheck(L) {
  const qs = L.plan.level_check;
  const form = el("form", { class: "learn-quiz" }, ...qs.map((q, i) => el("fieldset", {}, el("legend", { text: `${i + 1}. ${q.q}` }),
    ...q.options.map((o, j) => el("label", {}, el("input", { type: "radio", name: `q${i}`, value: String(j), required: "" }), ` ${o}`)))),
  el("button", { type: "submit", class: "primary", text: "See my level" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const answers = qs.map((_, i) => Number(form.querySelector(`input[name=q${i}]:checked`)?.value ?? -1));
    try {
      const r = await api("/api/plugins/learn/level", { answers }, 10000);
      form.replaceWith(el("p", { class: "warn-note", text: `${r.score}/${r.of} right. Start from stage ${r.start_stage}: ${r.stage_title}.` }));
    } catch (err) { toast({ title: errorText(err) }); }
  });
  $("learn-level").replaceChildren(form);
}

// ---------------------------------------------------------------- Repositories (plugins/repos)

function drawRepos(R) {
  const box = $("app-repos");
  const url = el("input", { class: "field", maxlength: "200", placeholder: "https://github.com/owner/repo", "aria-label": "GitHub address" });
  const msg = el("p", { class: "conn-msg", role: "status", text: R.git ? "" : "Git isn't installed: get it from git-scm.com, then come back." });
  const log = el("pre", { class: "dev-log", hidden: "" });
  const add = el("form", { class: "panel learn-now" }, el("h3", { text: "Add a repository" }),
    el("p", { class: "muted small", text: `Cloned into ${R.folder}. Install steps come from its README and run only when you press Confirm (pip goes into the repo's own venv).` }),
    el("div", { class: "row" }, url, el("button", { type: "submit", class: "primary", text: "Add" })), msg, log);
  add.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.textContent = "Cloning...";
    try { await api("/api/plugins/repos/add", { url: url.value }, 300000); msg.textContent = ""; loadApp("repos", false); } catch (err) { msg.textContent = errorText(err); }
  });
  const runStep = async (repo, step, b) => {
    if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Confirm: run it"; return; }
    log.hidden = false; log.textContent = "";
    try {
      let r = await api("/api/plugins/repos/install", { repo, step }, 20000);
      for (;;) {
        log.textContent += r.lines.join("\n") + (r.lines.length ? "\n" : "");
        log.scrollTop = log.scrollHeight;
        if (r.done) { msg.textContent = r.ok ? "Installed." : "It stopped: see the log."; break; }
        await new Promise((res) => setTimeout(res, 1500));
        r = await api(`/api/plugins/repos/install?after=${r.next}`, undefined, 15000);
      }
      loadApp("repos", false);
    } catch (err) { msg.textContent = errorText(err); }
  };
  const cards = R.items.map((x) => {
    const steps = x.steps.map((s) => {
      const b = el("button", { type: "button", class: "quiet small", text: s.runnable ? "Run" : "Run it yourself" });
      b.disabled = !s.runnable;
      b.addEventListener("click", () => runStep(x.id, s.cmd, b));
      return el("li", {}, el("code", { text: s.cmd }), " ", b);
    });
    const clone = button(x.state === "not cloned" ? "Clone" : "Check for updates", x.state === "not cloned" ? "install" : "refresh", async () => {
      msg.textContent = "Working...";
      try {
        if (x.state === "not cloned") await api("/api/plugins/repos/add", { url: x.url }, 300000);
        else { const r = await api("/api/plugins/repos/update", { repo: x.id }, 120000); msg.textContent = r.update ? `${x.id}: an update is available (git pull in its folder).` : `${x.id} is up to date.`; }
        loadApp("repos", false);
      } catch (err) { msg.textContent = errorText(err); }
    });
    return el("details", { class: "learn-stage" },
      el("summary", {}, el("strong", { text: x.id }), el("small", { class: "muted", text: ` · ${x.state}${x.update ? " · update available" : ""}${x.note ? ` · ${x.note}` : ""}` })),
      el("p", { class: "small", text: x.about }), el("a", { href: x.url, target: "_blank", rel: "noopener noreferrer", text: x.url }),
      el("div", { class: "row learn-actions" }, clone),
      steps.length ? el("ul", { class: "learn-res" }, ...steps) : el("p", { class: "muted small", text: x.state === "not cloned" ? "Clone it to see its install steps." : "No install steps found in its README." }));
  });
  const free = el("section", { class: "panel" }, el("h3", { text: "Free AI APIs" }),
    el("ul", { class: "brief" }, ...R.free.map((f) => el("li", {}, el("strong", { text: f.name }), ` · ${f.note} · `,
      el("span", { class: f.key ? "" : "muted", text: f.key ? "key saved" : "no key yet" }),
      f.trains_on_data ? el("small", { class: "warn-text", text: " · may train on what you send" }) : "", " · ",
      el("a", { href: f.get_key, target: "_blank", rel: "noopener noreferrer", text: "get a key" })))),
    el("p", { class: "muted small", text: "Keys go in APIs and keys (the vault). Limits change: check each site." }));
  box.replaceChildren(add, el("section", { class: "panel" }, el("h3", { text: "Repositories" }), ...cards), free);
}

// ---------------------------------------------------------------- Your day (plugins/daily): briefing, plan, check-in

function drawDaily(D) {
  const box = $("app-daily");
  const msg = el("p", { class: "conn-msg", role: "status" });
  const b = D.briefing;
  const story = (x) => el("li", {}, el("a", { href: x.url, target: "_blank", rel: "noopener noreferrer", text: x.summary }),
    x.why ? el("small", { class: "muted", text: ` · ${x.why}` }) : "", x.unsure ? el("small", { class: "warn-text", text: " · not sure" }) : "",
    el("small", { class: "muted", text: ` · ${x.source}` }));
  const build = button(b ? "Build again" : "Build now", "refresh", async () => {
    msg.textContent = "Fetching today's news... (up to a minute)";
    try { await api("/api/plugins/daily/briefing", {}, 180000); msg.textContent = ""; loadApp("daily", false); } catch (e) { msg.textContent = errorText(e); }
  });
  const read = button("Read aloud", "volume", () => {
    const text = (b?.ai || []).concat(b?.other || []).map((x) => x.summary).join(". ");
    if (text) { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(`In AI today: ${text}`)); }
  });
  const brief = el("section", { class: "panel learn-now" }, el("h3", { text: "Today's briefing" }),
    !b ? el("p", { class: "muted", text: `Not built yet today. It builds by itself at ${D.settings.briefing_time}.` })
      : b.offline || (!b.ai.length && !b.other.length) ? el("p", { class: "warn-note", text: b.note })
        : el("div", {}, el("h4", { text: "AI today" }), el("ul", { class: "brief" }, ...b.ai.map(story)),
          b.other.length ? el("h4", { text: "Other industries" }) : "", el("ul", { class: "brief" }, ...b.other.map(story)),
          el("p", { class: "muted small", text: `Only news AIRTAC fetched at ${b.made}, each with its link.${b.note ? ` ${b.note}` : ""}` })),
    el("div", { class: "row learn-actions" }, build, read), msg);

  const P = D.plan;
  const ticks = [];
  const rows = P.blocks.map((x, i) => {
    const t = el("input", { type: "checkbox", "aria-label": `Keep ${x.title}` });
    t.checked = true;
    t.disabled = x.kind === "fixed" || x.kind === "event";
    ticks.push([i, t]);
    return el("li", { class: `plan-block ${x.kind}` }, t, el("strong", { text: `${x.start}–${x.end}` }), ` ${x.title}`,
      x.note ? el("small", { class: "muted", text: ` · ${x.note}` }) : "");
  });
  const accept = button("Accept into Schedule", "calendar", async () => {
    const keep = ticks.filter(([, t]) => t.checked).map(([i]) => i);
    try { const r = await api("/api/plugins/daily/accept", { keep }, 15000); toast({ title: `Added ${r.added} blocks to today's Schedule` }); }
    catch (e) { toast({ title: errorText(e) }); }
  });
  const plan = el("section", { class: "panel" }, el("h3", { text: `Plan for today${P.light ? " (Light Mode)" : ""}` }),
    el("ul", { class: "learn-weeks" }, ...rows),
    P.dropped.length ? el("p", { class: "muted small", text: `Left out: ${P.dropped.join("; ")}` }) : "",
    el("p", { class: "muted small", text: `${Math.round(P.free_minutes / 60 * 10) / 10} hours stay free. Untick what you don't want, then Accept. Nothing is saved until you do.` }),
    el("div", { class: "row learn-actions" }, accept));

  const check = el("section", { class: "panel" }, el("h3", { text: "Evening check-in" }), el("div", { id: "checkin-box" },
    button("Start the check-in", "steps", drawCheckin)));

  const S = D.settings;
  const f = (k, label, type = "text") => {
    const input = el("input", { class: "field", type, name: k, value: Array.isArray(S[k]) ? S[k].join(", ") : String(S[k]) });
    if (type === "checkbox") { input.checked = Boolean(S[k]); input.value = "1"; }
    return el("label", { class: type === "checkbox" ? "switch" : "conn-field" }, type === "checkbox" ? input : el("span", { text: label }),
      type === "checkbox" ? ` ${label}` : input);
  };
  const form = el("form", { class: "panel daily-settings" }, el("h3", { text: "Settings" }),
    f("briefing_time", "Briefing time"), f("checkin_time", "Check-in time"), f("ai_topics", "AI topics (commas)"),
    f("other_industries", "Other industries (commas)"), f("quiet_start", "Quiet from"), f("quiet_end", "Quiet until"),
    f("proactive", "Messages I didn't ask for (all off when unticked)", "checkbox"), f("briefing_on", "Morning briefing message", "checkbox"),
    f("checkin_on", "Evening check-in message", "checkbox"), f("read_aloud", "Say \"briefing ready\" aloud", "checkbox"),
    el("p", { class: "muted small", text: "At most 3 messages a day, never in quiet hours." }),
    el("button", { type: "submit", class: "primary", text: "Save" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const v = (k) => form.elements[k];
    const body = { briefing_time: v("briefing_time").value, checkin_time: v("checkin_time").value, quiet_start: v("quiet_start").value,
      quiet_end: v("quiet_end").value, ai_topics: v("ai_topics").value.split(","), other_industries: v("other_industries").value.split(","),
      proactive: v("proactive").checked, briefing_on: v("briefing_on").checked, checkin_on: v("checkin_on").checked, read_aloud: v("read_aloud").checked };
    try { await api("/api/plugins/daily/settings", body, 10000); toast({ title: "Saved" }); } catch (err) { toast({ title: errorText(err) }); }
  });
  box.replaceChildren(brief, plan, check, el("section", { class: "panel" }, el("h3", { text: "This week" }), el("p", { text: D.review.line })), form);
}

async function drawCheckin() {
  let items;
  try { items = (await api("/api/plugins/daily/checkin", undefined, 10000)).items; } catch (e) { return toast({ title: errorText(e) }); }
  const boxes = items.map((x) => { const c = el("input", { type: "checkbox", value: String(x.id) }); c.checked = Boolean(x.done); return el("label", { class: "switch" }, c, ` ${x.time} ${x.title}`); });
  const feel = el("select", { class: "field" }, ...["easy", "ok", "hard"].map((x) => el("option", { value: x, text: x })));
  const blocked = el("input", { class: "field", maxlength: "300", placeholder: "Anything blocking you? (optional)" });
  const form = el("form", { class: "learn-quiz" },
    el("p", { text: items.length ? "1. What got done? (the rest moves to tomorrow)" : "Nothing from Your day was planned today." }), ...boxes,
    el("label", { class: "conn-field" }, el("span", { text: "2. How did today feel?" }), feel),
    el("label", { class: "conn-field" }, el("span", { text: "3. Anything blocking?" }), blocked),
    el("button", { type: "submit", class: "primary", text: "Done" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const done = [...form.querySelectorAll("input[type=checkbox]:checked")].map((c) => Number(c.value));
    try { const r = await api("/api/plugins/daily/checkin", { done, feeling: feel.value, blocked: blocked.value }, 15000);
      $("checkin-box").replaceChildren(el("p", { class: "warn-note", text: `Nice. ${r.done} done${r.moved ? `, ${r.moved} moved to tomorrow. No guilt.` : "."}` })); }
    catch (err) { toast({ title: errorText(err) }); }
  });
  $("checkin-box").replaceChildren(form);
}

// ---------------------------------------------------------------- Lectures (plugins/lectures): make, read, play

const lect = { voices: [], utter: null };

function localVoices() {
  const all = speechSynthesis.getVoices();
  return [...all.filter((v) => v.localService), ...all.filter((v) => !v.localService)];   // offline voices first
}

function drawLectures(L) {
  const box = $("app-lectures");
  const pick = el("select", { class: "field", "aria-label": "Attached file" }, el("option", { value: "", text: "Paste text instead" }),
    ...L.files.map((f) => el("option", { value: String(f.id), text: `${f.name} (${f.section})` })));
  const paste = el("textarea", { class: "field", rows: "4", maxlength: "60000", placeholder: "Paste a course outline or notes (or pick an attached file above)" });
  const title = el("input", { class: "field", maxlength: "100", placeholder: "Title (optional)" });
  const mode = el("select", { class: "field", "aria-label": "Style" }, el("option", { value: "teacher", text: "One teacher" }),
    el("option", { value: "conversation", text: "Two voices (teacher and student)" }));
  const msg = el("p", { class: "conn-msg", role: "status" });
  const form = el("form", { class: "panel learn-now" }, el("h3", { text: "Make a lecture" }),
    el("p", { class: "muted small", text: "Summary, glossary, a lecture in a teacher's voice and 5 quiz questions, from your file only, with page numbers. Attach files with the paperclip anywhere in AIRTAC." }),
    pick, paste, el("div", { class: "row" }, title, mode, el("button", { type: "submit", class: "primary", text: "Make it" })), msg);
  pick.addEventListener("change", () => { paste.hidden = Boolean(pick.value); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.textContent = "Writing your lecture... (a minute or two)";
    try {
      const r = await api("/api/plugins/lectures", { upload_id: pick.value ? Number(pick.value) : null, text: paste.value, title: title.value, mode: mode.value }, 300000);
      msg.textContent = "";
      await loadApp("lectures", false);
      openLecture(r.id);
    } catch (err) { msg.textContent = errorText(err); }
  });
  const list = el("section", { class: "panel" }, el("h3", { text: "Your lectures" }),
    L.items.length ? el("ul", { class: "learn-weeks" }, ...L.items.map((x) => {
      const b = el("button", { type: "button", class: "quiet-link", text: x.title });
      b.addEventListener("click", () => openLecture(x.id));
      return el("li", {}, b, el("small", { class: "muted", text: ` · ${x.mode === "conversation" ? "two voices" : "teacher"} · ${x.file}` }));
    })) : el("p", { class: "muted", text: "None yet." }));
  box.replaceChildren(form, el("div", { id: "lecture-view" }), list);
  lect.piper = L.piper;
}

async function openLecture(id) {
  let L;
  try { L = await api(`/api/plugins/lectures/${id}`, undefined, 15000); } catch (e) { return toast({ title: errorText(e) }); }
  stopLecture();
  const voices = localVoices();
  const vA = el("select", { class: "field", "aria-label": "Teacher's voice" }, ...voices.map((v, i) => el("option", { value: String(i), text: `${v.name}${v.localService ? "" : " (online)"}` })));
  const vB = el("select", { class: "field", "aria-label": "Student's voice" }, ...voices.map((v, i) => el("option", { value: String(i), text: v.name })));
  vB.value = String(Math.min(1, voices.length - 1));
  vB.hidden = L.mode !== "conversation";
  const rate = el("input", { type: "range", min: "0.6", max: "1.6", step: "0.1", value: "1", "aria-label": "Speed" });
  const lines = L.script.map((x) => el("p", { class: "lecture-line" }, el("strong", { text: `${x.speaker}: ` }), x.text));
  const play = button("Play", "play", () => playLecture(L, lines, voices, vA, vB, rate, 0));
  const pause = button("Pause", "stop", () => { if (speechSynthesis.paused) { speechSynthesis.resume(); pause.lastChild.textContent = "Pause"; } else { speechSynthesis.pause(); pause.lastChild.textContent = "Resume"; } });
  const stop = button("Stop", "close", stopLecture);
  const preview = button("Preview 3 voices", "volume", () => {
    stopLecture();
    voices.slice(0, 3).forEach((v, i) => { const u = new SpeechSynthesisUtterance(`Hi Ali, I'm voice ${i + 1}, ${v.name}.`); u.voice = v; speechSynthesis.speak(u); });
  });
  const saveAudio = button("Save audio", "install", async () => {
    try { const r = await api(`/api/plugins/lectures/${id}/audio`, {}, 600000); toast({ title: "Audio saved", body: r.file }); }
    catch (e) { toast({ title: errorText(e) }); }
  });
  saveAudio.hidden = !lect.piper;
  const del = button("Delete", "close", async () => {
    if (!del.dataset.armed) { del.dataset.armed = "1"; del.lastChild.textContent = "Press again to delete"; return; }
    await api(`/api/plugins/lectures/${id}/delete`, {}, 10000); loadApp("lectures", false);
  });
  const quiz = el("ol", {}, ...(L.quiz || []).map((q) => el("li", {}, `${q.q} `, el("details", { class: "inline-answer" }, el("summary", { text: "Answer" }), `${q.answer} (${q.where || ""})`))));
  $("lecture-view").replaceChildren(el("section", { class: "panel lecture" },
    el("h3", { text: L.title }), el("p", { class: "muted small", text: `From ${L.file}${L.partial ? " (the first part of a long file)" : ""}` }),
    el("div", { class: "row learn-actions" }, play, pause, stop, preview, saveAudio, del),
    el("div", { class: "row learn-actions" }, el("label", { class: "conn-field" }, el("span", { text: "Voice" }), vA),
      el("label", { class: "conn-field", ...(L.mode === "conversation" ? {} : { hidden: "" }) }, el("span", { text: "Student's voice" }), vB),
      el("label", { class: "conn-field" }, el("span", { text: "Speed" }), rate)),
    el("h4", { text: "Summary" }), el("p", { text: L.summary }),
    el("h4", { text: "Key concepts" }), el("ul", {}, ...(L.concepts || []).map((c) => el("li", {}, el("strong", { text: c.term }), `: ${c.meaning} `, el("small", { class: "muted", text: c.where || "" })))),
    el("h4", { text: "Lecture" }), ...lines,
    el("h4", { text: "Quiz" }), quiz,
    L.missing ? el("p", { class: "warn-note", text: `Not in the source: ${L.missing}` }) : ""));
  $("lecture-view").scrollIntoView({ behavior: "smooth" });
}

function playLecture(L, lines, voices, vA, vB, rate, i) {
  if (i === 0) stopLecture();
  if (i >= L.script.length) return;
  const x = L.script[i];
  const u = new SpeechSynthesisUtterance(x.text);
  u.voice = voices[Number(x.speaker === "Student" ? vB.value : vA.value)] || null;
  u.rate = Number(rate.value);
  lines.forEach((ln, j) => ln.classList.toggle("speaking", j === i));
  u.onend = () => { if (lect.utter === u) playLecture(L, lines, voices, vA, vB, rate, i + 1); };
  lect.utter = u;
  speechSynthesis.speak(u);
}

function stopLecture() {
  lect.utter = null;
  if (window.speechSynthesis) speechSynthesis.cancel();
  for (const ln of document.querySelectorAll(".lecture-line.speaking")) ln.classList.remove("speaking");
}

// ---------------------------------------------------------------- Settings: Memory and cache

async function loadMemoryCache() {
  const box = $("set-mem");
  if (!box) return;
  let kinds;
  try { kinds = (await api("/api/memory_cache", undefined, 10000)).kinds; }
  catch (e) { box.replaceChildren(el("p", { class: "muted", text: errorText(e) })); return; }
  box.replaceChildren(...kinds.filter((k) => k.id !== "history").map((k) => {
    const list = el("ul", { class: "mem-items", hidden: "" });
    const view = el("button", { type: "button", class: "quiet", text: "View" });
    view.hidden = !k.items || !k.count;
    view.addEventListener("click", async () => {
      if (!list.hidden) { list.hidden = true; view.textContent = "View"; return; }
      try {
        const items = (await api(`/api/memory_cache/${k.id}`, undefined, 10000)).items;
        list.replaceChildren(...items.map((it) => el("li", {}, el("span", { text: it.text }),
          historyDelete(async () => { await api(`/api/memory_cache/${k.id}/delete`, { id: it.id }, 10000); }))));
        list.hidden = false;
        view.textContent = "Hide";
      } catch (e) { toast({ title: errorText(e) }); }
    });
    const clear = el("button", { type: "button", class: "quiet", text: "Clear" });
    clear.hidden = !k.count;
    clear.addEventListener("click", async () => {
      if (!clear.dataset.armed) { clear.dataset.armed = "1"; clear.textContent = "Press again to clear"; setTimeout(() => { delete clear.dataset.armed; clear.textContent = "Clear"; }, 4000); return; }
      try { await api(`/api/memory_cache/${k.id}/delete`, {}, 15000); loadMemoryCache(); } catch (e) { toast({ title: errorText(e) }); }
    });
    return el("div", { class: "mem-row" }, el("span", { class: "mem-name" }, el("strong", { text: k.label }), el("small", { class: "muted", text: ` ${k.count}` })),
      el("span", { class: "spacer" }), view, clear, list);
  }));
}

// ---------------------------------------------------------------- files

async function loadFiles() {
  const list = $("files");
  let files = [];
  try {
    files = await api("/api/files", undefined, 15000);
  } catch { /* shown as empty */ }
  list.replaceChildren();
  if (!files.length) {
    list.append(el("li", { class: "empty", text: "No files yet. Try Work mode, Documents, Study or Diagrams." }));
    return;
  }
  for (const f of files) {
    const ext = f.name.split(".").pop().toLowerCase();
    const isImage = ext === "png";
    const thumb = isImage ? el("img", { src: API + f.url, alt: "", loading: "lazy" })
      : el("span", { class: "kind", text: { docx: "Word", pptx: "Slides", xlsx: "Excel", md: "Text" }[ext] || ext.toUpperCase() });
    list.append(el("li", {}, thumb, el("span", {},
      el("a", { href: API + f.url, target: "_blank", rel: "noopener", text: f.name }), el("br"),
      el("small", { text: `${Math.max(1, Math.round(f.size / 1024))} KB${f.plugin ? `, from ${f.plugin}` : ""}` }))));
  }
}

// ---------------------------------------------------------------- dialogs: paste problem and syllabus

function openDialog(id) {
  setDrawer(false);
  const dialog = $(id);
  if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
}

async function pasteProblem() {
  try {
    // Only works on https or localhost, and the browser asks for permission first.
    $("problem").value = (await navigator.clipboard.readText()).slice(0, 12000);
    if (!$("problem").value.trim()) $("problem").placeholder = "The clipboard is empty. Copy the problem first.";
  } catch {
    $("problem").placeholder = "The browser blocked clipboard access. Paste here with Ctrl+V instead.";
    $("problem").focus();
  }
}

function solvePasted() {
  const problem = $("problem").value.trim();
  if (!problem) { $("problem").focus(); return; }
  const language = $("language").value;
  const names = { python: "Python", "c++": "C++", java: "Java", c: "C" };
  $("paste-dialog").close();
  ask(`Solve this in ${names[language]}`, { problem_text: problem, site: $("site").value, language });
}

function fileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("Couldn't read that file."));
    reader.readAsDataURL(file);
  });
}

async function saveSyllabus() {
  const subject = $("subject").value.trim();
  const text = $("syllabus").value.trim();
  const file = $("syllabus-pdf").files[0];
  if (!subject) { $("subject").focus(); return; }
  if (!text && !file) { $("syllabus").focus(); return; }
  if (file && file.size > 5 * 1024 * 1024) { $("syllabus").placeholder = "That PDF is over 5 MB. Paste the text instead."; return; }
  setBusy(true);
  try {
    const body = { subject, text, pdf_base64: file ? await fileAsBase64(file) : "" };
    const data = await api("/api/plugins/study/syllabus", body);
    $("syllabus-dialog").close();
    if (currentView === "app") { addBubble("ai", data); loadApp(currentApp, false); } else render(data);
  } catch (e) {
    $("syllabus").placeholder = errorText(e);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------- speech out

const canSpeak = "speechSynthesis" in window;
const speakToggle = $("speak-toggle");
speakToggle.checked = canSpeak && load("airtac-speak") !== "off";
speakToggle.disabled = !canSpeak;
speakToggle.addEventListener("change", () => {
  save("airtac-speak", speakToggle.checked ? "on" : "off");
  if (!speakToggle.checked && canSpeak) speechSynthesis.cancel();
});

// Voices: Edge's "Natural" voices (for example Microsoft Neerja, English India) sound far better than
// the older ones, but stream from Microsoft, so they need internet. Pick one in the menu, or let
// AIRTAC choose: Indian English first, natural voices first.
let voices = [];
let speakingText = "";

function loadVoices() {
  if (!canSpeak) return;
  voices = speechSynthesis.getVoices().filter((v) => /^en/i.test(v.lang));
  const pick = $("voice-pick");
  const saved = load("airtac-voice") || "";
  pick.replaceChildren(el("option", { value: "", text: "Automatic (best English voice)" }));
  for (const v of voices) {
    pick.append(el("option", { value: v.name, text: `${v.name.replace(/^Microsoft /, "")} (${v.lang})` }));
  }
  pick.value = voices.some((v) => v.name === saved) ? saved : "";
}

function chooseVoice() {
  const saved = load("airtac-voice");
  const exact = voices.find((v) => v.name === saved);
  if (exact) return exact;
  const score = (v) => (v.lang.replace("_", "-") === LANG ? 4 : /en-(IN|GB)/i.test(v.lang) ? 2 : 1)
    + (/natural|neural|online/i.test(v.name) ? 3 : 0);
  return voices.slice().sort((a, b) => score(b) - score(a))[0] || null;
}

// Every utterance goes through here, so the logo and the Listen buttons always know what's playing.
function utter(text, { onBlocked } = {}) {
  const u = new SpeechSynthesisUtterance(text);
  const v = chooseVoice();
  if (v) u.voice = v;
  u.lang = v ? v.lang : LANG;
  u.rate = Number(load("airtac-rate") || 1);
  u.onstart = () => { speakingText = text; setLive("speaking", true); syncListenButtons(); };
  const done = () => { if (speakingText === text) speakingText = ""; setLive("speaking", false); syncListenButtons(); };
  u.onend = done;
  u.onerror = (event) => {
    done();
    if (event.error === "not-allowed" && onBlocked) onBlocked();
  };
  return u;
}

function speak(text, always = false, options = {}) {
  if (voiceCfg.tts === "local" && text && (always || speakToggle.checked)) {
    localSpeak(text).then((ok) => { if (!ok) speakBrowser(text, always, options); });
    return null;
  }
  return speakBrowser(text, always, options);
}

function speakBrowser(text, always = false, options = {}) {
  if (!canSpeak || !text || (!speakToggle.checked && !always)) return null;
  speechSynthesis.cancel(); // don't queue up old replies
  const u = utter(text, options);
  speechSynthesis.speak(u);
  return u;
}

function stopSpeaking() {
  if (canSpeak) speechSynthesis.cancel();
  speakingText = "";
  setLive("speaking", false);
  syncListenButtons();
}

// A Listen button turns into Stop while its own text is being read.
function syncListenButtons() {
  const label = $("today-listen").querySelector("span");
  if (label) label.textContent = speakingText && speakingText === daySpoken ? "Stop" : "Listen";
}

// ---------------------------------------------------------------- phones: wake up sound with the first tap
// Phone browsers won't play sound or speak until you touch the page once. On a phone AIRTAC shows
// a "Start AIRTAC" button; that tap (or any first tap) resumes the Web Audio context, plays a silent
// sound and speaks an empty line. After that it can speak by itself for the rest of the visit.
// (Only taps count: a touch starts on pointerdown, but phones grant permission on pointerup.)
const SILENT_WAV = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=";
let audioCtx = null;
let audioUnlocked = false;
let startDay = null;

function isPhone() {
  return matchMedia("(max-width: 760px), (pointer: coarse)").matches;
}

function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) {
      audioCtx = audioCtx || new AC();
      if (audioCtx.state === "suspended") audioCtx.resume();
      const src = audioCtx.createBufferSource();
      src.buffer = audioCtx.createBuffer(1, 1, 22050);
      src.connect(audioCtx.destination);
      src.start(0);
    }
  } catch { /* no Web Audio: speech still works */ }
  try { new Audio(SILENT_WAV).play().catch(() => {}); } catch { /* old browser */ }
  if (canSpeak) {
    try {
      const hush = new SpeechSynthesisUtterance(" ");
      hush.volume = 0;
      speechSynthesis.speak(hush);
      speechSynthesis.resume();  // Chrome on Android can leave speech paused after the tab sleeps
    } catch { /* speech not ready yet */ }
  }
  // Hide the button a moment later, so a tap that landed on it still counts as its click.
  setTimeout(() => { $("start-airtac").hidden = true; }, 400);
}

function showStart(day) {
  startDay = day ?? startDay;
  if (!audioUnlocked) $("start-airtac").hidden = false;
}

function startAirtac() {
  unlockAudio();
  $("start-airtac").hidden = true;
  $("today-tap").hidden = true;
  // Speak right inside the tap, while the browser still counts it as yours.
  if (daySpoken) playDay(startDay || load("airtac-announced"));
  else speak("AIRTAC is ready. Ask me anything.", true);
}

for (const type of ["pointerup", "touchend", "keydown"]) {
  document.addEventListener(type, unlockAudio, { capture: true, once: true, passive: true });
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !audioUnlocked) return;
  if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
  if (canSpeak) speechSynthesis.resume();
});

// ---------------------------------------------------------------- speech in

// Chrome and Edge have this (they send audio to their cloud service); Firefox doesn't.
// One recogniser serves both mics (home and the app chat); `voice` says where the words go.
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const HOLD_MS = 400; // pressed longer than this = hold-to-talk, letting go stops listening
let recognizer = null;
let listening = false;
let pressedAt = 0;
let heard = "";
let voice = { mic: "mic", input: "text", submit: (t) => send(t) };
const HINT = $("mic-hint").textContent;

function voiceNote(msg) {
  if (voice.mic === "mic") $("mic-hint").textContent = msg;
  else $("chat-text").placeholder = msg;
}

if (Recognition) {
  recognizer = new Recognition();
  recognizer.lang = LANG;
  recognizer.interimResults = true; // show words as they come
  recognizer.continuous = false;    // stop by itself after a pause

  recognizer.onstart = () => {
    listening = true;
    setLive("listening", true);
    heard = "";
    $(voice.mic).classList.add("listening");
    voiceNote("Listening...");
  };
  recognizer.onresult = (event) => {
    let text = "";
    for (const result of event.results) text += result[0].transcript;
    $(voice.input).value = text;
    heard = text;
  };
  recognizer.onerror = (event) => {
    const messages = {
      "not-allowed": "The microphone is blocked. Allow it in the browser, or type instead.",
      "network": "Voice input needs internet in this browser. Type instead.",
      "no-speech": "I didn't hear anything. Try again.",
    };
    if (event.error !== "aborted") voiceNote(messages[event.error] || `Voice error: ${event.error}`);
  };
  recognizer.onend = () => {
    listening = false;
    setLive("listening", false);
    $(voice.mic).classList.remove("listening");
    if ($("mic-hint").textContent === "Listening...") $("mic-hint").textContent = HINT;
    if ($("chat-text").placeholder === "Listening...") $("chat-text").placeholder = "Ask this app";
    const text = heard;
    heard = "";
    if (text.trim()) voice.submit(text);
  };
} else {
  for (const id of ["mic", "chat-mic"]) {
    $(id).disabled = true;
    $(id).title = "Voice input isn't available in this browser. Try Chrome or Edge.";
  }
}

function startListening() {
  if (!recognizer || busy) return;
  if (canSpeak) speechSynthesis.cancel(); // don't let AIRTAC hear itself
  try { recognizer.start(); } catch { /* already started */ }
}

function stopListening() {
  if (recognizer && listening) {
    try { recognizer.stop(); } catch { /* already stopped */ }
  }
}

// Click to talk, or hold and let go. `target` says which input and which send function to use.
function wireMic(id, target) {
  const mic = $(id);
  mic.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    if (voiceCfg.stt === "local") { voice = { mic: id, ...target }; localListen({ mic: id, ...target }); return; }
    if (listening) { // second click stops
      pressedAt = 0;
      stopListening();
      return;
    }
    voice = { mic: id, ...target };
    mic.setPointerCapture(event.pointerId); // so we still get pointerup if the finger slides off
    pressedAt = Date.now();
    startListening();
  });
  mic.addEventListener("pointerup", () => {
    if (pressedAt && Date.now() - pressedAt > HOLD_MS) stopListening();
    pressedAt = 0;
  });
  mic.addEventListener("click", (event) => {
    if (event.detail !== 0) return; // keyboard Enter/Space
    voice = { mic: id, ...target };
    listening ? stopListening() : startListening();
  });
}

wireMic("mic", { input: "text", submit: (t) => send(t) });
wireMic("chat-mic", { input: "chat-text", submit: (t) => sendApp(t) });

// ---------------------------------------------------------------- app pages

// Ask from wherever you are: an app page keeps its own chat, everything else goes to Home.
function ask(text, extra = {}) {
  return currentView === "app" ? sendApp(text, extra) : send(text, extra);
}

let appsWaiting = null;   // an app page asked for before the app list arrived

async function openApp(id) {
  document.body.dataset.view = "app";
  hideTip();
  if (!APPS.length) { appsWaiting = id; return; }   // the list isn't loaded yet: open it when it is (a reload on #app-...)
  if (!APPS.some((a) => a.id === id)) return showView("home");
  setDrawer(false);
  currentView = "app";
  if (currentApp !== id && window.Attach) Attach.clear("app");   // files belong to the app they were attached in
  currentApp = id;
  for (const v of VIEWS) $(`view-${v}`).hidden = v !== "app";
  setHash(`app-${id}`);
  markCurrent(`app-${id}`);
  const a = APPS.find((x) => x.id === id);
  $("app-icon").replaceChildren(icon(a.icon));
  $("app-name").textContent = a.name;
  $("app-about").textContent = a.about;
  $("chat-text").placeholder = `Ask ${a.name}`;
  for (const id2 of ["app-actions", "app-stats", "app-bars", "app-lists", "chat-log", "app-usage"]) $(id2).replaceChildren();
  $("app-schedule").hidden = true;
  $("app-library").hidden = true;
  $("app-lessons").hidden = true;
  $("app-accounts").hidden = true;
  window.scrollTo(0, 0);
  await loadApp(id, true);
}

async function loadApp(id, withChat) {
  let p;
  try {
    p = await api(`/api/apps/${id}`, undefined, 20000);
  } catch (e) {
    $("app-stats").replaceChildren(el("p", { class: "muted", text: errorText(e) }));
    return;
  }
  if (currentApp !== id) return;  // you moved on while it loaded
  drawDashboard(p);
  if (withChat) drawChat(p.chats);
}

function drawDashboard(p) {
  const used = p.usage.uses ? `Used ${p.usage.uses} time${p.usage.uses === 1 ? "" : "s"}` +
    (p.usage.last ? `, last ${new Date(p.usage.last).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}` : "")
    : "Not used yet. Try one of the actions below or ask in the chat.";
  $("app-usage").textContent = p.note ? `${used} ${p.note}` : used;

  const actions = $("app-actions");
  actions.replaceChildren();
  if (p.action === "paste") actions.append(chip("Paste a problem", () => openDialog("paste-dialog"), true));
  if (p.action === "syllabus") actions.append(chip("Add your syllabus", () => openDialog("syllabus-dialog"), true));
  for (const q of p.quick) actions.append(chip(q, () => sendApp(q)));

  const stats = $("app-stats");
  stats.replaceChildren(...p.stats.map((s) => el("div", { class: "stat" },
    el("b", { text: String(s.value) }), el("span", {}, s.label))));

  const bars = $("app-bars");
  bars.replaceChildren();
  if (p.bars && p.bars.items.length) bars.append(drawBars(p.bars));

  $("app-schedule").hidden = p.action !== "schedule";
  $("app-library").hidden = p.action !== "library";
  if (p.library) $("lib-note").textContent = `Folder: ${p.library.folder}` + (p.library.indexing ? " (reading now...)" : "")
    + (p.library.last ? `. Last read ${p.library.last}.` : "");
  $("app-learn").hidden = p.action !== "learn";
  if (p.action === "learn" && p.learn) drawLearn(p.learn);
  $("app-repos").hidden = p.action !== "repos";
  if (p.action === "repos" && p.repos) drawRepos(p.repos);
  $("app-daily").hidden = p.action !== "daily";
  if (p.action === "daily" && p.daily) drawDaily(p.daily);
  $("app-lectures").hidden = p.action !== "lectures";
  if (p.action === "lectures" && p.lectures) drawLectures(p.lectures);
  $("app-lessons").hidden = p.action !== "lessons";
  if (p.action === "lessons") drawLessons(p.lessons || []);
  $("app-accounts").hidden = p.action !== "accounts";
  if (p.action === "accounts" && p.accounts) drawAccounts(p.accounts);
  if (p.action === "schedule") {
    loadSchedule(); drawTemplates(); loadBriefing(false);
    api("/api/plugins/schedule/tidy", undefined, 10000).then((r) => { $("ev-tidy").value = String(r.days); }).catch(() => {});
  }

  const lists = $("app-lists");
  lists.replaceChildren();
  for (const l of p.lists) lists.append(drawList(l));
}

// ---------------------------------------------------------------- Accounts (LeetCode, CodeChef, GfG, EduSkills)
// Links open the real site in a new tab; only usernames ever come back to AIRTAC, never passwords.
function accLink(text, href, primary = false) {
  if (!href) return "";
  const a = el("a", { href, target: "_blank", rel: "noopener noreferrer", class: primary ? "primary" : "" }, text, icon("external"));
  a.addEventListener("click", (event) => openSite(event, href));  // on the PC: AIRTAC's Chrome profile, so you stay signed in
  return a;
}

// Login details: kept in Windows' password store, shown only on this PC, never sent to the AI.
function accVault(c) {
  const saved = c.vault || {};
  const box = el("details", { class: "acc-vault" }, el("summary", {}, "Login details",
    saved.password || saved.cookie ? el("span", { class: "muted small", text: ` · saved ${saved.password || saved.cookie}` }) : ""));
  if (!launch.here) {
    box.append(el("p", { class: "muted small", text: "For your safety, login details can only be seen or changed on the PC running AIRTAC." }));
    return box;
  }
  const pw = el("input", { type: "password", class: "field", autocomplete: "new-password", "aria-label": `${c.name} password`,
    placeholder: saved.password ? `Password saved ${saved.password}` : "Password (optional)" });
  const show = el("button", { type: "button", class: "quiet small", text: "Show" });
  show.addEventListener("click", async () => {
    if (pw.type === "text") { pw.type = "password"; pw.value = ""; show.textContent = "Show"; return; }
    try {
      const r = await api("/api/plugins/accounts/vault/reveal", { site: c.id, field: "password" }, 10000);
      pw.value = r.value; pw.type = "text"; show.textContent = "Hide";
    } catch (e) { $("acc-note").textContent = errorText(e); }
  });
  const cookie = el("textarea", { class: "field mono", rows: "3", "aria-label": `${c.name} session cookie`,
    placeholder: saved.cookie ? `Session cookie saved ${saved.cookie}. Paste a new one to replace it.`
      : "Session cookie (optional), for signed-in data" });
  const keep = el("button", { type: "button", class: "quiet small", text: "Save" });
  keep.addEventListener("click", async () => {
    const body = { site: c.id };
    if (pw.value && pw.type === "password") body.password = pw.value;
    if (cookie.value.trim()) body.cookie = cookie.value.trim();
    if (!body.password && !body.cookie) { $("acc-note").textContent = "Type a password or paste a cookie first."; return; }
    try {
      await api("/api/plugins/accounts/vault", body, 10000);
      $("acc-note").textContent = `Saved ${c.name}'s login details in Windows' password store.`;
      loadApp("accounts", false);
    } catch (e) { $("acc-note").textContent = errorText(e); }
  });
  const forget = el("button", { type: "button", class: "quiet small", text: "Delete saved" });
  forget.hidden = !(saved.password || saved.cookie);
  forget.addEventListener("click", async () => {
    try { await api("/api/plugins/accounts/vault/delete", { site: c.id }, 10000); loadApp("accounts", false); }
    catch (e) { $("acc-note").textContent = errorText(e); }
  });
  box.append(el("div", { class: "acc-pw" }, pw, show), cookie, el("div", { class: "acc-vault-actions" }, keep, forget));
  return box;
}

function accCard(c, live) {
  const card = el("article", { class: "acc-card", id: `acc-${c.id}` });
  const line = el("p", { class: "acc-line" }, c.line,
    c.checked ? el("em", { text: ` · checked ${c.checked}` }) : "");
  card.append(el("h4", { text: c.name }), line);
  if (c.needs_username) {
    const input = el("input", { type: "text", class: "field", value: c.username, placeholder: "Your username", autocomplete: "off",
      spellcheck: "false", "aria-label": `${c.name} username` });
    const form = el("form", {}, input, el("button", { type: "submit", class: "quiet", text: "Save" }));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const r = await api("/api/plugins/accounts/username", { site: c.id, username: input.value.trim() });
        card.replaceWith(accCard(r.card, live));
        $("acc-note").textContent = `Saved your ${c.name} username.` + (live ? " Press Refresh stats to read it." : "");
      } catch (err) { $("acc-note").textContent = errorText(err); }
    });
    card.append(form);
  }
  card.append(el("div", { class: "acc-links" }, accLink("Sign up", c.links.signup), accLink("Log in", c.links.login, true),
    accLink(c.id === "eduskills" ? "Dashboard" : "Profile", c.links.profile)));
  card.append(el("small", { text: c.how }));
  card.append(accVault(c));
  return card;
}

async function drawAccounts(a) {
  if (!launch.tiles.length && !launch.here) await loadLaunch();  // learn whether this is the PC itself
  $("acc-cards").replaceChildren(...a.cards.map((c) => accCard(c, a.live)));
  $("acc-note").textContent = a.vault && !a.vault.ok ? a.vault.why : "";
}

$("acc-refresh").addEventListener("click", async () => {
  const b = $("acc-refresh");
  b.disabled = true;
  $("acc-note").textContent = "Reading your public profiles...";
  try {
    const r = await api("/api/plugins/accounts/refresh", {}, 45000);
    drawAccounts(r);
    if (r.live) $("acc-note").textContent = "Updated.";
  } catch (err) { $("acc-note").textContent = errorText(err); }
  b.disabled = false;
});

function chip(text, onClick, primary = false) {
  const b = el("button", { type: "button", class: primary ? "chip-btn primary" : "chip-btn", text });
  b.addEventListener("click", onClick);
  return b;
}

function drawBars(b) {
  const panel = el("section", { class: "panel" }, el("h3", { text: b.title }));
  if (b.kind === "progress") {  // done out of total, e.g. schedule items per category
    for (const it of b.items) {
      const fill = el("div", { class: "fill" });
      fill.style.width = `${it.total ? (it.value / it.total) * 100 : 0}%`;
      panel.append(withTip(el("div", { class: "meter" }, el("span", { text: it.label }),
        el("div", { class: "track" }, fill), el("span", { class: "val", text: `${it.value} of ${it.total}` })),
        `${it.label}: ${it.value} of ${it.total} done`));
    }
    return panel;
  }
  const max = Math.max(1, ...b.items.map((i) => i.value));
  for (const it of b.items) {
    const fill = el("div", { class: "fill" });
    fill.style.width = `${(it.value / max) * 100}%`;
    panel.append(withTip(el("div", { class: "hbar" }, el("span", { text: it.label }),
      el("div", { class: "track" }, fill), el("span", { class: "val", text: String(it.value) })), `${it.label}: ${it.value}`));
  }
  return panel;
}

function drawList(l) {
  const panel = el("section", { class: "panel" }, el("h3", { text: l.title }));
  if (!l.items.length) { panel.append(el("p", { class: "muted", text: l.empty })); return panel; }
  if (l.items.every((i) => i.image)) {
    panel.append(el("div", { class: "gallery" }, ...l.items.map((i) =>
      el("a", { href: API + i.image, target: "_blank", rel: "noopener" }, el("img", { src: API + i.image, alt: i.title, loading: "lazy" })))));
    return panel;
  }
  const rows = el("ul", { class: "rows" });
  for (const i of l.items) {
    const head = i.url ? el("a", { href: i.url.startsWith("/") ? API + i.url : i.url, target: "_blank", rel: "noopener", text: i.title })
      : el("strong", { text: i.title });
    rows.append(el("li", {}, head, i.meta ? el("span", { text: i.meta }) : ""));
  }
  panel.append(rows);
  return panel;
}

// ---------------------------------------------------------------- an app's chat

function drawChat(chats) {
  const log = $("chat-log");
  log.replaceChildren();
  if (!chats.length) {
    log.append(el("li", { class: "chat-empty", text: "No messages yet. Type or use the mic. Replies show here and are read aloud." }));
    return;
  }
  for (const m of chats) addBubble(m.role === "user" ? "you" : "ai", { spoken: m.content });
}

function addBubble(who, data) {
  const log = $("chat-log");
  const empty = log.querySelector(".chat-empty");
  if (empty) empty.remove();
  const li = el("li", { class: `msg ${who}` }, el("div", { text: data.spoken }));
  const urls = who === "ai" ? [...new Set((data.screen || "").match(URL_RE) || [])].slice(0, 3) : [];
  if (urls.length) li.append(el("div", { class: "msg-links" }, ...urls.map((u) =>
    el("a", { href: u, target: "_blank", rel: "noopener noreferrer", text: u.replace(/^https:\/\/(www\.)?/, "") }))));
  if (who === "ai") {
    const meta = el("div", { class: "meta" });
    const listen = el("button", { type: "button", class: "listen", "aria-label": "Listen to this reply" }, icon("volume"), "Listen");
    listen.addEventListener("click", () => speak(data.spoken, true));
    meta.append(listen);
    const steps = (data.steps || []).map((s) => TOOL_NAMES[s.tool] || s.tool);
    if (steps.length) meta.append(el("span", { class: "used", text: `Used ${steps.join(", ")}` }));
    if (brainText(data.brain)) meta.append(el("span", { class: "used", text: brainText(data.brain) }));
    if ((data.noticed || []).length) { const nb = el("div", { class: "noticed" }); drawNoticed(nb, data.noticed); li.append(nb); }
    if (data.message_id) {
      const good = el("button", { type: "button", class: "icon-btn small", "aria-label": "Good answer" }, icon("thumb-up"));
      const bad = el("button", { type: "button", class: "icon-btn small", "aria-label": "Wrong answer" }, icon("thumb-down"));
      const rate = async (ok) => {
        const note = ok ? "" : (prompt("What should I have said? I'll remember it as a lesson.") || "");
        good.disabled = bad.disabled = true;
        try { await sendFeedback(data.message_id, ok, note); } catch { /* not important */ }
      };
      good.addEventListener("click", () => rate(true));
      bad.addEventListener("click", () => rate(false));
      meta.append(good, bad);
    }
    li.append(meta);
    if ((data.sources || []).length) li.append(el("ul", { class: "sources" }, ...sourceItems(data.sources)));
    const audio = (data.screen || "").match(/^Audio: (\/api\/plugins\/music\/play\/\w+)$/m);
    if (audio) li.append(el("audio", { src: API + audio[1], controls: "", autoplay: "" }));
    const image = (data.screen || "").match(/^Image: (\/files\/[\w\/.-]+\.png)$/m);
    if (image) li.append(el("img", { src: API + image[1], alt: "Image from this reply" }));
    if (data.screen && !image) {
      const details = el("details", {}, el("summary", { text: "Details" }), el("pre", { text: data.screen }));
      li.append(details);
    }
    if (data.pending) {
      li.append(el("pre", { class: "pending-args", text: pendingArgs(data.pending) }));
      const ok = chip("Confirm", () => answerPendingInApp(data.pending.id, true, li), true);
      const no = chip("Cancel", () => answerPendingInApp(data.pending.id, false, li));
      li.append(el("div", { class: "meta" }, ok, no));
    }
  }
  log.append(li);
  log.scrollTop = log.scrollHeight;
  return li;
}

async function sendApp(text, extra = {}) {
  if (busy || !currentApp) return;
  const withF = withFiles("app", text, extra);
  if (!withF) return;
  ({ text, extra } = withF);
  text = text.trim();
  if (!text) return;
  const id = currentApp;
  $("chat-text").value = "";
  addBubble("you", { spoken: text + (extra.problem_text ? " (with a pasted problem)" : "") + (withF.files ? ` (with ${withF.files} file${withF.files === 1 ? "" : "s"})` : "") });
  if (withF.files) Attach.clear("app");
  const thinking = addBubble("ai", { spoken: "Working on it..." });
  setBusy(true);
  try {
    const data = await api("/api/chat", { message: text, session_id: `app:${id}`, brain: chosenBrain(), ...extra }, 180000);
    thinking.remove();
    chatMeta(data, "app");
    if (currentApp === id) addBubble("ai", data);
    speak(data.spoken);
    if (currentApp === id) loadApp(id, false);  // numbers may have changed
  } catch (e) {
    thinking.firstChild.textContent = errorText(e);
  } finally {
    setBusy(false);
  }
}

// The confirm card shows every argument in full, so text slipped in by a web page, an issue or a
// PDF the model read can't hide inside an action you approve.
function pendingArgs(pending) {
  return Object.entries(pending.args || {})
    .map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join("\n");
}

async function answerPendingInApp(pending, approve, bubble) {
  for (const b of bubble.querySelectorAll(".chip-btn")) b.disabled = true;
  try {
    const data = await api("/api/confirm", { pending_id: pending, approve });
    addBubble("ai", data);
    speak(data.spoken);
  } catch (e) {
    addBubble("ai", { spoken: errorText(e) });
  }
}

// ---------------------------------------------------------------- the Schedule app's editor

async function loadSchedule() {
  let data;
  try {
    data = await api("/api/plugins/schedule", undefined, 15000);
  } catch (e) {
    $("ev-list").replaceChildren(el("p", { class: "muted", text: errorText(e) }));
    return;
  }
  sched.events = data.events;
  setSchedView(sched.view);
  const list = $("ev-list");
  list.replaceChildren();
  if (!data.events.length) {
    list.append(el("p", { class: "muted", text: "Nothing on your schedule yet. Add an exam or deadline above, or add the internship plan." }));
    return;
  }
  let month = "";
  for (const e of data.events) {
    const d = new Date(e.date + "T00:00:00");
    const m = d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    if (m !== month) { month = m; list.append(el("p", { class: "month", text: m })); }
    const box = el("input", { type: "checkbox", "aria-label": `Done: ${e.title}` });
    box.checked = Boolean(e.done);
    box.addEventListener("change", () => updateEvent(e.id, box.checked));
    const when = d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + (e.time ? `, ${e.time}` : "");
    const what = el("div", { class: "what" }, el("strong", { text: e.title }),
      el("small", {}, el("span", { class: "tag", text: e.category }), e.estimated ? el("span", { class: "tag est", text: "estimated" }) : "",
        e.repeat ? el("span", { class: "tag", text: { yearly: "every year", monthly: "every month", weekly: "every week" }[e.repeat] }) : "",
        e.notes || ""));
    if (/^https:\/\//.test(e.source || "")) what.append(el("small", {}, el("a", { href: e.source, target: "_blank", rel: "noopener", text: "Source" })));
    if (e.source === "chat") what.querySelector("small").prepend(el("span", { class: "tag", text: "from chat" }));
    const edit = el("button", { type: "button", class: "quiet small edit", text: "Edit", "aria-label": `Edit ${e.title}` });
    edit.addEventListener("click", () => startEdit(e));
    const remove = el("button", { type: "button", class: "icon-btn small remove", "aria-label": `Delete ${e.title}` }, icon("close"));
    remove.addEventListener("click", () => removeEvent(e));
    const row = el("div", { class: e.done ? "event done" : "event" }, box, el("time", { datetime: e.date, text: when }), what,
      el("span", { class: "event-actions" }, edit, remove));
    if (editingId === e.id) row.classList.add("editing");
    list.append(row);
  }
}

// ---------------------------------------------------------------- Schedule: import, calendar view, ask

const sched = { view: load("airtac-sched-view") || "list", month: null, events: [], preview: null };

async function importSchedule(file) {
  const note = $("imp-note");
  if (!file) return;
  const start = $("imp-start").value === "date" ? $("imp-date").value : $("imp-start").value;
  if ($("imp-start").value === "date" && !start) { note.textContent = "Pick the day the plan should start on."; return; }
  try {
    note.textContent = `Uploading ${file.name}...`;
    const up = await api("/api/upload", { section: "schedule", name: file.name, data_base64: await fileAsBase64(file) }, 180000);
    let row = up;
    for (let n = 0; row.status === "reading" && n < 400; n++) {
      note.textContent = row.kind === "image" ? "Reading the picture (OCR)..." : "Reading the file...";
      await sleep(n < 10 ? 800 : 1500);
      row = await api(`/api/plugins/attachments/${up.id}`, undefined, 20000);
    }
    if (row.status !== "ready") { note.textContent = row.message || "Couldn't read that file."; return; }
    const r = await api("/api/plugins/schedule/import", { attachment: up.id, start }, 60000);
    note.textContent = r.note;
    drawImport(r.items);
  } catch (e) { note.textContent = errorText(e); }
}

function drawImport(items) {
  const box = $("imp-preview");
  sched.preview = items.map((x) => ({ ...x, keep: true }));
  if (!items.length) { box.hidden = true; return; }
  const rows = sched.preview.map((x) => {
    const tick = el("input", { type: "checkbox", "aria-label": `Add ${x.title}` });
    tick.checked = true;
    tick.addEventListener("change", () => { x.keep = tick.checked; });
    const d = new Date(x.date + "T00:00:00");
    return el("label", { class: "imp-row" }, tick, el("time", { datetime: x.date, text: d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" }) + (x.time ? `, ${x.time}` : "") }),
      el("span", { text: x.title }), el("span", { class: "tag", text: x.category }));
  });
  const saveBtn = el("button", { type: "button", class: "primary", text: `Add ${items.length} to my schedule` });
  saveBtn.addEventListener("click", saveImport);
  const cancel = el("button", { type: "button", class: "quiet", text: "Cancel" });
  cancel.addEventListener("click", () => { box.hidden = true; sched.preview = null; });
  box.replaceChildren(el("p", { class: "small", text: "Check the items, untick any you don't want, then add them:" }), ...rows, el("div", { class: "row" }, saveBtn, cancel));
  box.hidden = false;
}

async function saveImport() {
  const items = (sched.preview || []).filter((x) => x.keep).map(({ title, date, time, category, notes, source }) => ({ title, date, time, category, notes, source }));
  try {
    const r = await api("/api/plugins/schedule/import/save", { items }, 60000);
    $("imp-note").textContent = `Added ${r.added}` + (r.skipped ? `; ${r.skipped} were already on your schedule.` : ".");
    $("imp-preview").hidden = true;
    loadApp("schedule", false);
  } catch (e) { $("imp-note").textContent = errorText(e); }
}

async function askSchedule(event) {
  event.preventDefault();
  const q = $("sched-q").value.trim();
  if (!q) return;
  const out = $("sched-answer");
  out.hidden = false;
  out.textContent = "Looking at your schedule...";
  try { out.textContent = (await api("/api/plugins/schedule/ask", { q }, 120000)).answer; }
  catch (e) { out.textContent = errorText(e); }
}

function setSchedView(view) {
  sched.view = view;
  save("airtac-sched-view", view);
  $("view-list").setAttribute("aria-selected", String(view === "list"));
  $("view-cal").setAttribute("aria-selected", String(view === "cal"));
  $("ev-list").hidden = view !== "list";
  $("ev-cal").hidden = view !== "cal";
  if (view === "cal") drawCalendar();
}

// A month grid (Monday first). Each day lists its items; a tap on one opens it in the list's editor.
function drawCalendar() {
  const box = $("ev-cal");
  const today = new Date();
  const m = sched.month || new Date(today.getFullYear(), today.getMonth(), 1);
  sched.month = m;
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const byDay = new Map();
  for (const e of sched.events) { if (!byDay.has(e.date)) byDay.set(e.date, []); byDay.get(e.date).push(e); }
  const prev = el("button", { type: "button", class: "icon-btn small", "aria-label": "Previous month", text: "‹" });
  const next = el("button", { type: "button", class: "icon-btn small", "aria-label": "Next month", text: "›" });
  prev.addEventListener("click", () => { sched.month = new Date(m.getFullYear(), m.getMonth() - 1, 1); drawCalendar(); });
  next.addEventListener("click", () => { sched.month = new Date(m.getFullYear(), m.getMonth() + 1, 1); drawCalendar(); });
  const head = el("div", { class: "cal-head" }, prev, el("strong", { text: m.toLocaleDateString(undefined, { month: "long", year: "numeric" }) }), next);
  const grid = el("div", { class: "cal-grid", role: "grid" }, ...["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => el("span", { class: "cal-dow", text: d })));
  const first = new Date(m);
  first.setDate(1 - ((m.getDay() + 6) % 7));
  for (let i = 0; i < 42; i++) {
    const d = new Date(first.getFullYear(), first.getMonth(), first.getDate() + i);
    const items = byDay.get(iso(d)) || [];
    const cell = el("div", { class: `cal-day${d.getMonth() !== m.getMonth() ? " out" : ""}${iso(d) === iso(today) ? " today" : ""}`, role: "gridcell" },
      el("span", { class: "cal-n", text: String(d.getDate()) }),
      ...items.slice(0, 3).map((e) => el("span", { class: `cal-ev ${e.category}${e.done ? " done" : ""}`, title: e.title + (e.notes ? `\n${e.notes}` : ""), text: e.title })),
      items.length > 3 ? el("span", { class: "cal-more", text: `+${items.length - 3} more` }) : "");
    grid.append(cell);
    if (i >= 34 && d.getMonth() !== m.getMonth() && d.getDay() === 0) break;
  }
  box.replaceChildren(head, grid);
}

async function scheduleCall(path, body, method) {
  try {
    return await api(path, body || undefined, 15000, { method });
  } catch (e) {
    addBubble("ai", { spoken: errorText(e) });
    return null;
  }
}

// Two ways in: this form (add, or Edit an item), or just mention a date in any chat
// ("my DBMS exam is on 14 Nov") and AIRTAC adds it, with an Undo button under the answer.
let editingId = null;
const EV_NOTE = "Add anything here, or just mention a date in a chat and AIRTAC adds it. Tap Edit to change an item.";

function startEdit(e) {
  editingId = e.id;
  $("ev-title").value = e.title;
  $("ev-date").value = e.date;
  $("ev-time").value = e.time || "";
  $("ev-cat").value = e.category;
  $("ev-repeat").value = e.repeat || "";
  if (e.first_date) $("ev-date").value = e.first_date;     // a repeating item is edited on its first date
  $("ev-save").textContent = "Save changes";
  $("ev-cancel").hidden = false;
  $("ev-note").textContent = `Editing "${e.title}". Change anything, then Save changes.`;
  $("event-form").scrollIntoView({ behavior: "smooth", block: "center" });
  $("ev-title").focus();
  loadSchedule();
}

function stopEdit() {
  editingId = null;
  $("event-form").reset();
  $("ev-save").textContent = "Add";
  $("ev-cancel").hidden = true;
  $("ev-note").textContent = EV_NOTE;
}

async function addEvent(event) {
  event.preventDefault();
  const body = { title: $("ev-title").value.trim(), date: $("ev-date").value, time: $("ev-time").value,
    category: $("ev-cat").value, repeat: $("ev-repeat").value };
  if (!body.title || !body.date) return;
  const saved = editingId
    ? await scheduleCall(`/api/plugins/schedule/${editingId}`, body, "POST")
    : await scheduleCall("/api/plugins/schedule", body, "POST");
  if (saved) {
    stopEdit();
    loadApp("schedule", false);
  }
}

async function updateEvent(id, done) {
  if (await scheduleCall(`/api/plugins/schedule/${id}`, { done }, "POST")) loadApp("schedule", false);
}

// Press the cross twice to delete (browser pop-ups are blocked in some views).
let deleteArmed = null;
async function removeEvent(e) {
  if (deleteArmed !== e.id) {
    deleteArmed = e.id;
    $("ev-note").textContent = `Press the cross again to delete "${e.title}".`;
    setTimeout(() => { if (deleteArmed === e.id) { deleteArmed = null; $("ev-note").textContent = EV_NOTE; } }, 4000);
    return;
  }
  deleteArmed = null;
  if (editingId === e.id) stopEdit();
  if (await scheduleCall(`/api/plugins/schedule/${e.id}`, null, "DELETE")) loadApp("schedule", false);
}

async function addPlan(t) {
  const data = await scheduleCall("/api/plugins/schedule/template", { name: t.name }, "POST");
  if (data) {
    addBubble("ai", { spoken: data.added ? `Added ${data.added} items from ${t.title}.`
      : `${t.title} is already on your schedule.` });
    loadApp("schedule", false);
  }
}

async function drawTemplates() {
  let list = [];
  try { list = await api("/api/plugins/schedule/templates", undefined, 10000); } catch { /* no plans to offer */ }
  const box = $("ev-templates");
  box.replaceChildren(...list.map((t) => chip(`${t.title} (${t.count})`, () => addPlan(t))));
}

// ---------------------------------------------------------------- wire up

$("install-app").addEventListener("click", installApp);
syncInstall();
$("conn-sheet").addEventListener("click", (event) => { if (event.target === event.currentTarget) closeConnection(); });
$("conn-sheet").addEventListener("close", () => { conn.open = null; });
$("ask").addEventListener("submit", (event) => {
  event.preventDefault();
  send($("text").value);
});
$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendApp($("chat-text").value);
});
$("event-form").addEventListener("submit", addEvent);
$("rs-body").addEventListener("input", saveSoon);
$("rs-title").addEventListener("input", saveSoon);
$("rs-notes").addEventListener("change", () => openNote(Number($("rs-notes").value)));
$("rs-new").addEventListener("click", async () => {
  await saveNow();
  try { const n = await api("/api/plugins/research/notes", { title: "New notes", body: "" }, 15000); await loadResearch(n.id); $("rs-title").select(); }
  catch (e) { $("rs-saved").textContent = errorText(e); }
});
$("rs-focus").addEventListener("click", () => setFocusWrite(!document.body.classList.contains("focus-write")));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("focus-write")) setFocusWrite(false); });

$("rs-delete").addEventListener("click", async () => {
  if (!rsNote) return;
  if ($("rs-delete").dataset.armed !== "1") {
    $("rs-delete").dataset.armed = "1"; $("rs-delete").textContent = "Press again to delete";
    setTimeout(() => { $("rs-delete").dataset.armed = ""; $("rs-delete").textContent = "Delete"; }, 4000);
    return;
  }
  $("rs-delete").dataset.armed = ""; $("rs-delete").textContent = "Delete";
  try { await api(`/api/plugins/research/notes/${rsNote.id}`, undefined, 15000, { method: "DELETE" }); }
  catch (e) { $("rs-saved").textContent = errorText(e); return; }
  rsNote = null;
  loadResearch();
});
$("rs-tab-write").addEventListener("click", () => setPreview(false));
$("rs-tab-preview").addEventListener("click", () => setPreview(true));
for (const b of document.querySelectorAll("[data-act]")) b.addEventListener("click", () => copilot(b.dataset.act, b.dataset.act === "sources" ? $("rs-q").value.trim() : ""));
$("rs-ask").addEventListener("submit", (event) => { event.preventDefault(); const q = $("rs-q").value.trim(); if (q) copilot("ask", q); });
window.addEventListener("beforeunload", () => { if (rsNote) saveNow(); });
$("ev-cancel").addEventListener("click", () => { stopEdit(); loadSchedule(); });
$("listen").addEventListener("click", () => speak(lastSpoken, true));
$("start-airtac").addEventListener("click", startAirtac);
$("today-listen").addEventListener("click", toggleDay);
// ---------------------------------------------------------------- Knowledge: add files to your library

async function uploadFiles(files) {
  const note = $("lib-note");
  for (const f of files) {
    if (f.size > 60e6) { note.textContent = `${f.name} is bigger than 60 MB.`; continue; }
    note.textContent = `Reading ${f.name}...`;
    try {
      const data = await fileAsBase64(f);
      const r = await api("/api/plugins/knowledge/upload", { name: f.name, shelf: $("lib-shelf").value, data_base64: data }, 300000);
      note.textContent = `Added ${r.path}: ${r.passages} passages.`;
    } catch (e) {
      note.textContent = `${f.name}: ${errorText(e)}`;
    }
  }
  if (currentApp === "knowledge") loadApp("knowledge", false);
}

// Research's sub-brain: a collection (shelf) of the Knowledge library, e.g. a GitHub repository.
async function loadBrains() {
  const pick = $("rs-brain");
  const keep = pick.value || load("airtac-rs-brain") || "";
  try {
    const r = await api("/api/plugins/knowledge/collections", undefined, 10000);
    pick.replaceChildren(el("option", { value: "", text: "All your knowledge" }),
      ...r.collections.map((c) => el("option", { value: c.id, text: `${c.id} (${c.docs} file${c.docs === 1 ? "" : "s"})` })));
    pick.value = r.collections.some((c) => c.id === keep) ? keep : "";
  } catch { /* the list stays as it was */ }
}

async function addRepo(event) {
  event.preventDefault();
  const note = $("repo-note");
  const repo = $("repo-name").value.trim();
  if (!repo) { $("repo-name").focus(); return; }
  try {
    await api("/api/plugins/knowledge/github", { repo, shelf: $("repo-shelf").value.trim().toLowerCase() }, 30000);
    for (let n = 0; n < 900; n++) {                // indexing a big tutorial takes minutes on a laptop
      const s = await api("/api/plugins/knowledge/github", undefined, 15000);
      note.textContent = s.error || s.last || "Working...";
      if (!s.busy) break;
      await sleep(2000);
    }
  } catch (e) { note.textContent = errorText(e); }
  if (currentApp === "knowledge") loadApp("knowledge", false);
}

// Search the library: a short answer quoted from your files with [n] marks, then each passage with file and page.
async function searchLibrary(event) {
  event.preventDefault();
  const q = $("lib-q").value.trim();
  const box = $("lib-results");
  if (!q) return;
  box.replaceChildren(el("p", { class: "muted", text: "Searching..." }));
  try {
    const r = await api(`/api/plugins/knowledge/search?q=${encodeURIComponent(q)}`, undefined, 60000);
    if (!r.results.length) { box.replaceChildren(el("p", { class: "muted", text: "Nothing in your library matches. Add files below." })); return; }
    const cite = (s) => `${s.file}${s.page ? `, p. ${s.page}` : ""}`;
    const answer = r.answer.text ? [el("p", { class: "lib-answer", text: r.answer.text }),
      el("ol", { class: "lib-cites" }, ...r.answer.sources.map((s) => el("li", { text: cite(s) })))] : [];
    box.replaceChildren(...answer, ...r.results.map((c) => el("article", { class: "lib-hit" },
      el("p", { class: "small muted", text: `${c.title} · ${cite(c)} · ${c.shelf || "main"} · score ${c.score}` }),
      el("p", { class: "small" }, ...highlight(c.text.slice(0, 500), q)))));
  } catch (e) { box.replaceChildren(el("p", { class: "muted", text: errorText(e) })); }
}

async function reindexLibrary() {
  $("lib-note").textContent = "Reading the knowledge folder...";
  try {
    const r = await api("/api/plugins/knowledge/reindex", {}, 600000);
    $("lib-note").textContent = r.busy ? "Already reading. Try again in a minute."
      : `Read ${r.added} new or changed file${r.added === 1 ? "" : "s"}` + (r.failed.length ? `. Couldn't read: ${r.failed.join(", ")}` : ".");
  } catch (e) {
    $("lib-note").textContent = errorText(e);
  }
  if (currentApp === "knowledge") loadApp("knowledge", false);
}

// ---------------------------------------------------------------- Coach: lessons you can switch on and off

function drawLessons(lessons) {
  const list = $("lessons-list");
  list.replaceChildren();
  if (!lessons.length) list.append(el("li", { class: "muted", text: "No lessons yet. Press thumbs-down on a wrong answer and say what was right." }));
  for (const x of lessons) {
    const toggle = el("input", { type: "checkbox", "aria-label": "Use this lesson" });
    toggle.checked = Boolean(x.active);
    toggle.addEventListener("change", async () => {
      try { await api(`/api/lessons/${x.id}?active=${toggle.checked}`, {}, 10000); } catch (e) { toggle.checked = !toggle.checked; }
    });
    const del = el("button", { type: "button", class: "icon-btn small", "aria-label": "Delete this lesson" }, icon("close"));
    del.addEventListener("click", async () => {
      try { await api(`/api/lessons/${x.id}`, undefined, 15000, { method: "DELETE" }); li.remove(); }
      catch (e) { toast({ title: "Couldn't delete that lesson", body: errorText(e) }); }
    });
    const li = el("li", { class: x.active ? "" : "off" }, el("label", { class: "switch" }, toggle),
      el("span", {}, el("span", { text: x.text }), el("small", { text: `${x.source === "review" ? "Suggested by the weekly review" : "From your correction"}, used ${x.uses} time${x.uses === 1 ? "" : "s"}` })),
      del);
    list.append(li);
  }
}

// ---------------------------------------------------------------- Brains and plugins

const STATE_TEXT = { on: "On", off: "Off", planned: "Planned", new: "New: restart to load", "needs-packages": "Needs packages",
  "needs-plugins": "Needs another plugin", unsupported: "Not on this computer", error: "Failed to load" };

// Models on this PC: size, memory each needs, and which role (General, Coding, ...) uses it.
async function loadModels() {
  let d;
  try { d = await api("/api/brains/models", undefined, 15000); }
  catch (e) { $("models-note").textContent = errorText(e); return; }
  $("models-note").textContent = !d.ollama_up ? "Ollama isn't running, so the downloaded models can't be listed. Open Ollama, then reload."
    : `${d.models.length} downloaded.` + (d.ram_gb ? ` This PC has ${d.ram_gb} GB of memory; one model is loaded at a time.` : "");
  $("models").replaceChildren(...d.models.map((m) => el("div", { class: `model-row${m.fits ? "" : " too-big"}` },
    el("code", { text: m.name }), el("span", { class: "muted small", text: `${m.params || ""} ${m.family || ""}`.trim() }),
    el("span", { text: `${m.size_gb} GB` }), el("span", { class: "muted small", text: `needs ~${m.ram_gb} GB` + (m.fits ? "" : " (too big for this PC)") }),
    el("span", { class: "small", text: m.roles.length ? `Used for: ${m.roles.join(", ")}` : "" }))));
  const names = d.models.map((m) => m.name);
  $("model-roles").replaceChildren(...d.roles.map((r) => {
    const pick = el("select", { class: "field", "aria-label": `Model for ${r.label}` },
      ...(r.role === "general" ? [] : [el("option", { value: "", text: r.role === "embed" ? "Off (keyword search)" : "Same as General" })]),
      ...names.map((n) => el("option", { value: n, text: n })));
    const current = names.find((n) => n === r.model || n === `${r.model}:latest`);
    pick.value = current || (r.role === "general" ? r.model : "");
    if (r.model && !current && r.role !== "embed") pick.append(el("option", { value: r.model, text: `${r.model} (not downloaded)` }));
    pick.addEventListener("change", async () => {
      try { await api("/api/brains/models", { role: r.role, model: pick.value }, 20000); toast({ title: `${r.label}: ${pick.value || "default"}`, body: "Saved. Used from the next question." }); loadPlugins(); }
      catch (e) { toast({ title: errorText(e), body: "" }); loadModels(); }
    });
    return el("label", { class: "setting" }, r.label, pick);
  }));
}

async function makePlugin(event) {
  event.preventDefault();
  const f = $("plugin-form");
  const body = { id: f.elements.id.value.trim(), name: f.elements.name.value.trim(), about: f.elements.about.value.trim(),
    risk: f.elements.risk.value, websites: f.elements.websites.value.split(",").map((x) => x.trim()).filter(Boolean) };
  try {
    const r = await api("/api/plugins/new", body, 20000);
    $("plugin-made").textContent = `Made ${r.made}. Edit ${r.files[1]}, then restart AIRTAC to load it.`;
    loadPlugins();
  } catch (e) { $("plugin-made").textContent = errorText(e); }
}

async function loadCcPlugins() {
  let d;
  try { d = await api("/api/plugins/ccplugins", undefined, 10000); } catch { return; }
  $("ccplugins").replaceChildren(...d.plugins.map((c) => {
    const box = el("input", { type: "checkbox", "aria-label": `Use ${c.name}` });
    box.checked = c.on;
    box.addEventListener("change", () => api("/api/plugins/ccplugins", { id: c.id, on: box.checked }, 10000).catch((e) => toast({ title: errorText(e) })));
    return el("div", { class: "plugin-card" },
      el("div", { class: "row" }, el("strong", { text: c.name }), el("span", { class: "spacer" }), el("label", { class: "switch" }, box, c.on ? " on" : " off")),
      el("p", { class: "small", text: c.does }),
      el("p", { class: "muted small", text: `May reach: ${c.domains.length ? c.domains.join(", ") : "nothing outside your PC"}${c.key ? " · key on APIs and keys" : ""}` }),
      el("code", { class: "small", text: c.how }),
      el("a", { href: c.source, target: "_blank", rel: "noopener noreferrer", class: "small", text: c.official ? "Anthropic's official plugins" : c.source.replace("https://github.com/", "github: ") }));
  }));
}

async function loadPlugins() {
  loadModels();
  loadCcPlugins();
  const [brains, plugins] = await Promise.all([
    api("/api/brains", undefined, 15000).catch(() => null), api("/api/plugins", undefined, 15000).catch(() => [])]);
  const bbox = $("brains");
  bbox.replaceChildren();
  if (brains) {
    if (brains.provider === "ollama" && brains.ollama_up === false) {
      bbox.append(el("p", { class: "warn-note", text: "Ollama isn't running. Open it from the Start menu, then reload this page." }));
    }
    const table = el("div", { class: "brain-table" });
    for (const r of brains.roles) {
      const state = r.installed === true ? "ready" : r.installed === false ? "not downloaded" : "";
      table.append(el("div", { class: `brain-row ${r.installed === false ? "missing" : ""}` },
        el("strong", { text: r.label }), el("code", { text: r.model }),
        el("span", { class: "muted", text: state + (r.tools ? "" : state ? ", answers only" : "answers only") })));
    }
    bbox.append(table);
    if (brains.tiers) {
      bbox.append(el("h3", { class: "tier-title", text: "Cloud brains, in order" }),
        el("p", { class: "muted small", text: "Used only when its key is saved (APIs and keys). One that runs out of free requests rests, and the next answers. With local_first on (config), your PC answers first and these step in when it can't." }));
      const tiers = el("div", { class: "brain-table" });
      for (const t of brains.tiers) {
        const state = t.tier === "ollama" ? "always on" : !t.key ? "off (add its key on APIs and keys)"
          : t.resting_seconds ? `resting ${Math.ceil(t.resting_seconds / 60)} min` : "ready";
        tiers.append(el("div", { class: `brain-row ${!t.key ? "missing" : ""}` }, el("strong", { text: t.label }), el("code", { text: t.model }),
          el("span", { class: "muted", text: `${state}${t.answered ? `, answered ${t.answered}` : ""}${t.trains_on_data ? ". Free tier may train on your data." : ""}` })));
      }
      bbox.append(tiers);
    }
    const cat = el("details", { class: "details" }, el("summary", { text: "Free models that fit an 8 GB laptop" }));
    for (const c of brains.catalog) {
      cat.append(el("p", {}, el("code", { text: `ollama pull ${c.model}` }), el("br"),
        el("small", { text: `${c.role}, ${c.size}, ${c.licence}. ${c.note}` })));
    }
    bbox.append(cat);
  }
  const grid = $("plugins");
  grid.replaceChildren();
  for (const p of plugins) {
    const net = (p.permissions.network || []);
    const perms = [net.length ? (net.includes("*") ? "Any public website" : `Websites: ${net.join(", ")}`) : "No internet",
      `Can ${{ read: "only read", local: "work on this PC", write: "change things (asks first)", device: "control this PC (asks first)" }[p.permissions.risk] || "read"}`];
    const card = el("article", { class: `plugin ${p.state}` },
      el("div", { class: "plugin-head" }, icon(p.icon), el("strong", { text: p.name }),
        el("span", { class: `pill ${p.state}`, text: STATE_TEXT[p.state] || p.state })),
      el("p", { text: p.about }),
      el("p", { class: "muted small", text: perms.join(" · ") }));
    if (p.tools.length) card.append(el("p", { class: "muted small", text: `Tools: ${p.tools.join(", ")}` }));
    if (p.reason) card.append(el("p", { class: "plugin-reason", text: p.reason }));
    if (p.state !== "planned" && p.state !== "new" && !["settings", "finder", "attachments"].includes(p.id)) {
      const sw = el("input", { type: "checkbox", role: "switch", "aria-label": `${p.name} on` });
      sw.checked = !p.switched_off;
      sw.addEventListener("change", async () => {
        try {
          await api(`/api/plugins/${encodeURIComponent(p.id)}/switch`, { on: sw.checked }, 15000);
          toast({ title: `${p.name} ${sw.checked ? "on" : "off"} after a restart`, body: "Saved in config\\airtac.toml." });
        } catch (e) { sw.checked = !sw.checked; toast({ title: errorText(e), body: "" }); }
      });
      card.append(el("label", { class: "plugin-switch" }, sw, el("span", { text: "On" })));
    }
    if (p.state === "planned" && p.plan && p.plan.steps) {
      card.append(el("details", {}, el("summary", { text: "The plan" }), el("ul", {}, ...p.plan.steps.map((s) => el("li", { text: s })))));
    }
    grid.append(card);
  }
}

// ---------------------------------------------------------------- Connections (airtac/connections.py)
// A compact board of tiles, side by side; tap one to open its sheet (key, Save, Test, Disconnect).

const CONN_STATUS = { connected: "Connected", saved: "Saved, not tested", off: "Not set up", error: "Needs a look", info: "Coming with Google sign-in" };
const SOURCE_TEXT = { app: "saved on this PC", env: "from your .env file" };
const CONN_HUES = ["#5aa9e6", "#7fc8a9", "#e6b04a", "#c38fff", "#ef7d7d", "#6cc4c4", "#f29e6d", "#a3b8ff"];
const CONN_MONO = { groq: "Gq", nebius: "Nb", nvidia: "Nv", openrouter: "OR", gemini: "Gm", tavily: "Tv", github: "GH",
  ollama: "Ol", phone: "Ph", huggingface: "HF", mistral: "Mi", deepseek: "DS", anthropic: "Cl", openai: "OA", xai: "xA",
  perplexity: "Px" };
const conn = { list: [], here: true, filter: "All", open: null, q: "" };

function connLabel(c) { return !c.fields.length && c.status === "saved" ? "Not checked yet" : CONN_STATUS[c.status]; }
function connShort(name) { return name.replace(/\s*\(.*\)\s*$/, ""); }
function connHue(id) { let h = 0; for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return CONN_HUES[h % CONN_HUES.length]; }

function connMono(c) {
  if (c.logo) return el("span", { class: "conn-mono has-logo", "aria-hidden": "true" }, el("img", { src: `./${c.logo}`, alt: "", loading: "lazy" }));
  const mono = el("span", { class: "conn-mono", "aria-hidden": "true", text: CONN_MONO[c.id] || c.name.slice(0, 2) });
  mono.style.setProperty("--hue", connHue(c.id));
  return mono;
}

async function loadConnections() {
  // The vault gate first (create or unlock), then the cards. Locked = nothing on the page but the gate.
  $("conn-list").replaceChildren();
  $("conn-leds").replaceChildren();
  $("conn-count").textContent = "";
  $("conn-filter").replaceChildren();
  $("conn-lock").hidden = true;
  await vaultGate($("conn-gate"), async () => { $("conn-gate").replaceChildren(); await loadConnectionCards(); }, "keys");
}

async function loadConnectionCards() {
  let data;
  try { data = await api("/api/connections", undefined, 15000); }
  catch (e) {
    if (e.kind === "locked") { vault.open = false; return loadConnections(); }
    $("conn-note").textContent = errorText(e); $("conn-note").hidden = false; return;
  }
  $("conn-lock").hidden = !data.store.unlocked;
  conn.list = data.connections;
  conn.here = data.here;
  const notes = [];
  if (!data.here) notes.push("You're on another device: you can see what's connected, but keys can only be changed on the PC itself.");
  if (!data.store.vault) notes.push(`${data.store.detail} Keys you save go into the .env file in the AIRTAC folder instead.`);
  $("conn-note").textContent = notes.join(" ");
  $("conn-note").hidden = !notes.length;
  drawConnections();
}

function drawConnections() {
  const list = conn.list;
  const on = list.filter((c) => c.status === "connected").length;
  const bad = list.filter((c) => c.status === "error").length;
  $("conn-leds").replaceChildren(...list.map((c) => el("i", { class: `led ${c.status}`, title: `${connShort(c.name)}: ${connLabel(c)}` })));
  $("conn-count").textContent = `${on} of ${list.length} connected${bad ? `, ${bad} need${bad === 1 ? "s" : ""} a look` : ""}`;
  const groups = ["All", ...new Set(list.map((c) => c.group))];
  $("conn-filter").replaceChildren(...groups.map((g) => {
    const b = el("button", { type: "button", class: "chip-btn", role: "tab", "aria-selected": String(conn.filter === g), text: g });
    b.addEventListener("click", () => { conn.filter = g; drawConnections(); });
    return b;
  }));
  const words = conn.q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (conn.filter === "All" ? list : list.filter((c) => c.group === conn.filter))
    .filter((c) => words.every((w) => `${c.name} ${c.about} ${c.group} ${c.fields.map((f) => f.key).join(" ")}`.toLowerCase().includes(w)));
  $("conn-list").replaceChildren(...shown.map(connTile));
  if (!shown.length) $("conn-list").append(el("p", { class: "muted", text: "Nothing matches. Add it as a custom key." }));
}

function connTile(c) {
  const tile = el("button", { type: "button", class: `conn-tile ${c.status}`, "aria-haspopup": "dialog",
    "aria-label": `${c.name}: ${connLabel(c)}` },
    connMono(c),
    el("span", { class: "conn-tile-text" },
      el("strong", { text: connShort(c.name) }),
      el("small", {}, el("i", { class: `led ${c.status}` }), connLabel(c)),
      c.test ? el("small", { class: "muted", text: `Tested ${c.test.at}` }) : ""));
  tile.addEventListener("click", () => openConnection(c.id));
  return tile;
}

function openConnection(id) {
  conn.open = id;
  const c = conn.list.find((x) => x.id === id);
  if (!c) return;
  $("conn-sheet-body").replaceChildren(...connSheet(c));
  const dialog = $("conn-sheet");
  if (!dialog.open) { if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", ""); }
}

function closeConnection() {
  const dialog = $("conn-sheet");
  if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open");
  conn.open = null;
}

function connSheet(c) {
  const away = !conn.here;
  const inputs = {};
  const form = el("form", { class: "conn-form", autocomplete: "off" });
  for (const f of c.fields) {
    const input = f.multiline
      ? el("textarea", { class: "field mono", rows: "4", spellcheck: "false", "aria-label": f.label, autocomplete: "off",
        placeholder: f.set ? "Saved. Paste a new one to replace it." : f.placeholder })
      : el("input", { type: f.secret ? "password" : "text", class: "field", autocomplete: f.secret ? "new-password" : "off",
        spellcheck: "false", "aria-label": f.label,
        placeholder: f.set ? "Saved. Paste a new one to replace it." : f.placeholder });
    if (f.set && !f.secret) input.value = f.shown;   // a region or project URL isn't secret: show it to edit
    input.disabled = away;
    inputs[f.key] = input;
    const label = el("label", { class: "conn-field" },
      el("span", { text: f.label + (f.set ? ` (${SOURCE_TEXT[f.source] || "set"})` : "") }), input);
    form.append(label);
    if (f.set && f.secret && !away) {
      // Gmail-style: dots, Show for 10 seconds, Copy (the PC's clipboard, cleared after 20 seconds).
      const masked = el("code", { class: "api-key", text: f.shown || "••••••••" });
      const show = showFor(masked, () => api(`/api/connections/${c.id}/reveal`, { key: f.key }, 10000), f.shown || "••••••••");
      const copy = button("Copy", "copy", async () => {
        try {
          const r = await api(`/api/connections/${c.id}/copy`, { key: f.key }, 10000);
          toast({ title: `${f.label} copied`, body: `Cleared from the clipboard in ${r.clears_in} seconds.` });
        } catch (e) {
          if (!/clipboard/.test(e.message)) return toast({ title: errorText(e) });
          try {   // this PC's clipboard isn't reachable (not Windows): the page copies it once
            const r = await api(`/api/connections/${c.id}/reveal`, { key: f.key }, 10000);
            await navigator.clipboard.writeText(r.value);
            toast({ title: `${f.label} copied` });
          } catch (e2) { toast({ title: errorText(e2) }); }
        }
      });
      form.append(el("div", { class: "conn-secret" }, masked, show, copy));
    }
  }
  const save = el("button", { type: "submit", class: "primary", text: "Save" });
  const test = el("button", { type: "button", class: "quiet", text: c.fields.length ? "Test" : "Check" });
  const off = el("button", { type: "button", class: "quiet", text: "Disconnect" });
  save.hidden = !c.fields.length;
  test.hidden = !c.can_test;
  off.hidden = !c.fields.length || c.status === "off";
  for (const b of [save, test, off]) b.disabled = away;
  const msg = el("p", { class: "conn-msg", role: "status", text: c.test ? `${c.test.message} (${c.test.at})` : "" });
  if (c.test) msg.classList.add(c.test.ok ? "ok" : "bad");
  const act = async (path, body, busyText) => {
    msg.className = "conn-msg";
    msg.textContent = busyText;
    for (const b of [save, test, off]) b.disabled = true;
    try {
      const r = await api(`/api/connections/${c.id}${path}`, body, 30000);
      const updated = r.connection || r;
      conn.list = conn.list.map((x) => (x.id === updated.id ? updated : x));
      drawConnections();
      if (conn.open === c.id) openConnection(c.id);
      if (r.still_in_env && r.still_in_env.length) {
        const m = $("conn-sheet-body").querySelector(".conn-msg");
        m.textContent = `Removed here, but ${r.still_in_env.join(", ")} is also a Windows environment variable. Remove it in Settings, Environment variables.`;
        m.className = "conn-msg bad";
      }
      wakeServer();
    } catch (e) {
      msg.textContent = errorText(e);
      msg.classList.add("bad");
      for (const b of [save, test, off]) b.disabled = away;
    }
  };
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = {};
    for (const [k, input] of Object.entries(inputs)) values[k] = input.value.trim();
    act("", { values }, "Saving...");
  });
  test.addEventListener("click", () => act("/test", {}, c.fields.length ? "Testing..." : "Checking..."));
  off.addEventListener("click", () => act("/disconnect", {}, "Disconnecting..."));
  form.append(el("div", { class: "conn-actions" }, save, test, off));

  const close = el("button", { type: "button", class: "icon-btn", "aria-label": "Close" }, icon("close"));
  close.addEventListener("click", closeConnection);
  const parts = [
    el("div", { class: "conn-sheet-head" }, connMono(c),
      el("div", {}, el("h2", { id: "conn-sheet-title", text: c.name }),
        el("p", { class: `conn-state ${c.status}` }, el("i", { class: `led ${c.status}` }), connLabel(c))),
      close),
    el("p", { class: "muted", text: c.about }),
  ];
  if (c.powers.length) parts.push(el("p", { class: "conn-powers", text: `Switches on: ${c.powers.join(", ")}` }));
  if (c.note) parts.push(el("p", { class: "conn-note", text: c.note }));
  parts.push(form, msg);
  if (c.get_key) parts.push(el("a", { href: c.get_key, target: "_blank", rel: "noopener noreferrer", class: "conn-link" },
    c.custom ? "Open the service" : c.fields.length ? "Get a key" : "Download", icon("external")));
  if (c.docs) parts.push(el("a", { href: c.docs, target: "_blank", rel: "noopener noreferrer", class: "conn-link" }, "What Test calls", icon("external")));
  if (c.custom && !away) {
    parts.push(el("div", { class: "conn-actions" },
      button("Edit", "edit", () => customKeyForm(c.id)),
      button("Delete", "close", async (ev) => {
        const b = ev.currentTarget;
        if (!b.dataset.armed) { b.dataset.armed = "1"; b.lastChild.textContent = "Press again to delete"; return; }
        try { await api(`/api/plugins/apikeys/custom/${c.id}/delete`, {}, 15000); closeConnection(); loadConnections(); }
        catch (e) { toast({ title: errorText(e) }); }
      })));
  }
  return parts;
}

// "+ Add custom API key": a name, the service's address, up to 4 fields, notes, a logo and an optional Test address.
async function customKeyForm(id = "") {
  let d = { name: "", url: "", test_url: "", header: "Authorization", bearer: true, notes: "", fields: [{ label: "API key", secret: true }] };
  if (id) {
    try { d = (await api("/api/plugins/apikeys/custom", undefined, 10000)).custom.find((x) => x.id === id) || d; }
    catch (e) { return toast({ title: errorText(e) }); }
  }
  const f = (name, value, attrs = {}) => el("input", { class: "field", name, value: value || "", ...attrs });
  const name = f("name", d.name, { maxlength: "60", required: "", placeholder: "e.g. Hackathon judge API" });
  const url = f("url", d.url, { maxlength: "300", placeholder: "https://... (the service's site)" });
  const testUrl = f("test_url", d.test_url, { maxlength: "300", placeholder: "https://... answers 200 with the key (optional)" });
  const header = f("header", d.header, { maxlength: "40", placeholder: "Authorization" });
  const bearer = el("input", { type: "checkbox" });
  bearer.checked = d.bearer !== false;
  const notes = el("textarea", { class: "field", maxlength: "500", rows: "2", placeholder: "Notes (optional)" });
  notes.value = d.notes || "";
  const rows = el("div", { class: "custom-fields" });
  const addRow = (x = { label: "", secret: true }) => {
    if (rows.children.length >= 4) return;
    const label = el("input", { class: "field", maxlength: "40", value: x.label, placeholder: "Field name, e.g. Secret" });
    const secret = el("input", { type: "checkbox" });
    secret.checked = x.secret !== false;
    const rm = button("", "close", () => row.remove());
    rm.setAttribute("aria-label", "Remove this field");
    const row = el("div", { class: "row" }, label, el("label", { class: "switch" }, secret, " secret"), rm);
    rows.append(row);
  };
  for (const x of d.fields) addRow(x);
  const logo = el("input", { type: "file", accept: "image/png,image/jpeg,image/webp", "aria-label": "Logo (PNG, JPEG or WebP)" });
  const msg = el("p", { class: "conn-msg bad", role: "status" });
  const form = el("form", { class: "conn-form", autocomplete: "off" },
    el("h2", { text: id ? `Edit ${d.name}` : "Add a custom API key" }),
    el("label", { class: "conn-field" }, el("span", { text: "Name" }), name),
    el("label", { class: "conn-field" }, el("span", { text: "Service address" }), url),
    el("div", { class: "conn-field" }, el("span", { text: "Fields (the values go in after you save)" }), rows,
      button("Add a field", "plus", () => addRow())),
    el("label", { class: "conn-field" }, el("span", { text: "Test address" }), testUrl),
    el("div", { class: "row" }, el("label", { class: "conn-field" }, el("span", { text: "Header for the key" }), header),
      el("label", { class: "switch" }, bearer, ' add "Bearer "')),
    el("label", { class: "conn-field" }, el("span", { text: "Logo (optional; letters if none)" }), logo),
    notes, msg,
    el("div", { class: "conn-actions" }, button("Cancel", "close", closeConnection), el("button", { type: "submit", class: "primary", text: "Save" })));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { name: name.value, url: url.value, test_url: testUrl.value, header: header.value, bearer: bearer.checked, notes: notes.value,
      fields: [...rows.children].map((r) => ({ label: r.querySelector(".field").value, secret: r.querySelector("input[type=checkbox]").checked })) };
    try {
      const saved = await api(id ? `/api/plugins/apikeys/custom/${id}` : "/api/plugins/apikeys/custom", body, 15000);
      if (logo.files[0]) {
        await api(`/api/plugins/apikeys/custom/${saved.id}/logo`, logo.files[0], 30000);
      }
      await loadConnections();
      openConnection(saved.id);
    } catch (err) { msg.textContent = errorText(err); }
  });
  $("conn-sheet-body").replaceChildren(form);
  const dialog = $("conn-sheet");
  if (!dialog.open) dialog.showModal();
  name.focus();
}

// ---------------------------------------------------------------- install as an app (PC and phone)
// Chrome and Edge offer an install prompt; Safari on iPhone installs from the Share menu. Phones only
// install from an https address (AIRTAC through Tailscale, see docs/PHONE_AND_PC.md); this PC's localhost is fine.

let installEvent = null;

function isStandalone() { return matchMedia("(display-mode: standalone)").matches || navigator.standalone === true; }
function isIOS() { return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1); }

function syncInstall() { $("install-app").hidden = isStandalone(); }

async function installApp() {
  setDrawer(false);
  if (installEvent) {
    installEvent.prompt();
    const choice = await installEvent.userChoice.catch(() => null);
    installEvent = null;
    if (choice && choice.outcome === "accepted") return;
  }
  let steps;
  if (!window.isSecureContext) {
    steps = "Phones only install apps from an https address. Open AIRTAC through Tailscale (https://your-pc-name.ts.net, see docs/PHONE_AND_PC.md), then choose Install app again.";
  } else if (isIOS()) {
    steps = "In Safari, tap the Share button, then Add to Home Screen.";
  } else if (isPhone()) {
    steps = "In Chrome, open the menu (three dots), then tap Install app or Add to Home screen.";
  } else {
    steps = "In Chrome or Edge, click the install icon at the right end of the address bar, or open the menu and choose Install AIRTAC (in Edge: Apps, then Install this site as an app).";
  }
  $("install-steps").textContent = steps;
  openDialog("install-dialog");
}

window.addEventListener("beforeinstallprompt", (event) => { event.preventDefault(); installEvent = event; syncInstall(); });
window.addEventListener("appinstalled", () => { installEvent = null; syncInstall(); toast({ title: "AIRTAC is installed", body: "Open it from your home screen or Start menu." }); });

// ---------------------------------------------------------------- notifications (reminders, the morning briefing, finished jobs)

let lastNote = -1;

function toast(n) {
  const item = el("li", { class: "toast" }, el("strong", { text: n.title }), n.body ? el("span", { text: n.body }) : "");
  const close = el("button", { type: "button", class: "icon-btn small", "aria-label": "Dismiss" }, icon("close"));
  close.addEventListener("click", () => item.remove());
  item.append(close);
  $("toasts").append(item);
  setTimeout(() => item.remove(), 12000);
}

async function pollNotifications() {
  try {
    const data = await api(`/api/notifications?after=${lastNote}`, undefined, 10000);
    const first = lastNote < 0;
    lastNote = data.last;
    if (!first) {
      for (const n of data.items) {
        toast(n);
        // The morning briefing isn't read twice if AIRTAC already read your day when it opened.
        const already = n.title === "Your day" && load("airtac-announced") === new Date().toISOString().slice(0, 10);
        if (n.spoken && !already) speak(n.spoken, true);
        if (n.plugin === "work" && currentView === "work") loadWork();
      }
    }
  } catch { /* try again next time */ }
  setTimeout(pollNotifications, document.hidden ? 60000 : 20000);
}

// ---------------------------------------------------------------- speech on this PC (faster-whisper and Piper)
// With [voice] stt = "local", the mic records here and the PC turns it into text: nothing leaves it.

let recorder = null;

async function localListen(target) {
  if (recorder) { recorder.stop(); return; }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    voiceNote("The microphone is blocked. Allow it in the browser, or type instead.");
    return;
  }
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (e) => chunks.push(e.data);
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    recorder = null;
    setLive("listening", false);
    $(target.mic).classList.remove("listening");
    voiceNote("Turning your words into text...");
    try {
      const res = await fetch(`${API}/api/voice/stt`, { method: "POST", body: new Blob(chunks, { type: "audio/webm" }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Speech recognition failed.");
      $(target.input).value = data.text;
      if (data.text.trim()) target.submit(data.text);
      voiceNote(HINT);
    } catch (e) {
      voiceNote(errorText(e));
    }
  };
  recorder.start();
  setLive("listening", true);
  $(target.mic).classList.add("listening");
  voiceNote("Listening... press the mic again when you're done.");
}

async function localSpeak(text) {
  try {
    const res = await fetch(`${API}/api/voice/tts`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 1500) }) });
    if (!res.ok) return false;
    const audio = new Audio(URL.createObjectURL(await res.blob()));
    setLive("speaking", true);
    audio.onended = () => setLive("speaking", false);
    await audio.play();
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- Web: search like a search engine, read like a reader

const PROVIDERS = { ddg: "DuckDuckGo", wikipedia: "Wikipedia", tavily: "Tavily", searxng: "your SearXNG", };
// "Search on ..." opens your default engine (Settings) in the browser, with the same words.
const ENGINE_URLS = {
  google: ["Google", "https://www.google.com/search?q="], bing: ["Bing", "https://www.bing.com/search?q="],
  ddg: ["DuckDuckGo", "https://duckduckgo.com/?q="], brave: ["Brave", "https://search.brave.com/search?q="],
  perplexity: ["Perplexity", "https://www.perplexity.ai/search?q="],
};
const webState = { engine: load("airtac-web-engine") || "all", engines: null };

async function drawEngines() {
  if (!webState.engines) {
    try { webState.engines = (await api("/api/plugins/web/engines", undefined, 10000)).engines; } catch { webState.engines = []; }
  }
  const all = [{ id: "all", name: "All results" }, ...webState.engines];
  if (!all.some((e) => e.id === webState.engine)) webState.engine = "all";
  $("web-engines").replaceChildren(...all.map((e) => {
    const b = el("button", { type: "button", class: "chip-btn", role: "tab", "aria-selected": String(e.id === webState.engine), text: e.name });
    b.addEventListener("click", () => { webState.engine = e.id; save("airtac-web-engine", e.id); drawEngines(); if ($("web-q").value.trim()) webSearch(); });
    return b;
  }));
}

function engineLink(q) {
  const [name, base] = ENGINE_URLS[hub.settings?.search_engine] || ENGINE_URLS.google;
  const a = $("web-engine-link");
  a.hidden = !q;
  a.href = base + encodeURIComponent(q);
  a.lastChild.textContent = `Search on ${name}`;
}
let webPage = null;  // the page open in the reader

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

function openWeb(query) {
  setHash("web");
  showView("web");
  if (query) webSearch(query);
}

async function webSearch(q) {
  q = (q ?? $("web-q").value).trim();
  if (!q) { $("web-q").focus(); return; }
  $("web-q").value = q;
  closeReader();
  const list = $("web-results");
  list.replaceChildren(el("li", { class: "muted", text: "Searching..." }));
  $("web-note").textContent = "";
  engineLink(q);
  drawEngines();
  try {
    const data = await api(`/api/plugins/web/search?q=${encodeURIComponent(q)}&provider=${encodeURIComponent(webState.engine)}&n=10`, undefined, 30000);
    list.replaceChildren(...data.results.map(webResult));
    const from = data.answered ? data.answered.join(", ") : PROVIDERS[data.provider] || data.provider;
    const missed = Object.entries(data.failed || {}).map(([k, v]) => `${k} ${v}`).join("; ");
    $("web-note").textContent = data.note
      || `${data.results.length} result${data.results.length === 1 ? "" : "s"} from ${from}` + (missed ? `. Not this time: ${missed}.` : "");
  } catch (e) {
    list.replaceChildren(el("li", { class: "muted", text: errorText(e) }));
  }
}

function webResult(r) {
  const host = hostOf(r.url);
  const read = el("button", { type: "button", class: "quiet small" }, icon("read"), "Read here");
  read.addEventListener("click", () => openReader(r.url));
  return el("li", { class: "web-result" },
    el("div", { class: "web-site" }, el("span", { class: "web-fav", text: host.slice(0, 1).toUpperCase() }),
      el("span", { text: host }), r.sources && r.sources.length > 1 ? el("span", { class: "web-src", text: r.sources.join(" · ") })
        : r.source ? el("span", { class: "web-src", text: r.source }) : ""),
    el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", class: "web-title", text: r.title }),
    el("p", { class: "web-snippet", text: r.snippet || "" }),
    el("div", { class: "web-actions" }, read,
      el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", class: "quiet small btn-link" }, icon("external"), "Open")));
}

async function openReader(url) {
  webPage = null;
  $("reader").hidden = false;
  $("reader-title").textContent = "Opening...";
  $("reader-src").textContent = hostOf(url);
  $("reader-src").href = SAFE_URL.test(url) ? url : "#";
  $("reader-text").replaceChildren();
  $("reader-answer").hidden = true;
  $("reader").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const page = await api(`/api/plugins/web/read?url=${encodeURIComponent(url)}`, undefined, 30000);
    webPage = page;
    $("reader-title").textContent = page.title || hostOf(page.url);
    $("reader-src").textContent = hostOf(page.url);
    $("reader-src").href = SAFE_URL.test(page.url) ? page.url : "#";
    const box = $("reader-text");
    for (const line of page.text.split("\n")) {
      if (line.startsWith("## ")) box.append(el("h4", { text: line.slice(3) }));
      else if (line.startsWith("- ")) box.append(el("p", { class: "bullet", text: line.slice(2) }));
      else box.append(el("p", { text: line }));
    }
    if (page.truncated) box.append(el("p", { class: "muted", text: "The page goes on. Open it for the rest." }));
  } catch (e) {
    $("reader-title").textContent = "Couldn't open this page";
    $("reader-text").replaceChildren(el("p", { class: "muted", text: errorText(e) }));
  }
}

function closeReader() {
  $("reader").hidden = true;
  webPage = null;
}

async function summarisePage() {
  if (!webPage || busy) return;
  const box = $("reader-answer");
  box.hidden = false;
  box.textContent = "Reading...";
  setBusy(true);
  try {
    const data = await api("/api/chat", { message: `Summarise this page: ${webPage.url}`, session_id: sessionId });
    box.replaceChildren(el("p", { text: data.spoken }));
    speak(data.spoken);
  } catch (e) {
    box.textContent = errorText(e);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------- Work mode: jobs with several steps

const WORK_STATUS = { planning: "Planning", running: "Working", waiting: "Needs your OK", done: "Done",
  failed: "Stopped with a problem", stopped: "Stopped" };
const STEP_MARK = { todo: "", running: "...", done: "✓", failed: "!", skipped: "–", waiting: "?" };
const ACTIVE = ["planning", "running", "waiting"];
let workJob = null;
let workTimer = null;

async function loadWork() {
  try {
    const data = await api("/api/work", undefined, 15000);
    drawWorkList(data.jobs);
    const box = $("work-examples");
    if (!box.children.length) {
      for (const ex of data.examples.slice(0, 3)) box.append(chip(ex, () => { $("work-goal").value = ex; $("work-goal").focus(); }));
    }
    drawSkills(data.skills || []);
    const running = data.jobs.find((j) => ACTIVE.includes(j.status));
    if (!workJob && (running || data.jobs[0])) showJob(running || data.jobs[0]);
  } catch (e) {
    $("work-list").replaceChildren(el("li", { class: "muted", text: errorText(e) }));
  }
}

function whenLabel(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function drawSkills(skills) {
  const box = $("work-skills");
  box.replaceChildren();
  $("work-skills-row").hidden = !skills.length;
  for (const s of skills) {
    box.append(chip(`${s.name} (${s.steps.length} steps)`, async () => {
      try { const job = await api("/api/work", { goal: s.goal, skill_id: s.id }, 20000); showJob(job); loadWork(); }
      catch (e) { $("work-status").textContent = errorText(e); }
    }));
  }
}

function drawWorkList(jobs) {
  const list = $("work-list");
  list.replaceChildren();
  if (!jobs.length) list.append(el("li", { class: "muted", text: "No jobs yet. Describe one above and press Start." }));
  for (const j of jobs) {
    const open = el("button", { type: "button", class: "work-item" },
      el("strong", { text: j.title || j.goal }),
      el("small", { text: `${WORK_STATUS[j.status] || j.status}, ${whenLabel(j.created_at)}` }));
    open.addEventListener("click", () => { showJob(j); pollJob(j.id, ""); });
    list.append(el("li", {}, open));
  }
}

function showJob(job) {
  workJob = job;
  $("work-current").hidden = false;
  $("work-title").textContent = job.title || job.goal;
  $("work-status").textContent = job.spoken || "";
  $("work-badge").textContent = WORK_STATUS[job.status] || job.status;
  $("work-badge").className = `work-badge ${job.status}`;
  const list = $("work-steps");
  list.replaceChildren();
  if (!job.steps.length) list.append(el("li", { class: "work-step running" }, el("span", { class: "work-mark", text: "..." }),
    el("span", {}, el("strong", { text: "Making a plan" }))));
  for (const s of job.steps) {
    list.append(el("li", { class: `work-step ${s.status}` }, el("span", { class: "work-mark", text: STEP_MARK[s.status] ?? "" }),
      el("span", {}, el("strong", { text: s.title }), s.note ? el("small", { text: s.note }) : "")));
  }
  const waiting = job.steps.find((s) => s.status === "waiting");
  $("work-confirm").hidden = !waiting;
  if (waiting) {
    $("work-confirm-text").textContent = `Next: ${waiting.title}. This changes something outside AIRTAC, so it needs your OK. `
      + `It will send: ${JSON.stringify(waiting.args)}`;
  }
  const result = $("work-result");
  result.replaceChildren();
  // The report is shown here (no file is made by itself); Convert makes a PDF, Word or PowerPoint file.
  if (job.report) result.append(el("div", { class: "md work-report" }, renderMd(job.report)));
  for (const [fmt, url] of Object.entries(job.files || {})) {
    result.append(el("a", { href: API + url, target: "_blank", rel: "noopener", class: "file-link" }, icon("pdf"), `Open the ${({ pdf: "PDF", docx: "Word file", pptx: "PowerPoint" })[fmt]}`));
  }
  // PDF, Word and PowerPoint buttons: always shown, usable once there's a report.
  const convertTo = (fmt, label) => {
    const c = chip(label, async () => {
      try { await api(`/api/work/${job.id}/convert/${fmt}`, {}, 120000); showJob(await api(`/api/work/${job.id}`, undefined, 15000)); }
      catch (e) { $("work-status").textContent = errorText(e); }
    });
    c.disabled = !job.report;
    if (!job.report) c.title = "Ready when the report is";
    return c;
  };
  result.append(el("div", { class: "chips work-convert" }, icon("pdf"), el("span", { class: "muted small", text: job.report ? "Make a file:" : "Make a file (when the report is ready):" }),
    convertTo("pdf", "PDF"), convertTo("docx", "Word"), convertTo("pptx", "PowerPoint")));
  const wanted = workWants[job.id];
  if (wanted && job.report && job.status === "done" && !(job.files || {})[wanted]) {
    delete workWants[job.id];
    api(`/api/work/${job.id}/convert/${wanted}`, {}, 120000).then(async () => showJob(await api(`/api/work/${job.id}`, undefined, 15000)))
      .catch((e) => { $("work-status").textContent = errorText(e); });
  }
  if (job.status === "done") {
    const keep = chip("Save as a skill", async () => {
      const name = prompt("Name this skill (you can run it again in one click):", job.title);
      if (name === null) return;
      try { await api("/api/skills", { job_id: job.id, name }, 10000); keep.disabled = true; keep.textContent = "Saved as a skill"; loadWork(); }
      catch (e) { $("work-status").textContent = errorText(e); }
    });
    result.append(el("div", { class: "chips" }, keep));
  }
  $("work-stop").hidden = !ACTIVE.includes(job.status);
  clearTimeout(workTimer);
  if (job.status === "planning" || job.status === "running") workTimer = setTimeout(() => pollJob(job.id, job.status), 2500);
}

async function pollJob(id, before) {
  try {
    const job = await api(`/api/work/${id}`, undefined, 15000);
    if (workJob && workJob.id !== id) return;
    showJob(job);
    if (before && job.status !== before && !["planning", "running"].includes(job.status)) {
      speak(job.spoken);
      loadWork();
    }
  } catch {
    workTimer = setTimeout(() => pollJob(id, before), 5000);
  }
}

const workWants = {};   // job id -> "pdf" | "docx" | "pptx", picked in the File menu before Start

async function startWork(event) {
  event.preventDefault();
  const goal = $("work-goal").value.trim();
  if (goal.length < 3) { $("work-goal").focus(); return; }
  try {
    const job = await api("/api/work", { goal }, 20000);
    const fmt = $("work-format").value;
    if (fmt) workWants[job.id] = fmt;      // made by itself when the report is ready
    $("work-goal").value = "";
    showJob(job);
    loadWork();
  } catch (e) {
    $("work-status").textContent = errorText(e);
  }
}

async function workAction(path, body) {
  if (!workJob) return;
  try {
    showJob(await api(`/api/work/${workJob.id}/${path}`, body, 15000));
  } catch (e) {
    $("work-status").textContent = errorText(e);
  }
  loadWork();
}

$("announce-mode").value = announceMode();
$("announce-mode").addEventListener("change", () => save("airtac-announce-mode", $("announce-mode").value));
$("voice-pick").addEventListener("change", () => save("airtac-voice", $("voice-pick").value));
$("voice-rate").value = load("airtac-rate") || "1";
$("voice-rate").addEventListener("change", () => save("airtac-rate", $("voice-rate").value));
$("voice-test").addEventListener("click", () => speak("Hi, I'm AIRTAC. This is how I'll read your day.", true));
if (canSpeak) {
  loadVoices();
  speechSynthesis.addEventListener("voiceschanged", loadVoices);
}
$("today-open").addEventListener("click", (event) => { event.preventDefault(); openApp("schedule"); });
$("text").addEventListener("input", () => { clearTimeout(suggestTimer); suggestTimer = setTimeout(updateSuggest, 180); });
$("text").addEventListener("focus", updateSuggest);
$("text").addEventListener("blur", () => setTimeout(closeSuggest, 120));
$("text").addEventListener("keydown", onSearchKey);
$("approve").addEventListener("click", () => answerPending(true));
$("reject").addEventListener("click", () => answerPending(false));
$("kill").addEventListener("click", stopAll);
$("news-refresh").addEventListener("click", loadNews);
$("paste").addEventListener("click", pasteProblem);
$("solve").addEventListener("click", solvePasted);
$("save-syllabus").addEventListener("click", saveSyllabus);
$("menu-open").addEventListener("click", () => setDrawer(true));
$("menu-close").addEventListener("click", () => setDrawer(false));
$("scrim").addEventListener("click", () => setDrawer(false));
$("web-go").addEventListener("click", () => openWeb($("text").value.trim()));
$("fb-good").addEventListener("click", () => rateAnswer(true));
$("fb-bad").addEventListener("click", () => rateAnswer(false));
$("fb-form").addEventListener("submit", saveCorrection);
$("lib-file").addEventListener("change", () => { uploadFiles([...$("lib-file").files]); $("lib-file").value = ""; });
$("lib-reindex").addEventListener("click", reindexLibrary);
$("repo-form").addEventListener("submit", addRepo);
$("lib-search").addEventListener("submit", searchLibrary);
$("imp-file").addEventListener("change", () => { importSchedule($("imp-file").files[0]); $("imp-file").value = ""; });
$("imp-start").addEventListener("change", () => { $("imp-date").hidden = $("imp-start").value !== "date"; });
$("sched-ask").addEventListener("submit", askSchedule);
$("plugin-form").addEventListener("submit", makePlugin);
$("view-list").addEventListener("click", () => setSchedView("list"));
$("view-cal").addEventListener("click", () => setSchedView("cal"));
$("rs-brain").addEventListener("change", () => save("airtac-rs-brain", $("rs-brain").value));
for (const kind of ["dragenter", "dragover"]) $("drop").addEventListener(kind, (e) => { e.preventDefault(); $("drop").classList.add("over"); });
for (const kind of ["dragleave", "drop"]) $("drop").addEventListener(kind, () => $("drop").classList.remove("over"));
$("drop").addEventListener("drop", (e) => { e.preventDefault(); uploadFiles([...e.dataTransfer.files]); });
$("web-form").addEventListener("submit", (event) => { event.preventDefault(); webSearch(); });
$("reader-sum").addEventListener("click", summarisePage);
$("reader-close").addEventListener("click", closeReader);
$("work-form").addEventListener("submit", startWork);
$("work-approve").addEventListener("click", () => workAction("confirm", { approve: true }));
$("work-skip").addEventListener("click", () => workAction("confirm", { approve: false }));
$("work-stop").addEventListener("click", async () => {
  if (!workJob) return;
  try { await api(`/api/work/${workJob.id}/stop`, {}, 10000); } catch { /* the next poll shows the state */ }
  pollJob(workJob.id, workJob.status);
});
$("ev-holidays").addEventListener("click", async () => {
  $("ev-extra-note").textContent = "Reading Google's Holidays in India calendar...";
  try { const r = await api("/api/plugins/schedule/holidays", {}, 60000); $("ev-extra-note").textContent = `Added ${r.added} festivals and holidays (${r.skipped} already there).`; loadApp("schedule", false); }
  catch (e) { $("ev-extra-note").textContent = errorText(e); }
});
$("ev-tidy").addEventListener("change", async () => {
  try { const r = await api(`/api/plugins/schedule/tidy?days=${$("ev-tidy").value}`, {}, 15000); $("ev-extra-note").textContent = r.days ? `Done items go after ${r.days} days (${r.removed} removed now). Repeating items stay.` : "Done items are kept."; loadSchedule(); }
  catch (e) { $("ev-extra-note").textContent = errorText(e); }
});
$("conn-q").addEventListener("input", () => { conn.q = $("conn-q").value; drawConnections(); });
$("conn-add").addEventListener("click", () => customKeyForm());
$("conn-lock").addEventListener("click", () => { vault.open = true; vaultLockNow(); });
$("history-by-q").addEventListener("click", () => { hist.mode = "q"; loadHistory(); });
$("history-by-chat").addEventListener("click", () => { hist.mode = "chat"; loadHistory(); });
$("history-del").addEventListener("click", deletePickedHistory);
$("history-clear").addEventListener("click", clearAllHistory);
$("history-q").addEventListener("input", () => { clearTimeout(historyTimer); historyTimer = setTimeout(loadHistory, 200); });

for (const a of document.querySelectorAll("[data-view]")) {
  a.addEventListener("click", (event) => {
    event.preventDefault();
    try { history.replaceState(null, "", `#${a.dataset.view}`); } catch { /* some embedded views refuse it */ }
    showView(a.dataset.view);
    setDrawer(false);
  });
}
for (const b of document.querySelectorAll("[data-open]")) b.addEventListener("click", () => openDialog(b.dataset.open));
for (const r of document.querySelectorAll('input[name="theme"]')) r.addEventListener("change", () => applyTheme(r.value));

document.addEventListener("keydown", (event) => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
  if (event.key === "/" && !typing) {
    event.preventDefault();
    showView("home");
    $("text").focus();
  } else if (event.key === "Escape") {
    setDrawer(false);
  } else if (event.altKey && /^[1-9]$/.test(event.key)) {
    // Alt+1 to Alt+9: the menu's items, in order (Home, ...)
    const link = document.querySelectorAll(".drawer-nav:not(.drawer-apps) a[data-view]")[Number(event.key) - 1];
    if (link) { event.preventDefault(); setDrawer(false); setHash(link.dataset.view); showView(link.dataset.view); }
  }
});
$("boot-toggle").checked = load("airtac-boot") !== "off";
$("boot-toggle").addEventListener("change", () => save("airtac-boot", $("boot-toggle").checked ? "on" : "off"));

// The side panel, command palette and AI apps (dock.js) get their data here.
window.DOCK_HOST = {
  tabs: ["ask", "web", "ai", "today", "tokens"],
  get here() { return launch.here; },
  linkNote: "On this PC, apps open in AIRTAC's Chrome profile, so you stay signed in. Star an app to keep it on Home.",
  async aiApps() {
    const d = await api("/api/plugins/desktop/tiles", undefined, 10000);
    launch = { ...launch, ...d };
    return { apps: d.ai_all, groups: d.ai_groups, pinned: d.pinned };
  },
  openUrl: (event, url) => openSite(event, url),
  addApp: async (a) => (await api("/api/plugins/desktop/ai_apps", a, 10000)).ai_all,
  removeApp: async (a) => (await api("/api/plugins/desktop/ai_apps/remove", { name: a.name, url: a.url }, 10000)).ai_all,
  webSearch: (q) => api(`/api/plugins/web/search?q=${encodeURIComponent(q)}`, undefined, 30000),
  find: (q, kinds) => api(`/api/plugins/finder?q=${encodeURIComponent(q)}&kinds=${encodeURIComponent(kinds || "")}&limit=6`, undefined, 15000),
  go: (r) => goToResult(r),
  readPage: (url) => api(`/api/plugins/web/read?url=${encodeURIComponent(url)}`, undefined, 30000),
  schedule: async () => (await api("/api/plugins/schedule", undefined, 15000)).events,
  tokens: () => api("/api/plugins/tokens/summary", undefined, 15000),
  saveTokens: (b) => api("/api/plugins/tokens/settings", b, 15000),
};

// Where a Search-everything (Ctrl+K) result leads.
function goToResult(r) {
  const g = r.go || {};
  if (g.app) {
    const a = allCatalogApps().find((x) => x.id === g.app) || { id: g.app, url: g.url, app: g.page, open_as: g.open_as, name: r.title };
    if (a.app) { location.hash = `app-${a.app}`; return; }
    if (a.open_as === "tab") { openInTab(a); return; }
    if (launch.here) { openCatalogApp({ preventDefault() {} }, a); return; }
    window.open(a.url, "_blank", "noopener,noreferrer");
    return;
  }
  if (g.url) { window.open(g.url, "_blank", "noopener"); return; }
  if (!g.view) return;
  setHash(g.view);
  showView(g.view);
  if (g.view === "history" && g.q) { $("history-q").value = g.q; loadHistory(); }
  if (g.view === "research" && g.note && typeof openNote === "function") openNote(g.note);
  if (g.focus) setTimeout(() => { const t = $(g.focus); if (t) { t.scrollIntoView({ block: "center" }); t.focus({ preventScroll: true }); } }, 120);
}

// Typed or bookmarked addresses (#web, #work, #app-schedule) and the Back button switch views too.
window.addEventListener("hashchange", () => showView(location.hash.slice(1) || "home"));

document.body.dataset.state = "idle";
applyTheme(load("airtac-theme") || "crimson");
drawApps();
hubInit();
showView(location.hash.slice(1) || "home");
routeIdle();
wakeServer().then(async (ok) => {
  if (!ok) return;
  link.pageReady = true;   // a later outage refreshes the page in place instead of reloading it
  routeIdle();  // now we know whether Nemotron or the keyword rules answer
  try {
    APPS = await api("/api/apps", undefined, 10000);  // the server's list wins if it differs
    drawApps();
    if (appsWaiting && location.hash === `#app-${appsWaiting}`) openApp(appsWaiting);
    appsWaiting = null;
  } catch { /* keep the built-in list */ }
  loadLaunch();
  loadBriefing();
  loadNews();
  loadProgress();
  pollNotifications();
});

// Installable app: the service worker keeps the page itself available, so AIRTAC opens like an app
// on this PC and on your phone (Install from the browser menu). Answers still come from your PC.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => { /* fine without it */ });
