// Settings > Dictation (plugins/dictation, scripts/dictate.py): hold a key anywhere in Windows, speak, and it types.
"use strict";

const Dictation = (() => {
  let timer = null;
  const STATUS = { ready: "Ready: hold the key and talk.", listening: "Listening...", writing: "Writing what you said...",
    stopped: "Off.", stopping: "Stopping...", off: "Off." };

  function fill(sel, entries, value) {
    sel.replaceChildren(...entries.map(([v, t]) => el("option", { value: v, text: t })));
    sel.value = value;
  }

  function draw(d) {
    const s = d.settings, st = d.state || {};
    $("dict-on").checked = s.on;
    fill($("dict-key"), d.keys.map((k) => [k, k.replace(/\b\w/g, (c) => c.toUpperCase())]), s.key);
    fill($("dict-model"), Object.entries(d.models), s.model);
    fill($("dict-lang"), Object.entries(d.languages), s.language);
    $("dict-lang").disabled = s.model.endsWith(".en");
    $("dict-clean").checked = s.clean;
    $("dict-state").textContent = st.status === "error" ? `Couldn't start: ${st.error || "see data\\logs"}. If a package is missing, double-click repair_airtac.cmd.`
      : st.running ? `${STATUS[st.status] || "Running."} Key: ${s.key}.` : s.on ? "Starting..." : "Off.";
  }

  async function load() {
    clearTimeout(timer);
    let d;
    try { d = await api("/api/plugins/dictation", undefined, 10000); }
    catch (e) { $("dict-state").textContent = errorText(e); return; }
    draw(d);
    // Keep the state line fresh, but only while it can be seen: a hidden or minimised window polled every 4 s
    // all day (thousands of GET /api/plugins/dictation lines in server.log).
    if (document.body.dataset.view === "settings" && document.visibilityState === "visible") timer = setTimeout(load, 4000);
  }

  async function change(body) {
    $("dict-state").textContent = body.on === false ? "Stopping..." : "Starting dictation (the first time loads Whisper: about 10 seconds)...";
    try { draw(await api("/api/plugins/dictation", body, 30000)); }
    catch (e) { $("dict-state").textContent = errorText(e); }
  }

  $("dict-on").addEventListener("change", (e) => change({ on: e.target.checked }));
  $("dict-key").addEventListener("change", (e) => change({ key: e.target.value }));
  $("dict-model").addEventListener("change", (e) => change({ model: e.target.value }));
  $("dict-lang").addEventListener("change", (e) => change({ language: e.target.value }));
  $("dict-clean").addEventListener("change", (e) => change({ clean: e.target.checked }));
  if (document.body.dataset.view === "settings") load();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && document.body.dataset.view === "settings") load();
  });
  return { load };
})();
window.Dictation = Dictation;
