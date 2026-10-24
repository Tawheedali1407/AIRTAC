# Security: what AIRTAC Advance protects, and what it doesn't

It's one person's assistant on one PC. These are the threats it is built for, said plainly.

## What it protects

| Threat | Protection |
|---|---|
| A web page or document tricks the model into doing something | `write` and `device` tools (create issue, open app, upload backup) never run without your Confirm. The confirm box shows every argument in full. Work mode stops at those steps too. |
| Another website in your browser talks to AIRTAC (DNS rebinding) | Only host names in `[server] trusted_hosts` are answered. |
| Someone on your Wi-Fi or tailnet opens AIRTAC | The server listens only on 127.0.0.1 by default. Any address other than this PC needs the passcode. Sessions are random tokens (only their hash is stored); cookies are HttpOnly and SameSite=Strict, and Secure over https. After 5 wrong passcodes, it waits 10 minutes. |
| The reader is used to reach your router or this PC (SSRF) | The reader only opens public addresses. It checks every redirect, refuses user:password links, refuses downloads, and caps pages at 2 MB. |
| A plugin quietly sends data somewhere | `fetch()` refuses any site not in the plugin's manifest, and so does the backend's own guard on every httpx request a plugin's code makes (`plugin_api.guard_http`). The Plugins page shows each plugin's sites and risk. |
| A web page (or a sandboxed frame, which sends `Origin: null`) posts to AIRTAC through your browser | Every `/api/` call needs this install's random token, a SameSite=Strict, HttpOnly cookie the page gets when it loads (`data/.install_token`). Another site can't send it. Scripts on this PC send it as `X-AIRTAC-Token`. |
| Keys leak | API keys live in the vault (`data/vault.bin`, AES-256-GCM), sealed to your Windows account so brains work while it's locked; never config, the database, backups or the portable zip. Seeing the APIs and keys page, saving, Show and Copy need the vault unlocked, on this PC only. Keys show as dots (the last 4 only while unlocked); Show lasts 10 s; Copy uses the PC's clipboard, cleared after 20 s. Each Show and Copy is rate-limited and written to `data/logs/audit.log` (never the value). Every log line goes through a filter that hides anything shaped like a key (`airtac/logsafe.py`). Text that looks like a key is refused by Remember. |
| A file path trick (`../.env`) | `/files/` only serves files inside `outputs/` and only known file types. Music only plays files it indexed itself. |
| Medical answers taken as diagnosis | The medicine brain gets a safety prompt (emergency number 112, no personal doses), and every medical answer is labelled as not medical advice. |

## What it does NOT protect
- **Plugins are not sandboxed.** A plugin is Python code with your user's rights. The permission list catches mistakes and shows intent, but a malicious plugin could ignore it. Only install plugins you've read.
- **DNS can change between the check and the request** in the reader (a small race). The reader also never sends your cookies or keys.
- **Anyone using your Windows account** can read `data/`, `.env` and your library. Use a Windows password and BitLocker.
- **The browser's own speech recognition** (the default) sends audio to Google or Microsoft. Set `[voice] stt = "local"` to keep it on the PC.
- **Cloud backup** sends your data to your cloud drive. Use an rclone **crypt** remote so the provider only sees encrypted files.
- **Models can be wrong.** Check important answers, especially medicine, law and money.

## Accounts (LeetCode, CodeChef, GeeksforGeeks, EduSkills)
- **Logins live in Chrome.** Sign up and Log in open in Google Chrome with AIRTAC's own profile
  (`data\chrome_profile`). Chrome keeps the sessions and encrypts its cookies with your Windows sign-in. The
  folder is never in AIRTAC's backups or the portable zip. Anyone who can use your Windows account can use those logins.
- **The vault is optional.** A password or session cookie you type on the Accounts page goes to Windows
  Credential Manager (the `keyring` package), not to AIRTAC's folder or database. AIRTAC's database keeps only
  which site has something saved, and when.
