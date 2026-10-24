// The passcode page for other devices (see airtac/auth.py). A separate file, so the page's
// security policy can forbid inline scripts.
try { document.documentElement.dataset.skin = localStorage.getItem("airtac-theme") || "crimson"; } catch { /* default */ }
document.getElementById("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("error");
  error.textContent = "";
  try {
    const res = await fetch("api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode: document.getElementById("code").value }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "That didn't work.");
    location.href = "./";
  } catch (e) {
    error.textContent = e.message;
  }
});
