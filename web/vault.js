// The vault on the page (Part D): one gate for Passwords and APIs and keys, the recovery key,
// the strength meter, Show (10 s) and Copy, and locking when the window is hidden.
// The server (plugins/passwords) does the real work; this file never keeps a secret longer than it shows it.

const vault = { status: null, open: false };

async function vaultStatus() {
  vault.status = await api("/api/plugins/passwords/status", undefined, 10000);
  vault.open = Boolean(vault.status.here && vault.status.unlocked);
  return vault.status;
}

function vaultLockNow() {
  if (!vault.open) return;
  vault.open = false;
  api("/api/plugins/passwords/lock", {}, 5000).catch(() => { /* it locks itself after 5 minutes anyway */ });
  for (const id of ["view-passwords", "view-connections"]) {
    const v = $(id);
    if (v && !v.hidden) setTimeout(() => (id === "view-passwords" ? loadPasswords() : loadConnections()), 100);
  }
}

// Locks when the window is hidden (another app in front, minimized, the screen locked).
document.addEventListener("visibilitychange", () => { if (document.hidden) vaultLockNow(); });

// ---------------------------------------------------------------- the gate

// Draws setup / unlock / recover into `box` and calls onOpen() once the vault is open.
// what: "passwords" or "keys" (only changes the words).
async function vaultGate(box, onOpen, what = "passwords") {
  let s;
  try { s = await vaultStatus(); } catch (e) { box.replaceChildren(el("p", { class: "warn-note", text: errorText(e) })); return false; }
  if (!s.here) {
    box.replaceChildren(el("div", { class: "panel pc-only" }, icon("lock"), el("p", { text: s.message || "Vault works on this PC only." }),
      el("p", { class: "muted small", text: "Your passwords and keys never leave the PC that runs AIRTAC, so this page doesn't open on a phone." })));
    return false;
  }
  if (s.unlocked) { await onOpen(s); return true; }
  if (!s.exists) box.replaceChildren(vaultSetupForm(s, () => vaultGate(box, onOpen, what), what));
  else box.replaceChildren(vaultUnlockForm(s, () => vaultGate(box, onOpen, what), what));
  setTimeout(() => box.querySelector("input")?.focus(), 50);
  return false;
}

function vaultMsg(form, text) {
  let m = form.querySelector(".vault-msg");
  if (!m) { m = el("p", { class: "warn-note vault-msg", role: "status" }); form.append(m); }
  m.textContent = text;
  m.hidden = !text;
}

function strengthMeter(input) {
  const bar = el("span", { class: "strength-bar" }, el("i"));
  const words = el("span", { class: "muted small", "aria-live": "polite" });
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!input.value) { bar.dataset.score = ""; words.textContent = ""; return; }
      try {
        const r = await api("/api/plugins/passwords/strength", { passcode: input.value }, 8000);
        bar.dataset.score = r.score;
        words.textContent = `${r.label}${r.hint ? `: ${r.hint}` : ""}`;
      } catch { /* the meter is only a hint */ }
    }, 250);
  });
  return el("div", { class: "strength" }, bar, words);
}

function pwInput(label, auto) {
  return el("input", { type: "password", class: "field", autocomplete: auto, maxlength: "200", required: "", "aria-label": label, spellcheck: "false" });
}

