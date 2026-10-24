// The Connectors page (plugins/connectors): cards like Claude's connectors. Connect, switch on or off, and examples.
"use strict";

const Connectors = (() => {
  const API_ = "/api/plugins/connectors";
  let poll = null;

  async function load() {
    let d;
    try { d = await api(API_, undefined, 15000); }
    catch (e) { $("cn-grid").replaceChildren(el("p", { class: "muted", text: errorText(e) })); return; }
    $("cn-grid").replaceChildren(...d.connectors.map(card));
  }

  function card(c) {
    const state = c.connected ? (c.on ? "Connected" : "Connected, switched off") : c.sign_in_again ? "Sign in again" : "Not connected";
    const head = el("div", { class: "cn-head" }, el("img", { class: "cn-logo", src: `./logos/${c.logo}.svg`, alt: "" }),
      el("div", {}, el("h3", { text: c.name }), el("span", { class: `cn-state${c.connected ? " ok" : ""}`, text: state + (c.email && c.connected ? ` · ${c.email}` : "") })));
    const parts = [head, el("p", { class: "small", text: c.about })];
    if (c.needs) parts.push(el("p", { class: "muted small", text: c.needs }));
    const row = el("div", { class: "row" });
    const connect = el("button", { type: "button", class: c.connected ? "quiet" : "primary", text: c.connected ? (c.kind === "google" ? "Connect again" : "Change key") : "Connect" });
    connect.addEventListener("click", () => doConnect(c, connect));
    row.append(connect);
    if (c.connected) {
      const sw = el("input", { type: "checkbox", "aria-label": `Use ${c.name} in chats` });
      sw.checked = c.on;
      sw.addEventListener("change", async () => {
        try { await api(`${API_}/${c.id}/switch`, { on: sw.checked }, 10000); load(); } catch (e) { toast({ title: "Couldn't change it", body: errorText(e) }); }
      });
      row.append(el("label", { class: "switch" }, sw, " Use in chats"));
    }
    parts.push(row);
    if (c.connected) {
      parts.push(el("div", { class: "cn-try" }, el("span", { class: "muted small", text: "Try: " }), ...c.examples.map((x) => {
        const b = el("button", { type: "button", class: "chip-btn", text: x });
        b.addEventListener("click", () => { location.hash = "#chat"; setTimeout(() => { const t = $("cp-text"); if (t) { t.value = x; t.focus(); } }, 400); });
        return b;
      })));
    }
    return el("article", { class: `cn-card${c.connected ? " on" : ""}` }, ...parts);
  }

  async function doConnect(c, btn) {
    btn.disabled = true;
    try {
      const r = await api(`${API_}/${c.id}/connect`, {}, 15000);
      if (r.auth_url) {
        window.open(r.auth_url, "_blank", "noopener");
        toast({ title: `Connecting ${c.name}`, body: r.say });
        clearInterval(poll);
        const until = Date.now() + 300000;
        poll = setInterval(async () => {
          const s = await api("/api/plugins/account", undefined, 10000).catch(() => null);
          if (!s || !s.pending || Date.now() > until) { clearInterval(poll); load(); }
        }, 2500);
      } else if (r.open) {
        location.hash = r.open;
        toast({ title: c.name, body: r.say });
      }
    } catch (e) {
      toast({ title: `Couldn't connect ${c.name}`, body: errorText(e) });
    } finally { btn.disabled = false; }
  }

  function open() { load(); }
  if (document.body.dataset.view === "connectors") open();
  return { open, load };
})();
window.Connectors = Connectors;
