// Part G: "Get mobile app" (Main chat header only) and Settings > Devices. The pairing code and its QR come from
// /api/pair/start (this PC only); the phone app pins the certificate fingerprint shown here.
"use strict";

const devices = { timer: null };

function fmtWhen(ts) {
  if (!ts) return "never";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function shortFp(fp) {
  return fp ? fp.slice(0, 32).match(/.{4}/g).join(" ") + " ..." : "";
}

function apkPart(apk, lan) {
  if (!apk || !apk.ready) {
    return el("div", { class: "mobile-step" },
      el("h3", { text: "1. Get the app" }),
      el("p", { class: "muted small", text: "The Android app isn't built yet. Build it with GitHub Actions or on your new laptop (docs\\MOBILE.md), then put airtac.apk in mobile\\release\\. Until then, use the web app (step 3)." }));
  }
  const mb = (apk.size / 1048576).toFixed(1);
  return el("div", { class: "mobile-step" },
    el("h3", { text: "1. Get the app" }),
    el("p", {}, "On the phone, open ", el("strong", { class: "wrap", text: `https://${(lan.ips || [])[0] || "<this PC's Wi-Fi address>"}:${lan.port}/download/airtac.apk` }),
      ` (${mb} MB) while phone access on Wi-Fi is on (press Pair below), or copy the file over USB. Chrome warns about the certificate once: Advanced > Proceed.`),
    el("p", { class: "muted small mono wrap", text: `SHA-256: ${apk.sha256}` }),
    el("p", { class: "muted small", text: "Android asks once to allow installs from this source (Settings > Apps > Special access > Install unknown apps > your browser). Play Protect may warn about an app from outside Play: choose More details > Install anyway, only for this file with the SHA-256 above." }));
}

async function openMobileDialog() {
  const box = $("mobile-body");
  box.replaceChildren(el("p", { class: "muted", text: "Loading..." }));
  openDialog("mobile-dialog");
  let d;
  try { d = await api("/api/devices", undefined, 10000); }
  catch (e) { box.replaceChildren(el("p", { class: "muted", text: errorText(e) })); return; }
  const qr = el("div", { class: "mobile-qr", "aria-live": "polite" });
  const pair = button("Pair a phone", "phone", () => startPairing(qr, pair), "primary");
  box.replaceChildren(
    apkPart(d.apk, d.lan),
    el("div", { class: "mobile-step" },
      el("h3", { text: "2. Pair it with this PC" }),
      el("p", { class: "muted small", text: "Press Pair, then scan the code in the app (it's valid for 2 minutes). Phone and PC must be on the same Wi-Fi; away from home, use Tailscale. Windows may ask to let Python use the network: allow Private networks only." }),
      el("div", { class: "row learn-actions" }, pair), qr),
    el("div", { class: "mobile-step" },
      el("h3", { text: "3. Or use the web app" }),
      el("p", { class: "muted small", text: "No install needed: open AIRTAC in Chrome on the phone (over Tailscale, see docs\\PHONE_AND_PC.md), then menu (three dots) > Add to Home screen." })));
}

async function startPairing(qr, btn) {
  clearInterval(devices.timer);
  btn.disabled = true;
  qr.replaceChildren(el("p", { class: "muted", text: "Making a code..." }));
  let r;
  try { r = await api("/api/pair/start", {}, 15000); }
  catch (e) { qr.replaceChildren(el("p", { class: "conn-msg", text: errorText(e) })); btn.disabled = false; return; }
  btn.disabled = false;
  const img = r.qr_svg
    ? el("img", { src: "data:image/svg+xml;charset=utf-8," + encodeURIComponent(r.qr_svg), alt: "Pairing QR code: scan it in Airtac Mobile", width: 220, height: 220 })
    : el("p", { class: "muted small", text: "The QR picture needs the segno package (pip install -r requirements.txt). Type the code in the app instead." });
  const left = el("p", { class: "muted small", role: "timer" });
  const where = r.hosts.length ? r.hosts.map((h) => `${h}:${r.port}`).join(", ") : "no Wi-Fi address found: connect this PC to Wi-Fi";
  qr.replaceChildren(img,
    el("p", { class: "small" }, "Code: ", el("strong", { class: "mono", text: r.code })),
    el("p", { class: "muted small", text: `This PC: ${where}` }),
    el("p", { class: "muted small mono", text: `Certificate: ${shortFp(r.fp)}` }),
    r.google ? el("p", { class: "muted small", text: "This PC is signed in to Google: sign in with the same account in the app." }) : "",
    r.lan.error ? el("p", { class: "conn-msg", text: r.lan.error }) : "",
    left);
  const until = Date.now() + r.expires_in * 1000;
  const tick = () => {
    const s = Math.max(0, Math.round((until - Date.now()) / 1000));
    left.textContent = s ? `Valid for ${s} s.` : "Expired. Press Pair a phone for a new code.";
    if (!s) { clearInterval(devices.timer); img.remove?.(); loadDevices(); }
  };
  tick();
  devices.timer = setInterval(tick, 1000);
}

async function loadDevices() {
  const box = $("set-devices");
  if (!box) return;
  let d;
  try { d = await api("/api/devices", undefined, 10000); }
  catch (e) { box.replaceChildren(el("p", { class: "muted small", text: errorText(e) })); return; }
  const msg = el("p", { class: "conn-msg", role: "status" });
  const lan = d.lan;
  const sw = el("input", { type: "checkbox", id: "lan-on" });
  sw.checked = lan.on;
  sw.addEventListener("change", async () => {
    try { await api("/api/devices/lan", { on: sw.checked }, 15000); msg.textContent = sw.checked ? "Phone access on Wi-Fi is on." : "Phone access on Wi-Fi is off. Paired phones can't reach this PC until you switch it on."; setTimeout(loadDevices, 600); }
    catch (e) { msg.textContent = errorText(e); sw.checked = !sw.checked; }
  });
  const rows = d.devices.map((x) => {
    const rm = button("Remove", "close", async () => {
      if (!rm.dataset.armed) { rm.dataset.armed = "1"; rm.lastChild.textContent = "Press again to remove"; setTimeout(() => { delete rm.dataset.armed; rm.lastChild.textContent = "Remove"; }, 4000); return; }
      try { await api(`/api/devices/${x.id}/revoke`, {}, 10000); loadDevices(); } catch (e) { msg.textContent = errorText(e); }
    });
    return el("li", { class: "dev-row" }, icon("phone"),
      el("span", { class: "dev-name", text: x.name }),
      el("span", { class: "muted small", text: `paired ${fmtWhen(x.paired)} · last seen ${fmtWhen(x.seen)}${x.ip ? " · " + x.ip : ""}` }), rm);
  });
  box.replaceChildren(
    el("label", { class: "switch-row", for: "lan-on" }, sw, el("span", { text: `Phone access on Wi-Fi (HTTPS, port ${lan.port})` }),
      el("span", { class: "muted small", text: lan.running ? " · listening" : lan.on ? " · starting" : "" })),
    lan.error ? el("p", { class: "conn-msg", text: lan.error }) : "",
    lan.fingerprint ? el("p", { class: "muted small mono", text: `Certificate: ${shortFp(lan.fingerprint)}` }) : "",
    rows.length ? el("ul", { class: "dev-list" }, ...rows) : el("p", { class: "muted small", text: "No phones paired yet. Use Get mobile app in the chat header." }),
    msg);
}

document.addEventListener("DOMContentLoaded", () => {
  const b = $("get-mobile");
  if (b) b.addEventListener("click", openMobileDialog);
  const dlg = $("mobile-dialog");
  if (dlg) dlg.addEventListener("close", () => { clearInterval(devices.timer); loadDevices(); });
});
