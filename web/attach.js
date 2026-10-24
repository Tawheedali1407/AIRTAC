// Attachments: ONE component for every chat (Main chat, each app's chat, Research's co-pilot).
// A paperclip button, drag and drop onto the chat, and paste (a screenshot or a copied file).
// Each file becomes a chip: uploading -> reading -> ready (or the reason it couldn't be read), with a
// preview of the text AIRTAC read (or the photo) and a remove button. The server checks every file
// (plugins/attachments); this side only keeps the list and sends the ids with the next message.
// Loaded after app.js; uses its helpers ($, el, icon, api, errorText, sleep).
window.Attach = (() => {
  const TYPES = [".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xlsx", ".csv", ".txt", ".md", ".jpg", ".jpeg", ".png",
    ".mp3", ".wav", ".m4a", ".ogg", ".webm", ".ics"];
  const MAX_MB = 30;
  const MAX_FILES = 10;
  const KIND_ICON = { pdf: "pdf", image: "image", audio: "volume" };
  const boxes = {};

  // One preview dialog for all chips.
  const dialog = el("dialog", { class: "app-dialog attach-dialog", "aria-labelledby": "attach-dialog-title" });
  const dTitle = el("h3", { id: "attach-dialog-title" });
  const dBody = el("div", { class: "attach-preview" });
  const dClose = el("button", { type: "button", class: "quiet", text: "Close" });
  dClose.addEventListener("click", () => dialog.close());
  dialog.append(dTitle, dBody, el("div", { class: "row" }, dClose));
  document.body.append(dialog);

  function mount(key, { form, section, zone, paste, before }) {
    const picker = el("input", { type: "file", multiple: "", accept: TYPES.join(","), hidden: "", "aria-hidden": "true" });
    const btn = el("button", { type: "button", class: "mic attach-btn", "aria-label": "Attach files",
      title: "Attach files: PDF, PowerPoint, Word, Excel/CSV, text, photos, audio (or drop or paste them here)" }, icon("clip"));
    const chips = el("div", { class: "attach-chips", "aria-live": "polite" });
    btn.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => { add(key, [...picker.files]); picker.value = ""; });
    if (before && before.parentNode === form) form.insertBefore(btn, before); else form.append(btn);
    form.append(picker);
    form.after(chips);
    const area = zone || form;
    let depth = 0;
    area.addEventListener("dragenter", (e) => { if (hasFiles(e)) { depth++; area.classList.add("drop-on"); } });
    area.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; area.classList.remove("drop-on"); } });
    area.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
    area.addEventListener("drop", (e) => {
      depth = 0;
      area.classList.remove("drop-on");
      if (!hasFiles(e)) return;
      e.preventDefault();
      add(key, [...e.dataTransfer.files]);
    });
    for (const target of paste || []) {
      target.addEventListener("paste", (e) => {
        const files = [...(e.clipboardData?.files || [])];
        if (!files.length) return;          // ordinary text pastes as usual
        e.preventDefault();
        add(key, files.map((f, i) => (f.name && f.name !== "image.png") ? f
          : new File([f], `pasted-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}${i ? "-" + i : ""}.png`, { type: f.type })));
      });
    }
    boxes[key] = { section, items: [], chips };
  }

  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  const sectionOf = (box) => (typeof box.section === "function" ? box.section() : box.section) || "chat";

  function add(key, files) {
    const box = boxes[key];
    for (const f of files) {
      if (box.items.length >= MAX_FILES) { box.items.push({ name: f.name, status: "error", message: `Up to ${MAX_FILES} files per message.` }); break; }
      const ext = (f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
      const item = { name: f.name, size: f.size, status: "uploading", message: "", id: null };
      box.items.push(item);
      if (!TYPES.includes(ext)) { item.status = "error"; item.message = `AIRTAC takes ${TYPES.map((t) => t.slice(1).toUpperCase()).join(", ")}.`; continue; }
      if (f.size > MAX_MB * 1e6) { item.status = "error"; item.message = `Files up to ${MAX_MB} MB, please.`; continue; }
      upload(key, item, f);
    }
    draw(key);
  }

  function base64(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(",")[1] || "");
      r.onerror = () => reject(r.error);
      r.readAsDataURL(file);
    });
  }

  async function upload(key, item, file) {
    const box = boxes[key];
    try {
      const data = await base64(file);
      const r = await api("/api/upload", { section: sectionOf(box), name: file.name, data_base64: data }, 180000);
      Object.assign(item, { id: r.id, kind: r.kind, status: r.status, message: r.message });
      draw(key);
      for (let n = 0; item.status === "reading" && n < 400 && box.items.includes(item); n++) {   // up to ~10 minutes (long audio)
        await sleep(n < 10 ? 800 : 1500);
        const s = await api(`/api/plugins/attachments/${item.id}`, undefined, 20000);
        Object.assign(item, { status: s.status, message: s.message, parts: s.parts, chars: s.chars });
        draw(key);
      }
    } catch (e) {
      item.status = "error";
      item.message = errorText(e);
    }
    draw(key);
  }

  function label(item) {
    if (item.status === "uploading") return "Uploading...";
    if (item.status === "reading") return item.kind === "audio" ? "Listening..." : item.kind === "image" ? "Reading the picture..." : "Reading...";
    if (item.status === "error") return item.message || "Couldn't read it.";
    const unit = { pdf: "page", pptx: "slide", xlsx: "sheet", audio: "minute" }[item.kind];
    return unit && item.parts ? `Ready · ${item.parts} ${unit}${item.parts === 1 ? "" : "s"}` : "Ready";
  }

  function draw(key) {
    const box = boxes[key];
    box.chips.replaceChildren(...box.items.map((item) => {
      const remove = el("button", { type: "button", class: "chip-x", "aria-label": `Remove ${item.name}`, title: "Remove" }, icon("close"));
      remove.addEventListener("click", () => { box.items = box.items.filter((x) => x !== item); draw(key); });
      const look = el("button", { type: "button", class: "chip-name", title: item.id ? "Preview what AIRTAC read" : item.name, text: item.name });
      look.disabled = !item.id;
      look.addEventListener("click", () => preview(item));
      return el("span", { class: `attach-chip ${item.status}` }, icon(KIND_ICON[item.kind] || "clip"),
        el("span", { class: "chip-words" }, look, el("small", { text: label(item) })), remove);
    }));
    box.chips.hidden = !box.items.length;
  }

  async function preview(item) {
    dTitle.textContent = item.name;
    dBody.replaceChildren(el("p", { class: "muted", text: "Loading..." }));
    dialog.showModal();
    try {
      const r = await api(`/api/plugins/attachments/${item.id}`, undefined, 20000);
      const parts = [];
      if (r.kind === "image") parts.push(el("img", { src: `/api/plugins/attachments/${item.id}/file`, alt: item.name, class: "attach-img" }));
      parts.push(el("p", { class: "muted small", text: r.status === "ready" ? `What AIRTAC read (${r.chars.toLocaleString()} characters):` : label(r) }));
      if (r.text) parts.push(el("pre", { class: "attach-text", text: r.text + (r.chars > 4000 ? "\n..." : "") }));
      dBody.replaceChildren(...parts);
    } catch (e) { dBody.replaceChildren(el("p", { class: "muted", text: errorText(e) })); }
  }

  return {
    mount,
    // ids to send with the message (files that failed are left out; the chip says why)
    ids: (key) => (boxes[key]?.items || []).filter((i) => i.id && i.status !== "error").map((i) => i.id),
    // a file still uploading or being read: the message waits for it
    waiting: (key) => (boxes[key]?.items || []).filter((i) => i.status === "uploading" || i.status === "reading").map((i) => i.name),
    clear: (key) => { if (boxes[key]) { boxes[key].items = []; draw(key); } },
  };
})();

// Where the paperclip goes: Main chat, every app's chat (the section is the app), Research's co-pilot.
Attach.mount("main", { form: $("ask"), section: "chat", zone: $("view-home"), paste: [$("text")], before: $("mic") });
Attach.mount("app", { form: $("chat-form"), section: () => (typeof currentApp === "string" ? currentApp : "app"), zone: document.querySelector("#view-app .chat-panel"), paste: [$("chat-text")], before: $("chat-mic") });
Attach.mount("research", { form: $("rs-ask"), section: "research", zone: $("view-research"), paste: [$("rs-q"), $("rs-body")] });
