// Research > Reverse engineering (plugins/reverse): say what you want, AIRTAC finds a suitable public GitHub repo itself,
// clones it, reads it and does the task from it (Bug 2, 20 Oct); or analyse a project already on this PC.
"use strict";

const Reverse = (() => {
  const notesParts = () => [...document.querySelectorAll("#view-research > .research-head, #view-research > .research")];

  function mode(which) {
    const rev = which === "reverse";
    $("rs-mode-notes").setAttribute("aria-selected", String(!rev));
    $("rs-mode-reverse").setAttribute("aria-selected", String(rev));
    $("rs-reverse").hidden = !rev;
    for (const n of notesParts()) n.hidden = rev;
    if (rev) loadRepos();
  }

  async function loadRepos() {
    let d = { repos: [] };
    try { d = await api("/api/plugins/reverse", undefined, 15000); } catch (e) { $("rv-status").textContent = errorText(e); }
    $("rv-repo").replaceChildren(el("option", { value: "", text: d.repos.length ? "A repository on this PC" : "No repositories cloned yet" }),
      ...d.repos.map((r) => el("option", { value: r.id, text: r.id })));
  }

  function facts(a) {
    const card = (title, ...body) => el("section", { class: "panel rv-card" }, el("h3", { text: title }), ...body);
    const total = a.languages.reduce((n, l) => n + l.lines, 0) || 1;
    const bar = el("div", { class: "rv-bar" }, ...a.languages.map((l, i) => el("span", { style: `flex:${l.lines || 1};--i:${i}`, title: `${l.name}: ${l.lines} lines` })));
    const list = (items) => el("ul", { class: "rv-list" }, ...(items.length ? items : ["none found"]).map((x) => el("li", { text: x })));
    return [
      card("Languages", bar, list(a.languages.map((l) => `${l.name}: ${l.files} files, ${l.lines} lines (${Math.round(l.lines * 100 / total)}%)`))),
      card("AI and frameworks", list(a.ai)),
      card("Entry points", list(a.entry_points)),
      card("Structure", list(a.top_level.slice(0, 20))),
      card("Dependencies", ...Object.entries(a.dependencies).map(([k, v]) => el("details", {}, el("summary", { text: `${k} (${v.length})` }), list(v))),
        ...(Object.keys(a.dependencies).length ? [] : [el("p", { class: "muted small", text: "None listed." })])),
      card("Licence", el("p", { class: "small", text: a.licence }), el("p", { class: "muted small", text: `${a.files} files, ${a.tests} test files` })),
    ];
  }

  async function run(e) {
    e.preventDefault();
    let repo = $("rv-repo").value;
    const url = $("rv-url").value.trim();
    $("rv-out").hidden = true;
    try {
      if (url) {
        $("rv-status").textContent = "Cloning it on this PC (a minute for big projects)...";
        repo = (await api("/api/plugins/repos/add", { url }, 600000)).id;
        await loadRepos();
        $("rv-repo").value = repo;
        $("rv-url").value = "";
      }
      if (!repo) { $("rv-status").textContent = "Pick a repository or paste its GitHub address."; return; }
      $("rv-status").textContent = `Taking ${repo} apart, then writing the plan (the brain can take a minute)...`;
      const r = await api("/api/plugins/reverse/analyze", { repo, save: $("rv-save").checked }, 300000);
      $("rv-facts").replaceChildren(...facts(r.analysis));
      $("rv-plan").replaceChildren(renderMd(r.blueprint));
      $("rv-out").hidden = false;
      $("rv-status").textContent = r.note_id ? "Saved in your Research notes." : "";
    } catch (err) { $("rv-status").textContent = errorText(err); }
  }

  // ---------------------------------------------------------------- "Find a repo and do it"
  const STEPS = [["searching", "Searching GitHub"], ["chosen", "Choosing a repo"], ["cloning", "Getting it onto this PC"],
    ["reading", "Reading it"], ["writing", "Doing your task"]];
  let polling = null;

  function drawSteps(job) {
    const at = STEPS.findIndex(([k]) => k === job.state);
    const done = job.state === "done";
    $("rv-steps").hidden = false;
    $("rv-steps").replaceChildren(...STEPS.map(([k, label], i) => el("li", {
      class: done || i < at ? "done" : i === at ? (job.state === "failed" ? "failed" : "now") : "",
      text: label + (i === at && !done ? "…" : ""),
    })));
    if (job.state === "failed") $("rv-steps").append(el("li", { class: "failed", text: "Couldn't find one" }));
    $("rv-status").textContent = job.message || "";
  }

  function drawChosen(found, request) {
    if (!found) return;
    const c = found.chosen;
    const box = $("rv-chosen");
    box.hidden = false;
    const link = el("a", { href: c.url, target: "_blank", rel: "noopener noreferrer", text: c.id });
    const others = (found.others || []).map((o) => {
      const b = el("button", { type: "button", class: "chip-btn", text: `${o.id} (${o.stars.toLocaleString()}★, ${o.licence})`, title: o.reason });
      b.addEventListener("click", () => find(request, o.id));
      return b;
    });
    box.replaceChildren(el("h3", {}, "Chosen: ", link), el("p", { class: "small", text: c.description || "" }),
      el("p", { class: "muted small", text: `Why: ${c.reason}.` }),
      ...(others.length ? [el("p", { class: "muted small", text: "Use another one instead:" }), el("div", { class: "rv-others" }, ...others)] : []));
  }

  async function find(request, pick = "") {
    clearInterval(polling);
    $("rv-out").hidden = true;
    $("rv-chosen").hidden = true;
    drawSteps({ state: "searching", message: "Starting…" });
    let job;
    try { job = await api("/api/plugins/reverse/discover", { request, pick }, 15000); }
    catch (e) { drawSteps({ state: "failed", message: errorText(e) }); return; }
    const tick = async () => {
      try { job = await api(`/api/plugins/reverse/job/${job.id}`, undefined, 10000); }
      catch (e) { clearInterval(polling); drawSteps({ state: "failed", message: errorText(e) }); return; }
      drawSteps(job);
      drawChosen(job.found, request);
      if (job.state === "done") {
        clearInterval(polling);
        const r = job.result;
        $("rv-facts").replaceChildren(...facts(r.analysis));
        $("rv-plan").replaceChildren(renderMd(r.answer));
        $("rv-out").hidden = false;
        $("rv-status").textContent = `Worked from ${r.repo.id} (${r.how}). It's in ${r.folder}.`;
        loadRepos();
      } else if (job.state === "failed") clearInterval(polling);
    };
    polling = setInterval(tick, 1000);
    tick();
  }

  $("rv-find").addEventListener("submit", (e) => {
    e.preventDefault();
    const request = $("rv-ask").value.trim();
    if (request.length < 3) { $("rv-status").textContent = "Say what you want to build or learn."; return; }
    find(request);
  });

  $("rs-mode-notes").addEventListener("click", () => mode("notes"));
  $("rs-mode-reverse").addEventListener("click", () => mode("reverse"));
  $("rv-form").addEventListener("submit", run);
  return { mode };
})();
window.Reverse = Reverse;