function vaultSetupForm(s, then, what) {
  const a = pwInput("Super password", "new-password");
  const b = pwInput("Type it again", "new-password");
  const ok = el("input", { type: "checkbox", required: "" });
  const form = el("form", { class: "panel pw-setup", autocomplete: "off" },
    el("h3", { text: "Create your super password" }),
    el("p", { text: `One password opens your logins and your API keys. ${what === "keys" ? "Your API keys move into the vault when you create it." : ""}` }),
    el("p", { class: "muted small", text: `At least ${s.min_length} characters. A few random words is strong and easy to remember.` }),
    el("label", { class: "setting" }, "Super password", a), strengthMeter(a),
    el("label", { class: "setting" }, "Type it again", b),
    el("label", { class: "switch" }, ok, " I understand: if I forget this password AND lose the recovery key, everything in the vault is lost."),
    el("button", { type: "submit", class: "primary", text: "Create the vault" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (a.value !== b.value) return vaultMsg(form, "The two passwords aren't the same.");
    try {
      const r = await api("/api/plugins/passwords/setup", { passcode: a.value, again: b.value }, 60000);
      a.value = b.value = "";
      vault.open = true;
      await recoveryModal(r.recovery_key, true);
      then();
    } catch (err) { vaultMsg(form, errorText(err)); }
  });
  return form;
}

function vaultUnlockForm(s, then, what) {
  const input = pwInput(s.migrate ? "Current master passcode" : "Super password", "current-password");
  input.placeholder = s.migrate ? "Your current master passcode" : "Super password";
  const forgot = el("button", { type: "button", class: "quiet-link", text: "Forgot it? Use your recovery key" });
  forgot.hidden = Boolean(s.migrate);
  const form = el("form", { class: "panel pw-unlock", autocomplete: "off" },
    el("h3", {}, icon("lock"), s.migrate ? " Upgrade your vault" : " Locked"),
    el("p", { class: "muted small", text: s.migrate
      ? "Your vault gets stronger encryption. Type your current master passcode once: it becomes your super password. A copy of the old vault is kept in data\\backups."
      : `${what === "keys" ? "Your API keys are" : `${s.count} login${s.count === 1 ? " is" : "s are"}`} in the vault. It locks after 5 minutes without use and when this window is hidden.` }),
    el("div", { class: "row" }, input, el("button", { type: "submit", class: "primary" }, icon("unlock"), "Unlock")), forgot);
  if (s.wait_seconds > 0) vaultMsg(form, `Too many wrong tries. Try again in ${fmtWait(s.wait_seconds)}.`);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/plugins/passwords/unlock", { passcode: input.value }, 60000);
      input.value = "";
      vault.open = true;
      if (r.migrated) toast({ title: "Vault upgraded", body: `${r.count} login${r.count === 1 ? "" : "s"} moved to the new format. The old file is kept in data\\backups.` });
      if (r.recovery_key) await recoveryModal(r.recovery_key, true);
      if (r.weak) toast({ title: "Your super password is weak", body: "Change it to a longer one: Change super password, on the Passwords page." });
      then();
    } catch (err) { input.value = ""; vaultMsg(form, errorText(err)); }
  });
  forgot.addEventListener("click", () => form.replaceWith(vaultRecoverForm(s, then)));
  return form;
}

function fmtWait(sec) { return sec >= 120 ? `${Math.ceil(sec / 60)} minutes` : `${sec} seconds`; }

function vaultRecoverForm(s, then) {
  const key = el("input", { class: "field mono", maxlength: "80", required: "", "aria-label": "Recovery key", placeholder: "ABCD-EFGH-...", spellcheck: "false", autocomplete: "off" });
  const a = pwInput("New super password", "new-password");
  const b = pwInput("Type it again", "new-password");
  const form = el("form", { class: "panel pw-setup", autocomplete: "off" },
    el("h3", { text: "Use your recovery key" }),
    el("p", { class: "muted small", text: "The key you saved when you created the vault. Then choose a new super password." }),
    el("label", { class: "setting" }, "Recovery key", key), el("label", { class: "setting" }, "New super password", a), strengthMeter(a),
    el("label", { class: "setting" }, "Type it again", b),
    el("div", { class: "row" }, el("span", { class: "spacer" }), button("Back", "back", () => form.replaceWith(vaultUnlockForm(s, then, "passwords"))),
      el("button", { type: "submit", class: "primary", text: "Open and set the new password" })));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/plugins/passwords/recover", { key: key.value, new: a.value, again: b.value }, 60000);
      key.value = a.value = b.value = "";
      toast({ title: "New super password set", body: "Your old recovery key still works. Make a new one on the Passwords page if you think someone saw it." });
      then();
    } catch (err) { vaultMsg(form, errorText(err)); }
  });
  return form;
}

