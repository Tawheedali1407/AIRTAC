// Page layout on this device (Ali, 18 Oct): "Fit to screen" (the phone layout, the default) or "Desktop layout"
// (the full PC layout at 1280 px: scroll sideways and pinch to zoom). Runs in <head>, before the page is drawn,
// so the choice applies from the first frame. Kept per device in localStorage; the app works the same without it.
(function () {
  var KEY = "airtac-view";
  var mode = "fit";
  try { mode = localStorage.getItem(KEY) === "desktop" ? "desktop" : "fit"; } catch (e) { /* private window: fit */ }

  function apply(m) {
    var meta = document.querySelector('meta[name="viewport"]');
    if (meta) meta.setAttribute("content", m === "desktop" ? "width=1280" : "width=device-width, initial-scale=1");
    document.documentElement.setAttribute("data-page-view", m);   // not "data-view": that marks menu links
    mode = m;
    var b = document.getElementById("view-toggle");
    if (b) b.lastChild.textContent = m === "desktop" ? "Phone view (fit to screen)" : "Desktop view (scroll sideways)";
    var r = document.querySelector('input[name="page-view"][value="' + m + '"]');
    if (r) r.checked = true;
  }

  window.airtacSetView = function (m) {
    try { localStorage.setItem(KEY, m); } catch (e) { /* not kept, still applied */ }
    apply(m);
    window.scrollTo(0, 0);
  };

  apply(mode);

  // Accent colour (Ali, 18 Oct: "colours are limited"): any colour over the theme, kept per device.
  function hex(c, a) { var n = parseInt(c.slice(1), 16); return "rgba(" + (n >> 16) + ", " + ((n >> 8) & 255) + ", " + (n & 255) + ", " + a + ")"; }
  function setAccent(c) {
    var st = document.documentElement.style;
    if (!c) { ["--accent", "--accent-ink", "--accent-soft"].forEach(function (k) { st.removeProperty(k); }); return; }
    st.setProperty("--accent", c); st.setProperty("--accent-ink", c); st.setProperty("--accent-soft", hex(c, 0.15));
  }
  var accent = null;
  try { accent = localStorage.getItem("airtac-accent"); } catch (e) { /* none */ }
  if (accent && /^#[0-9a-f]{6}$/i.test(accent)) setAccent(accent);
  document.addEventListener("DOMContentLoaded", function () {
    var pick = document.getElementById("accent-pick");
    if (pick) {
      if (accent) pick.value = accent;
      pick.addEventListener("input", function () { setAccent(pick.value); try { localStorage.setItem("airtac-accent", pick.value); } catch (e) { /* not kept */ } });
      document.getElementById("accent-reset").addEventListener("click", function () { setAccent(null); try { localStorage.removeItem("airtac-accent"); } catch (e) { /* fine */ } });
    }
  });
  document.addEventListener("DOMContentLoaded", function () {
    apply(mode);
    var b = document.getElementById("view-toggle");
    if (b) b.addEventListener("click", function () { window.airtacSetView(mode === "desktop" ? "fit" : "desktop"); });
    var radios = document.querySelectorAll('input[name="page-view"]');
    for (var i = 0; i < radios.length; i++) {
      radios[i].addEventListener("change", function (ev) { window.airtacSetView(ev.target.value); });
    }
  });
})();
