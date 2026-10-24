// Settings' phone QR code with the phone QR code, and Home's live strip
// (clock, date, weather, what's next), which keeps itself up to date like a phone's home screen.
"use strict";

const Extras = (() => {

  // ---------------------------------------------------------------- Home: live strip
  async function weather() {
    try {
      const d = await api("/api/plugins/weather", undefined, 10000);
      const w = d.weather;
      if (!w) return;
      const chip = $("ls-weather");
      chip.textContent = `${Math.round(w.now.temp)}°C ${w.now.sky} · ${w.place}` + (w.today && w.today.rain_chance >= 40 ? ` · rain ${w.today.rain_chance}%` : "");
      chip.hidden = false;
    } catch { /* the weather plugin is off or offline: no chip */ }
  }

  async function next() {
    try {
      const b = await api("/api/briefing", undefined, 15000);
      const hm = new Date().toTimeString().slice(0, 5);
      const today = b.today || [];
      const item = today.find((x) => x.time && x.time >= hm) || today.find((x) => !x.time) || (b.tomorrow || [])[0];
      const chip = $("ls-next");
      chip.hidden = !item;
      if (item) chip.textContent = `${today.includes(item) ? "Next" : "Tomorrow"}: ${item.time && today.includes(item) ? item.time + " " : ""}${item.title}`.slice(0, 60);
    } catch { /* fine */ }
  }

  function tick() {
    const now = new Date();
    $("ls-clock").textContent = now.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    $("ls-sec").textContent = String(now.getSeconds()).padStart(2, "0");
    $("ls-date").textContent = now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  }

  function live() {
    if (!$("live-strip")) return;
    tick();
    setInterval(tick, 1000);
    weather();
    next();
    setInterval(() => { if (!document.hidden) { weather(); next(); } }, 15 * 60000);   // fresh every 15 minutes
  }

  // ---------------------------------------------------------------- Settings: the phone QR code
  async function qr() {
    let d;
    try { d = await api("/api/devices/qr", undefined, 10000); }
    catch (e) { $("mn-qr-text").textContent = errorText(e); return; }
    $("mn-lan").hidden = d.ready;
    if (!d.ready) {
      $("mn-qr").replaceChildren(el("div", { class: "mn-qr-off" }, icon("phone")));
      $("mn-qr-text").textContent = "Turn on phone access (same Wi-Fi as this PC), then scan the code with the phone's camera.";
      return;
    }
    $("mn-qr").replaceChildren(el("img", { src: "data:image/svg+xml;charset=utf-8," + encodeURIComponent(d.qr_svg), alt: `QR code for ${d.url}`, width: 200, height: 200 }));
    $("mn-qr-text").replaceChildren(
      d.kind === "apk" ? "Scan with the phone's camera to download Airtac Mobile (Android). " : "Scan with the phone's camera to open AIRTAC, then menu > Add to Home screen (the Android app isn't built yet). ",
      el("span", { class: "mono wrap", text: d.url }),
      el("br"), "Chrome warns about the certificate once: Advanced > Proceed. Phone and PC must be on the same Wi-Fi.");
  }

  // ---------------------------------------------------------------- Settings: Security
  async function security() {
    let d;
    try { d = await api("/api/security", undefined, 10000); } catch (e) { $("sec-checks").replaceChildren(el("li", { text: errorText(e) })); return; }
    $("sec-checks").replaceChildren(...d.checks.map((c) => el("li", { class: c.ok === true ? "ok" : c.ok === false ? "bad" : "info" },
      el("strong", { text: c.name }), el("span", { class: "muted small", text: c.detail }))));
  }

  async function scan() {
    const b = $("sec-scan"), out = $("sec-scan-out");
    b.disabled = true;
    out.hidden = false;
    out.textContent = "Checking packages for known vulnerabilities, the code for risky patterns and the folder for leaked keys (a few minutes)...";
    try { const r = await api("/api/security/scan", {}, 660000); out.textContent = r.lines.join("\n") || (r.ok ? "No problems found." : "See scripts\\security_check.py."); }
    catch (e) { out.textContent = errorText(e); }
    b.disabled = false;
  }

  function wire() {
    $("sec-scan").addEventListener("click", scan);
    $("mn-pair").addEventListener("click", () => { if (typeof openMobileDialog === "function") openMobileDialog(); });
    $("mn-lan").addEventListener("click", async () => {
      try { await api("/api/devices/lan", { on: true }, 20000); } catch (e) { toast({ title: "Couldn't turn it on", body: errorText(e) }); }
      qr();
    });
  }

  function open() { qr(); security(); }

  wire();
  live();
  const v = document.body.dataset.view;
  if (v === "settings") open();
  return { open };
})();
window.Extras = Extras;