// Shown once: the recovery key, a Copy button, and "I saved it" before it can close.
function recoveryModal(key, first = false) {
  return new Promise((resolve) => {
    if (!key) return resolve();
    const code = el("code", { class: "recovery-key", text: key });
    const saved = el("input", { type: "checkbox" });
    const done = el("button", { type: "button", class: "primary", text: "Done" });
    done.disabled = true;
    saved.addEventListener("change", () => { done.disabled = !saved.checked; });
    const copy = button("Copy", "copy", async () => {
      try { await navigator.clipboard.writeText(key); copy.lastChild.textContent = "Copied"; } catch { copy.lastChild.textContent = "Select it and copy"; }
    });
    const dialog = el("dialog", { class: "sheet conn-sheet pass-sheet", "aria-labelledby": "rk-title" },
      el("div", { class: "sheet-body" },
        el("h2", { id: "rk-title", text: first ? "Your recovery key" : "Your new recovery key" }),
        el("p", { text: "If you ever forget your super password, this key opens the vault. Write it down or keep it in a safe place, not on this PC." }),
        code, el("div", { class: "row" }, copy),
        el("p", { class: "warn-note", text: "It's shown only now. Forgetting both your super password and this key means the data in the vault is lost: nobody can recover it." }),
        el("label", { class: "switch" }, saved, " I saved it"), el("div", { class: "row" }, el("span", { class: "spacer" }), done)));
    document.body.append(dialog);
    const close = () => { code.textContent = ""; dialog.close(); dialog.remove(); resolve(); };
    done.addEventListener("click", close);
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); if (saved.checked) close(); });
    dialog.showModal();
  });
}

// ---------------------------------------------------------------- Show for 10 seconds

// reveal(): a promise of {value, hide_after}. The value is put in `target` and taken out again.
function showFor(target, reveal, masked = "••••••••") {
  const b = button("Show", "eye", async () => {
    if (b.dataset.on) { hide(); return; }
    try {
      const r = await reveal();
      target.textContent = r.value;
      b.dataset.on = "1";
      b.lastChild.textContent = "Hide";
      b.timer = setTimeout(hide, (r.hide_after || 10) * 1000);
    } catch (e) { toast({ title: errorText(e) }); }
  });
  function hide() { clearTimeout(b.timer); target.textContent = masked; delete b.dataset.on; b.lastChild.textContent = "Show"; }
  return b;
}

// ---------------------------------------------------------------- Passwords page

async function loadPasswords() {
  clearInterval(hub.pwTick);
  $("pw-lock").hidden = true;
  $("pw-state").replaceChildren();
  pwMessage("");
  await vaultGate($("pw-body"), async (s) => {
    $("pw-lock").hidden = false;
    $("pw-state").replaceChildren(icon("unlock"), el("span", { text: "Unlocked" }));
    await drawPwOpen();
  }, "passwords");
  if (!vault.open && vault.status?.exists) $("pw-state").replaceChildren(icon("lock"), el("span", { text: "Locked" }));
}

function pwMessage(text) {
  $("pw-msg").textContent = text;
  $("pw-msg").hidden = !text;
}

async function unlockPrompt() {
  // From Social or AI pages ("Sign in with saved login"): open the Passwords page to unlock there.
  toast({ title: "Unlock the vault first", body: "Type your super password on the Passwords page, then press the button again." });
  setHash("passwords"); showView("passwords");
  return false;
}

