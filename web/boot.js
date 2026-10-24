// AIRTAC start-up (see boot.css): the name decodes, the server and brain report in, then the doors open.
// app.js tells this script what the server said with a window event: "airtac:health" (detail = /api/health).
// Tap or press any key to skip. Turn it off in the menu (Start-up animation).
(() => {
  const boot = document.getElementById("boot");
  if (!boot) return;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let seen = false;
  try { seen = sessionStorage.getItem("airtac-booted") === "1"; sessionStorage.setItem("airtac-booted", "1"); } catch { /* fine */ }
  // The first open in a session gets the full show; a reload gets the quick one.
  const MIN = reduce ? 500 : seen ? 900 : 1800;
  const MAX = seen ? 1500 : 2600;
  const start = performance.now();
  const lines = document.getElementById("boot-lines");
  let health = null;
  let closing = false;

  // The name decodes: each letter flickers through signal glyphs, then settles, left to right.
  const word = document.getElementById("boot-word");
  if (word && !reduce) {
    const target = word.textContent;
    const glyphs = "ΛΣΞ◆◇▲01<>/#";
    let tick = 0;
    const timer = setInterval(() => {
      tick++;
      word.textContent = [...target].map((ch, i) => (tick > 8 + i * 3 ? ch : glyphs[(tick * 7 + i * 5) % glyphs.length])).join("");
      if (tick > 8 + target.length * 3) clearInterval(timer);
    }, 34);
  }

  function line(text, wait = false) {
    const li = document.createElement("li");
    li.textContent = text;
    if (wait) li.className = "wait";
    lines.append(li);
  }

  function brainText(h) {
    if (h.brain) return h.brain.provider === "mock" ? "Brain: keyword rules" : `Brain: ${h.brain.model}${h.brain.ready === false ? " (starting)" : ""}`;
    if (h.mode) return h.mode === "live" ? "Brain: Nemotron on Nebius" : "Brain: keyword rules (no key yet)";
    return "Brain ready";
  }

  function report() {
    if (health) {
      line("Server connected");
      setTimeout(() => line(brainText(health)), 160);
      // On a PC with a keyboard, teach the one key that opens everything.
      if (matchMedia("(hover: hover)").matches) setTimeout(() => line("Ctrl+K opens any mode or AI app"), 320);
    } else {
      line("Waking the server", true);
    }
  }

  function close() {
    if (closing) return;
    closing = true;
    boot.classList.add("out");
    setTimeout(() => boot.remove(), reduce ? 300 : 820);
  }

  let reported = false;
  function maybeReport() {
    if (reported) return;
    reported = true;
    report();
    const wait = Math.max(MIN - (performance.now() - start), 0) + (health ? 420 : 320);
    setTimeout(close, wait);
  }

  window.addEventListener("airtac:health", (e) => {
    health = e.detail || {};
    if (performance.now() - start > 700) maybeReport(); else setTimeout(maybeReport, 700 - (performance.now() - start));
  }, { once: true });
  setTimeout(maybeReport, MAX);   // the server is slow: open anyway, the page keeps trying

  for (const type of ["pointerdown", "keydown"]) window.addEventListener(type, close, { once: true, capture: true });
})();
