// The Chat page (Ali, 18 Oct: "answers ... not like chat interaction"): a ChatGPT-style page. A list of chats on the
// left, answers that type out live (POST /api/chat/stream sends newline-delimited JSON), Copy, Listen, Regenerate,
// Edit and Stop. Uses app.js's helpers ($, el, icon, api, renderMd, speak, toast) and hub.js's chosenBrain/Plugins.
// Everything from the server is put on the page as text nodes (renderMd builds nodes, never innerHTML).
"use strict";

const Chat = (() => {
  const KEY = "airtac-chat-current";
  const SECTION_KEY = "airtac-chat-section";
  const state = { sid: "", chats: [], messages: [], busy: false, ctrl: null, sections: [], started: false, filter: "" };

  const newId = () => "c-" + Math.random().toString(36).slice(2, 12);
  const BRAIN_LAST = "airtac-brain-last";   // a new chat starts with the model you used last

  // ---------------------------------------------------------------- the model picker in this page's header
  // Remembered per chat (airtac-brain:<chat id>), sent as `brain` with every message, so the server routes it there.
  function brain() { return ($("cp-brain") && $("cp-brain").value) || ""; }

  function syncBrain() {
    const want = read(`airtac-brain:${state.sid}`) ?? read(BRAIN_LAST) ?? "";
    $("cp-brain").value = typeof pickable === "function" && pickable(want) ? want : "";
  }

  function setBrain(value) {
    $("cp-brain").value = value;
    store(`airtac-brain:${state.sid}`, value);
    store(BRAIN_LAST, value);
  }
  const store = (k, v) => { try { localStorage.setItem(k, v); } catch { /* not important */ } };
  const read = (k) => { try { return localStorage.getItem(k); } catch { return null; } };

  // ---------------------------------------------------------------- the list of chats

  function dayGroup(iso) {
    const d = new Date(iso || Date.now());
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const diff = (today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5;
    return diff <= 0 ? "Today" : diff <= 1 ? "Yesterday" : diff <= 7 ? "Previous 7 days" : "Older";
  }

  async function loadList() {
    try { state.chats = (await api("/api/conversations?limit=200", undefined, 15000)).chats; }
    catch (e) { $("cp-list").replaceChildren(el("li", { class: "muted small", text: errorText(e) })); return; }
    drawList();
  }

  function drawList() {
    const q = state.filter.toLowerCase();
    const shown = state.chats.filter((c) => !q || (c.title || "").toLowerCase().includes(q));
    const out = [];
    let group = "";
    for (const c of shown) {
      const g = dayGroup(c.at);
      if (g !== group) { group = g; out.push(el("li", { class: "cp-group", text: g })); }
      const open = el("button", { type: "button", class: "cp-item", "aria-current": String(c.id === state.sid), title: c.title || "New chat" },
        el("span", { class: "cp-item-title", text: c.title || "New chat" }));
      open.addEventListener("click", () => { openChat(c.id); closeSide(); });
      const rename = el("button", { type: "button", class: "icon-btn small cp-act", "aria-label": `Rename ${c.title}` }, icon("edit"));
      rename.addEventListener("click", () => renameChat(c));
      const del = el("button", { type: "button", class: "icon-btn small cp-act", "aria-label": `Delete ${c.title}` }, icon("close"));
      del.addEventListener("click", () => deleteChat(c));
      out.push(el("li", { class: "cp-row" }, open, rename, del));
    }
    if (!shown.length) out.push(el("li", { class: "muted small cp-empty", text: q ? "No chat matches." : "Your chats appear here." }));
    $("cp-list").replaceChildren(...out);
  }

  async function renameChat(c) {
    const title = prompt("Name this chat", c.title || "");
    if (!title || !title.trim()) return;
    try { await api(`/api/conversations/${encodeURIComponent(c.id)}/rename`, { title: title.trim().slice(0, 80) }, 10000); }
    catch (e) { toast({ title: "Couldn't rename it", body: errorText(e) }); return; }
    await loadList();
    if (c.id === state.sid) $("cp-title").textContent = title.trim();
  }

  async function deleteChat(c) {
    if (!confirm(`Delete "${c.title || "this chat"}"? Its messages are removed from History too.`)) return;
    try { await api("/api/history/delete", { chats: [c.id] }, 15000); }
    catch (e) { toast({ title: "Couldn't delete it", body: errorText(e) }); return; }
    if (c.id === state.sid) newChat();
    await loadList();
  }

  // ---------------------------------------------------------------- one chat

  function newChat() {
    stop();
    state.sid = newId();
    state.messages = [];
    store(KEY, state.sid);
    $("cp-title").textContent = "New chat";
    syncBrain();
    drawThread();
    drawList();
    $("cp-text").focus();
  }

  async function openChat(sid) {
    stop();
    state.sid = sid;
    store(KEY, sid);
    syncBrain();
    let data;
    try { data = await api(`/api/conversations/${encodeURIComponent(sid)}`, undefined, 15000); }
    catch (e) { toast({ title: "Couldn't open that chat", body: errorText(e) }); return; }
    state.messages = data.messages.map((m) => ({ id: m.id, role: m.role, text: m.content }));
    const found = state.chats.find((c) => c.id === sid);
    $("cp-title").textContent = data.title || found?.title || "New chat";
    drawThread();
    drawList();
  }

  function drawThread() {
    const log = $("cp-thread");
    log.replaceChildren(...state.messages.map(bubble));
    $("cp-empty").hidden = state.messages.length > 0;
    markLast();
    scrollDown(true);
  }

  function scrollDown(force) {
    const box = $("cp-scroll");
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 160;
    if (force || near) box.scrollTop = box.scrollHeight;
  }

  // Regenerate only on the newest answer, like ChatGPT.
  function markLast() {
    const nodes = [...$("cp-thread").querySelectorAll(".cp-msg.ai")];
    nodes.forEach((n, i) => n.classList.toggle("last", i === nodes.length - 1));
  }

  function bubble(m) {
    if (m.role === "user") {
      const body = el("div", { class: "cp-body" }, el("p", { text: m.text }));
      const edit = el("button", { type: "button", class: "icon-btn small", "aria-label": "Edit this message", title: "Edit" }, icon("edit"));
      edit.addEventListener("click", () => startEdit(m, li));
      const copy = copyButton(() => m.text);
      const li = el("li", { class: "cp-msg you" }, body, el("div", { class: "cp-actions" }, copy, edit));
      return li;
    }
    const body = el("div", { class: "cp-body md" });
    body.append(renderMd(m.text || ""));
    const li = el("li", { class: "cp-msg ai" }, el("span", { class: "cp-avatar", "aria-hidden": "true" }, icon("spark")),
      el("div", { class: "cp-main" }, el("div", { class: "cp-steps" }), body, actions(m)));
    // A stuck or failed answer names the model it tried, not "Answered".
    const tried = m.extra?.problem && m.extra.problem !== "" ? `Tried: ${shortModel(m.brain?.model || "")}` : "";
    if (m.brain) li.querySelector(".cp-actions").append(el("span", { class: "cp-brain", text: tried || brainText(m.brain) }));
    if (m.extra) extras(li, m.extra);
    return li;
  }

  function copyButton(get) {
    const b = el("button", { type: "button", class: "icon-btn small", "aria-label": "Copy", title: "Copy" }, icon("copy"));
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(get()); b.classList.add("done"); setTimeout(() => b.classList.remove("done"), 1200); }
      catch { toast({ title: "Copy didn't work", body: "Select the text and press Ctrl+C." }); }
    });
    return b;
  }

  function actions(m) {
    const listen = el("button", { type: "button", class: "icon-btn small", "aria-label": "Read aloud", title: "Read aloud" }, icon("volume"));
    listen.addEventListener("click", () => speak(m.spoken || plain(m.text), true));
    const again = el("button", { type: "button", class: "icon-btn small cp-regen", "aria-label": "Regenerate", title: "Regenerate" }, icon("refresh"));
    again.addEventListener("click", () => send("", { regenerate: true }));
    const good = el("button", { type: "button", class: "icon-btn small", "aria-label": "Good answer", title: "Good answer" }, icon("thumb-up"));
    const bad = el("button", { type: "button", class: "icon-btn small", "aria-label": "Wrong answer", title: "Wrong answer" }, icon("thumb-down"));
    const rate = async (ok) => {
      if (!m.id) return;
      const note = ok ? "" : (prompt("What should the answer have been? It becomes a lesson.") || "");
      try { await sendFeedback(m.id, ok, note.slice(0, 500)); good.disabled = bad.disabled = true; (ok ? good : bad).classList.add("done"); }
      catch (e) { toast({ title: "Couldn't save that", body: errorText(e) }); }
    };
    good.addEventListener("click", () => rate(true));
    bad.addEventListener("click", () => rate(false));
    return el("div", { class: "cp-actions" }, copyButton(() => m.text), listen, again, good, bad);
  }

  const plain = (md) => (md || "").replace(/```[\s\S]*?```/g, " ").replace(/[#*_`>|]/g, "").replace(/\s+/g, " ").trim().slice(0, 600);

  // Edit: the message becomes a box; Save sends it in place of the old one (and the answers after it).
  function startEdit(m, li) {
    if (state.busy) return;
    const box = el("textarea", { class: "field cp-edit", rows: "3", maxlength: "8000", "aria-label": "Edit your message" });
    box.value = m.text;
    const save = el("button", { type: "button", class: "primary", text: "Send" });
    const cancel = el("button", { type: "button", class: "quiet", text: "Cancel" });
    const wrap = el("div", { class: "cp-editing" }, box, el("div", { class: "row" }, cancel, save));
    li.replaceChildren(wrap);
    box.focus();
    cancel.addEventListener("click", () => drawThread());
    save.addEventListener("click", () => {
      const text = box.value.trim();
      if (!text) return;
      const at = state.messages.indexOf(m);
      state.messages = state.messages.slice(0, at);
      drawThread();
      send(text, { edit_from: m.id });
    });
    box.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save.click(); } });
  }

  // ---------------------------------------------------------------- sending, and the live answer

  function setBusy(on) {
    state.busy = on;
    $("cp-send").hidden = on;
    $("cp-stop").hidden = !on;
    document.getElementById("view-chat").classList.toggle("cp-busy", on);
    if (typeof setLive === "function") setLive("thinking", on);
  }

  // Stop: the browser stops reading, and the server cancels this chat's model call (so Ollama stops too).
  function stop() {
    if (state.busy) api("/api/kill", { session_id: state.sid }, 5000).catch(() => {});
    if (state.ctrl) { state.ctrl.abort(); state.ctrl = null; }
  }

  async function send(text, extra = {}) {
    if (state.busy) return;
    text = (text || "").trim();
    if (!text && !extra.regenerate) return;
    if (extra.regenerate) {
      // the last answer (and its question) are asked again
      const lastQ = [...state.messages].reverse().find((m) => m.role === "user");
      if (!lastQ) return;
      state.messages = state.messages.slice(0, state.messages.indexOf(lastQ) + 1);
      drawThread();
    } else {
      state.messages.push({ role: "user", text });
      $("cp-thread").append(bubble(state.messages[state.messages.length - 1]));
      $("cp-empty").hidden = true;
    }
    $("cp-text").value = "";
    grow();
    const live = { role: "assistant", text: "" };
    const node = bubble(live);
    node.classList.add("typing");
    $("cp-thread").append(node);
    scrollDown(true);
    const bodyEl = node.querySelector(".cp-body");
    const stepsEl = node.querySelector(".cp-steps");
    const waitEl = el("span", { class: "cp-wait muted small", text: "Thinking…" });
    bodyEl.replaceChildren(el("span", { class: "cp-dots", "aria-hidden": "true" }, el("i"), el("i"), el("i")), waitEl);
    setBusy(true);
    let buffer = "", drawn = 0, raf = 0, reply = null;
    const paint = () => {
      raf = 0;
      if (drawn === buffer.length) return;
      drawn = buffer.length;
      bodyEl.replaceChildren(renderMd(buffer), el("span", { class: "cp-caret", "aria-hidden": "true" }));
      scrollDown(false);
    };
    const section = $("cp-section").value || undefined;
    const payload = { message: text, session_id: state.sid, mode: "chat", brain: brain() || undefined, section,
      plugins: section ? undefined : chosenPlugins(), ...extra };
    state.ctrl = new AbortController();
    try {
      const res = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload), signal: state.ctrl.signal });
      if (res.status === 401) { location.href = "login.html"; return; }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(typeof d.detail === "string" ? d.detail : `AIRTAC had a problem (error ${res.status}).`);
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
          if (ev.type === "wait") { if (!buffer) waitEl.textContent = `Thinking… ${ev.seconds} s · Stop or Esc cancels`; }
          else if (ev.type === "start") { buffer = ""; drawn = -1; }
          else if (ev.type === "token") { buffer += ev.text; if (!raf) raf = requestAnimationFrame(paint); }
          else if (ev.type === "step") drawStep(stepsEl, ev);
          else if (ev.type === "done") reply = ev.reply;
          else if (ev.type === "error") throw new Error(ev.text);
        }
      }
      if (!reply) throw new Error("The answer was cut off. Press Regenerate to try again.");
      finish(node, live, reply, extra);
    } catch (e) {
      if (e.name === "AbortError") {
        live.text = buffer ? buffer + "\n\n_Stopped._" : "_Stopped._";
      } else {
        live.text = (buffer ? buffer + "\n\n" : "") + `**Couldn't answer:** ${errorText(e)}`;
      }
      node.classList.remove("typing");
      bodyEl.replaceChildren(renderMd(live.text));
      state.messages.push(live);
    } finally {
      if (raf) cancelAnimationFrame(raf);
      state.ctrl = null;
      setBusy(false);
      markLast();
      loadList();
    }
  }

  function drawStep(box, ev) {
    const name = TOOL_NAMES[ev.tool] || ev.tool;
    let chip = box.querySelector(`[data-tool="${CSS.escape(ev.tool)}"]:not(.done)`);
    if (!chip) { chip = el("span", { class: "cp-step", "data-tool": ev.tool }); box.append(chip); }
    chip.textContent = ev.status === "running" ? `Using ${name}…` : `${name}${ev.status === "ok" ? "" : " (failed)"}`;
    chip.classList.toggle("done", ev.status !== "running");
    chip.classList.toggle("error", ev.status === "error");
  }

  function finish(node, live, reply, extra) {
    live.id = reply.message_id;
    live.text = reply.text || reply.spoken;
    live.spoken = reply.spoken;
    live.brain = reply.brain;
    live.extra = reply;   // steps, image, details, sources and a pending action: drawn by bubble()
    const user = [...state.messages].reverse().find((m) => m.role === "user");
    if (user && reply.user_message_id) user.id = reply.user_message_id;
    state.messages.push(live);
    node.replaceWith(bubble(live));
    if (typeof chatMeta === "function") chatMeta(reply, "home");
    if (read("airtac-chat-voice") === "on") speak(reply.spoken, true);
    if (state.messages.filter((m) => m.role === "user").length === 1 && !extra.regenerate) {
      $("cp-title").textContent = state.messages[0].text.slice(0, 80);
    }
    scrollDown(false);
  }

  // What a reply carries besides its text (only for answers given since the page opened).
  function extras(li, r) {
    const body = li.querySelector(".cp-body");
    const steps = li.querySelector(".cp-steps");
    for (const s of r.steps || []) drawStep(steps, { tool: s.tool, status: s.status === "pending" ? "running" : s.status });
    const after = [];
    const image = (r.screen || "").match(/^Image: (\/files\/[\w\/.-]+\.png)$/m);
    if (image) after.push(el("img", { class: "preview", src: image[1], alt: "Image from this reply" }));
    if (r.screen && r.screen !== r.spoken && !(r.text || "").includes(r.screen.slice(0, 60))) {
      after.push(el("details", { class: "details cp-details" }, el("summary", { text: "Details" }), el("pre", { class: "screen" }, ...linkify(r.screen))));
    }
    const seen = new Set();
    const sources = (r.sources || []).filter((x) => { const k = `${x.title}|${x.where}`; if (seen.has(k)) return false; seen.add(k); return true; });
    if (sources.length) after.push(el("ul", { class: "sources" }, ...sourceItems(sources)));
    if (r.pending) after.push(pendingBox(r.pending));
    if (r.problem === "timeout" || r.problem === "brain_error") after.push(fallbackBox(r));
    body.after(...after);
  }

  // A stuck or failed answer (Bug 3, 20 Oct): never a silent hang. Offer the cloud brains that have a key;
  // the question is asked again there (the keys stay on the PC: the server adds them).
  function fallbackBox(r) {
    const box = el("div", { class: "cp-fallback" });
    const brains = r.fallback_brains || [];
    if (!brains.length) {
      box.append(el("p", { class: "muted small", text: "No cloud brain has a key yet. Add a free one (Groq or OpenRouter) on APIs and keys, and stuck answers can be retried there." }));
      return box;
    }
    box.append(el("span", { class: "muted small", text: "Try it on a cloud brain:" }));
    for (const b of brains.slice(0, 3)) {
      const go = el("button", { type: "button", class: "chip-btn", text: b.label });
      go.addEventListener("click", () => { if (!state.busy) send("", { regenerate: true, brain: b.id }); });
      box.append(go);
    }
    return box;
  }

  function pendingBox(p) {
    const yes = el("button", { type: "button", class: "primary", text: "Confirm" });
    const no = el("button", { type: "button", class: "quiet", text: "Cancel" });
    const box = el("div", { class: "pending" }, el("p", { text: "This changes something outside AIRTAC, so it needs your OK:" }),
      el("pre", { class: "pending-args", text: pendingArgs(p) }), yes, no);
    const answer = async (ok) => {
      yes.disabled = no.disabled = true;
      try {
        const r = await api("/api/confirm", { pending_id: p.id, approve: ok }, 120000);
        const m = { role: "assistant", id: r.message_id, text: r.text || r.spoken, spoken: r.spoken, brain: r.brain };
        state.messages.push(m);
        $("cp-thread").append(bubble(m));
        markLast();
        scrollDown(true);
      } catch (e) { toast({ title: "Couldn't do that", body: errorText(e) }); }
    };
    yes.addEventListener("click", () => answer(true));
    no.addEventListener("click", () => answer(false));
    return box;
  }

  // ---------------------------------------------------------------- the composer

  function grow() {
    const t = $("cp-text");
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 240) + "px";
  }

  function micInput() {
    const R = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!R) { toast({ title: "Talking isn't available here", body: "Use Chrome or Edge, or the dictation key (Settings > Dictation)." }); return; }
    const rec = new R();
    rec.lang = typeof LANG === "string" ? LANG : "en-IN";
    rec.interimResults = true;
    const before = $("cp-text").value;
    $("cp-mic").classList.add("on");
    rec.onresult = (e) => {
      const said = [...e.results].map((r) => r[0].transcript).join("");
      $("cp-text").value = (before ? before + " " : "") + said;
      grow();
    };
    rec.onend = () => $("cp-mic").classList.remove("on");
    rec.start();
  }

  async function loadSections() {
    let list = [];
    try { list = (await api("/api/plugins/sections", undefined, 10000)).sections || []; } catch { list = []; }
    state.sections = list;
    const sel = $("cp-section");
    const keep = read(SECTION_KEY) || "";
    sel.replaceChildren(el("option", { value: "", text: "General chat" }),
      ...list.map((s) => el("option", { value: s.id, text: s.name })));
    sel.value = list.some((s) => s.id === keep) ? keep : "";
    $("cp-section-wrap").hidden = !list.length;
    sectionLook();
  }

  // The empty chat says which section it's in.
  function sectionLook() {
    const s = state.sections.find((x) => x.id === $("cp-section").value);
    $("cp-empty").querySelector("h2").textContent = s ? s.name : "What can I help with?";
    $("cp-empty").querySelector(".cp-examples").hidden = Boolean(s);
    $("cp-text").placeholder = s ? `Message ${s.name}` : "Message AIRTAC";
  }

  function openSide() { document.getElementById("view-chat").classList.add("side-open"); }
  function closeSide() { document.getElementById("view-chat").classList.remove("side-open"); }

  function wire() {
    $("cp-form").addEventListener("submit", (e) => { e.preventDefault(); send($("cp-text").value); });
    $("cp-text").addEventListener("input", grow);
    $("cp-text").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("cp-text").value); }
      if (e.key === "Escape" && state.busy) stop();
    });
    $("cp-stop").addEventListener("click", stop);
    $("cp-brain").addEventListener("change", (e) => setBrain(e.target.value));
    $("cp-new").addEventListener("click", () => { newChat(); closeSide(); });
    $("cp-search").addEventListener("input", (e) => { state.filter = e.target.value; drawList(); });
    $("cp-mic").addEventListener("click", micInput);
    $("cp-side-open").addEventListener("click", openSide);
    $("cp-side-close").addEventListener("click", closeSide);
    $("cp-section").addEventListener("change", (e) => { store(SECTION_KEY, e.target.value); sectionLook(); });
    $("cp-setup").addEventListener("click", () => { if (typeof openChatSide === "function") openChatSide(); });
    $("cp-title").addEventListener("click", () => {
      const c = state.chats.find((x) => x.id === state.sid);
      if (c) renameChat(c);
    });
    const voice = $("cp-voice");
    voice.checked = read("airtac-chat-voice") === "on";
    voice.addEventListener("change", () => store("airtac-chat-voice", voice.checked ? "on" : "off"));
    for (const b of document.querySelectorAll("[data-cp-example]")) {
      b.addEventListener("click", () => { $("cp-text").value = b.textContent; grow(); $("cp-text").focus(); });
    }
  }

  async function open() {
    const top = document.querySelector(".topbar");
    if (top) document.documentElement.style.setProperty("--chat-top", `${Math.ceil(top.getBoundingClientRect().bottom)}px`);
    if (!state.started) {
      state.started = true;
      wire();
      // Live: chats started elsewhere (the phone, Home) show up in the list without a reload.
      setInterval(() => { if (document.body.dataset.view === "chat" && !state.busy && !document.hidden) loadList(); }, 30000);
      state.sid = read(KEY) || newId();
      await loadList();
      if (state.chats.some((c) => c.id === state.sid)) await openChat(state.sid);
      else newChat();
    } else {
      loadList();
    }
    loadSections();
    // Fill this page's model picker (shared with Home's: hub.js loadBrainChoices), then pick this chat's model.
    if (typeof loadBrainChoices === "function") loadBrainChoices().then(syncBrain); else syncBrain();
    setTimeout(() => $("cp-text").focus(), 60);
  }

  const visible = () => document.body.dataset.view === "chat";

  return { open, send, newChat, openChat, state, brain, setBrain, syncBrain, visible };
})();
window.Chat = Chat;
// app.js may have opened #chat before this file loaded (scripts are deferred in order): open it now.
if (document.body.dataset.view === "chat") Chat.open();