async function drawPwOpen() {
  let d;
  try { d = await api("/api/plugins/passwords/entries", undefined, 10000); }
  catch (e) { return loadPasswords(); }
  if (!hub.apps) { try { await catalog(); } catch { /* logos are optional */ } }
  const apps = [...(hub.apps?.social || []), ...(hub.apps?.ai_all || [])];
  const timer = el("span", { class: "muted small", "aria-live": "off" });
  const q = el("input", { type: "search", class: "field", placeholder: "Search logins", "aria-label": "Search logins" });
  const toolbar = el("div", { class: "row pw-tools" },
    button("Add a login", "key", () => drawPwForm(null, apps), "primary"), q,
    button("Change super password", "refresh", changePasscode),
    button("New recovery key", "key", async () => {
      try { const r = await api("/api/plugins/passwords/recovery-key", {}, 60000); await recoveryModal(r.recovery_key); }
      catch (e) { pwMessage(errorText(e)); }
    }),
    button("Export encrypted backup", "install", async () => {
      try { const r = await api("/api/plugins/passwords/export", {}, 20000); toast({ title: "Encrypted backup saved", body: `${r.file} in ${r.folder}. It opens only with your super password.` }); }
      catch (e) { pwMessage(errorText(e)); }
    }),
    el("span", { class: "spacer" }), timer);
  const rows = d.entries.map((x) => ({ x, li: pwRow(x, apps) }));
  const list = el("ul", { class: "pw-list" }, ...rows.map((r) => r.li));
  q.addEventListener("input", () => {
    const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    for (const r of rows) r.li.hidden = !words.every((w) => `${r.x.site} ${r.x.username} ${r.x.url}`.toLowerCase().includes(w));
  });
  if (d.weak) pwMessage("Your super password is weak. Change it to a longer one (Change super password).");
  $("pw-body").replaceChildren(toolbar, el("div", { id: "pw-form" }),
    d.entries.length ? list : el("p", { class: "muted", text: "No logins yet. Add one: pick the app, then your user name and password." }));
  let left = d.idle_left;
  const tick = () => {
    timer.textContent = `Locks in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    if (left-- <= 0) { clearInterval(hub.pwTick); vault.open = false; loadPasswords(); }
  };
  tick();
  hub.pwTick = setInterval(tick, 1000);
}

function button(text, ic, run, cls = "quiet") {
  const b = el("button", { type: "button", class: cls }, icon(ic), text);
  b.addEventListener("click", run);
  return b;
}

function pwRow(x, apps) {
  const app = apps.find((a) => a.id === x.app_id);
  const masked = el("span", { class: "pw-mask", text: x.has_password ? "••••••••" : "no password" });
  const show = showFor(masked, () => api(`/api/plugins/passwords/entries/${x.id}/reveal`, {}, 10000));
  show.hidden = !x.has_password;
  const del = button("Delete", "close", async () => {
    if (!del.dataset.armed) { del.dataset.armed = "1"; del.lastChild.textContent = "Press again to delete"; setTimeout(() => { delete del.dataset.armed; del.lastChild.textContent = "Delete"; }, 4000); return; }
    try { await api(`/api/plugins/passwords/entries/${x.id}/delete`, {}, 10000); drawPwOpen(); } catch (e) { pwMessage(errorText(e)); }
  });
  return el("li", { class: "pw-row" },
    appLogo(app || { name: x.site }, "sm"),
    el("div", { class: "pw-who" }, el("strong", { text: x.site }), el("span", { class: "muted small", text: x.username || "no user name" })),
    masked,
    el("div", { class: "pw-actions" },
      button("User name", "copy", () => copySecret(x.id, "username").catch((e) => pwMessage(errorText(e)))),
      button("Password", "copy", () => copySecret(x.id, "password").catch((e) => pwMessage(errorText(e)))),
      show, button("Edit", "edit", () => drawPwForm(x, apps)), del));
}

function drawPwForm(x, apps) {
  const pick = el("select", { class: "field", "aria-label": "App" }, el("option", { value: "", text: "Other (type a name)" }),
    ...apps.map((a) => el("option", { value: a.id, text: a.name })));
  const site = el("input", { class: "field", maxlength: "80", placeholder: "Name, e.g. Instagram", "aria-label": "Name" });
  const url = el("input", { class: "field", maxlength: "300", placeholder: "https://...", "aria-label": "Address" });
  const user = el("input", { class: "field", maxlength: "200", autocomplete: "off", placeholder: "User name or email", "aria-label": "User name" });
  const pass = el("input", { type: "password", class: "field", maxlength: "500", autocomplete: "new-password", placeholder: x ? "Leave empty to keep the saved password" : "Password", "aria-label": "Password" });
  const notes = el("textarea", { class: "field", maxlength: "1000", rows: "2", placeholder: "Notes (optional)", "aria-label": "Notes" });
  const gen = button("Generate", "refresh", async () => {
    try {
      const r = await api("/api/plugins/passwords/generate?length=20", undefined, 10000);
      pass.value = r.password;
      pass.type = "text";
      setTimeout(() => { pass.type = "password"; }, 10000);
    } catch (e) { pwMessage(errorText(e)); }
  });
  if (x) { pick.value = x.app_id || ""; site.value = x.site; url.value = x.url; user.value = x.username || ""; notes.value = x.notes || ""; }
  pick.addEventListener("change", () => {
    const a = apps.find((y) => y.id === pick.value);
    if (a) { site.value = a.name; url.value = a.login || a.url; }
  });
  const form = el("form", { class: "panel pw-form", autocomplete: "off" },
    el("h3", { text: x ? `Edit ${x.site}` : "Add a login" }),
    el("div", { class: "pw-grid" }, pick, site, url, user, el("div", { class: "row" }, pass, gen)), notes,
    el("div", { class: "row" }, el("span", { class: "spacer" }),
      button("Cancel", "close", () => $("pw-form").replaceChildren()), el("button", { type: "submit", class: "primary", text: "Save" })));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { site: site.value, url: url.value, app_id: pick.value, username: user.value, password: pass.value, notes: notes.value };
    try {
      await api(x ? `/api/plugins/passwords/entries/${x.id}` : "/api/plugins/passwords/entries", body, 15000);
      pass.value = "";
      drawPwOpen();
    } catch (err) { pwMessage(errorText(err)); }
  });
  $("pw-form").replaceChildren(form);
  site.focus();
}

// Asks for the current password only when a vault exists (the old "Change" asked even with none).
async function changePasscode() {
  let s;
  try { s = await vaultStatus(); } catch (e) { return toast({ title: errorText(e) }); }
  if (!s.here) return toast({ title: "Vault works on this PC only." });
  const old = el("input", { type: "password", class: "field", autocomplete: "current-password", maxlength: "200", "aria-label": "Current super password" });
  const a = pwInput("New super password", "new-password");
  const b = pwInput("Type it again", "new-password");
  const ok = el("button", { type: "submit", class: "primary", text: s.exists ? "Change it" : "Create the vault" });
  const cancel = el("button", { type: "button", class: "quiet", text: "Cancel" });
  const form = el("form", { class: "sheet-body", autocomplete: "off" },
    el("h2", { text: s.exists ? "Change super password" : "Create your super password" }),
    s.exists ? el("label", { class: "setting" }, "Current super password", old) : "",
    el("label", { class: "setting" }, "New super password", a), strengthMeter(a), el("label", { class: "setting" }, "Type it again", b),
    el("p", { class: "muted small", text: `At least ${s.min_length} characters. Everything in the vault is encrypted again with it.` }),
    el("div", { class: "row" }, el("span", { class: "spacer" }), cancel, ok));
  const dialog = el("dialog", { class: "sheet conn-sheet pass-sheet" }, form);
  document.body.append(dialog);
  const close = () => { old.value = a.value = b.value = ""; dialog.close(); dialog.remove(); };
  cancel.addEventListener("click", close);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/plugins/passwords/change", { old: old.value, new: a.value, again: b.value }, 60000);
      close();
      if (r.recovery_key) await recoveryModal(r.recovery_key, true);
      toast({ title: s.exists ? "Super password changed" : "Vault created", body: "Everything in the vault is encrypted with it." });
      vault.open = true;
      loadPasswords();
    } catch (err) { vaultMsg(form, errorText(err)); }
  });
  dialog.showModal();
  (s.exists ? old : a).focus();
}