- **Where a secret may go:** to the site it belongs to (LeetCode's cookie only to leetcode.com), and back to your
  screen on this PC when you press Show. Never to the AI model (no tool takes or returns one), the chat history,
  a log line, a backup or another device. The vault routes refuse any request that isn't from this PC.
- **"This PC" is checked properly.** A request counts as local only when the address says localhost and the
  connection really comes from this machine, so another device can't fake its way past the passcode.
- **Session cookies and site terms.** A cookie lets anyone who holds it act as you on that site, which is why it
  stays in the vault. Sites' terms may not allow automated requests, so AIRTAC sends one request per site only
  when you press Refresh. CodeChef and GeeksforGeeks pages are read best effort; if a page changes, AIRTAC says so
  rather than guessing a number.

## Free cloud brains ([failover])
- With no keys connected, nothing leaves the PC. With a key, that service sees your question and the context sent
  with it (recent chat, matching notes or library passages). Disconnect a key to keep that service out.
- Google AI Studio's free tier may use what you send for training and human review, so it isn't in the default order.
  Add it to `[failover] order` yourself (with `local_first = true`, it answers only when your PC's model can't).

## The vault (v2, Part D, 17 Oct 2026)
**Threat model in one line:** it protects your logins and keys against **file theft** (a copied `data\` folder,
a lost backup, a stolen disk) and **casual access** (someone at your unlocked PC opening the page). It
**cannot** protect against **malware running as you while the vault is unlocked**: such a program can read
what you can.
- One vault, `data/vault.bin`, for logins and API keys. **Super password** (at least 12 characters) ->
  Argon2id (64 MiB, tuned to about 0.7 s on this PC) -> a key that wraps two random 256-bit data keys
  (envelope). Every entry is AES-256-GCM with its own random nonce; the file header (format, version, id)
  and each key slot's Argon2 settings are authenticated, and an encrypted list of entries catches one being
  added or removed. Any change made outside AIRTAC is refused as tampering.
- A **recovery key** (160 random bits) is a second slot, shown once with "I saved it". Forgetting both the
  password and the recovery key means the data is lost.
- **Locked by default.** Locks after 5 minutes idle, when the window is hidden, and with Lock. 5 wrong tries,
  then 30 s, doubling to 1 hour; the try counter is signed with a key sealed to this PC, so editing the file
  doesn't reset it. Comparisons are constant-time.
- API keys use the second data key, sealed with Windows DPAPI to your account, so cloud brains work while the
  vault is locked; viewing or changing them still needs the super password.
- Writes are atomic with a `.bak`; the file is readable by your Windows user only (icacls).
- Migration from the old vault (scrypt + Fernet, `data/vault.db`) happens on the first unlock with the old
  master passcode, after `data\` is backed up; the old file is kept in `data/backups/`.
- Never sent to the model, the chat, a log, an error message, a URL, a PDF, an export or git. The vault
  plugin has no tools. See [PASSWORDS.md](PASSWORDS.md).

## Paired phones (Part G, 18 Oct 2026)
- A second listener, **HTTPS on port 8766**, runs only while Settings > Devices > Phone access on Wi-Fi is on. It
  answers only to private and Tailscale IP addresses or this PC's own name (a web page pointing its domain at your
  PC is refused). The main port stays on 127.0.0.1.
- The certificate is self-signed (EC P-256, made once in `data/tls/`, key readable by you only). The app pins its
  SHA-256 from the QR code and trusts no certificate authority, so a look-alike server on the Wi-Fi is refused.
- Pairing codes: random, 2 minutes, single use, 5 wrong codes per address then 10 minutes. Device tokens are random
  (256-bit); only their SHA-256 is stored; Remove takes effect on the next request. If this PC is signed in to Google,
  the phone must sign in with the same account (Google ID token checked: signature, audience, expiry, nonce tied
  to the pairing code).
- A paired phone is a signed-in remote device: the same request limits, and no vault, API keys, Dev tools, plugin
  switches or pairing (those stay `local_only`). The web view gets HttpOnly, Secure, SameSite=Strict cookies once,
  so the token never appears in a URL.
- **Not protected:** someone holding your unlocked phone with the app open (use the app's fingerprint lock), and
  malware on the phone.

## No code from you or the model is executed
AIRTAC has no "run this code" feature: no `eval`/`exec` of text, no shell built from a request. Dev tools run
fixed commands chosen by name. SQL always uses `?` parameters (column and table names come from AIRTAC's own
code). If a code runner is ever added, it must run in a sandbox with no network and with time and memory limits.

## Checking it yourself: scripts/security_check.py
`.venv\Scripts\python scripts\security_check.py` runs pip-audit (known-vulnerable packages), bandit (risky
code patterns, medium and up) and gitleaks (secrets in git history or the folder; `.env` and `data\` are
skipped because they hold your real keys on purpose). Put gitleaks.exe (8.30.1) in `tools\gitleaks\`.
The tests run the gitleaks scan too, and skip it with that hint when gitleaks is missing.

## Reading the web (0.4)
- The Agent Reach reader opens only **public** addresses. Every local or private-network address is
  refused — localhost, `.local`/`.internal` names, and any IP in a private, loopback, link-local or
  reserved range (IPv4 and IPv6) — and host names are resolved first, so this can't be used to reach
  things inside your network (SSRF).

## Dev tools (0.4)
- The Dev tools page installs software on the PC. Each setup command is **fixed in AIRTAC's code** and
  chosen by a whitelisted name, never taken from the request, and the routes run only on this PC.
- The MCP bridge (`scripts/mcp_server.py`) that links AIRTAC to Claude Code only ever talks to
  `127.0.0.1`, takes a digits-only port, and exposes only read-style tools (`ask_airtac`, `airtac_read`).

## Errors never echo input (0.4)
- A request that doesn't fit gets a short "check these fields" message. AIRTAC never sends back what you
  typed, so a mistyped password or key can't leak in an error.

## Scanning yourself with Strix (0.4)
- `scripts/strix_scan.py` runs the open-source Strix agent against AIRTAC on your own PC to find
  weaknesses. Only scan AIRTAC on a machine you own.

## Security review 0.4.1 (13 Oct 2026)
A hardening pass (Strix is wired into the Dev tools page for a full scan on your own PC, with Docker and
an LLM key). Two real issues were found by reviewing the code and fixed, each with a test:

- **SSRF: loopback in disguise.** The reader's guard let through numeric and short IP forms that resolve
  to loopback — `http://2130706433/`, `http://127.1/`, `http://0x7f.0.0.1/` — and IPv4-mapped IPv6
  (`::ffff:127.0.0.1`). It now parses a literal IP directly and **resolves everything else before
  judging it**, and judges the inner IPv4 of a mapped address. A numeric host that can't be resolved is
  blocked rather than allowed. This matters most for the Agent Reach CLI, which reads on the PC itself.
- **Login flood.** `/api/login` is now covered by the per-device rate limit as well as the existing
  5-wrong-tries-then-10-minutes lock-out, so a remote device can't hammer the passcode check.

Static scans: **pip-audit** finds no known vulnerable packages. **Bandit** reports only the previously
documented, guarded items (SQL built from fixed column-name lists with parameterised values; the arXiv
XML parse, which has the XML-bomb guard) plus false positives (a `max_completion_tokens` argument name
and the vault's length/limit constants read as "passwords"). The vault's own crypto code is clean.
