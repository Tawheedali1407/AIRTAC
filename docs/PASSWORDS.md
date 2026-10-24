# The vault: passwords and API keys

One vault on the PC running AIRTAC, for your logins (site, user name, password, notes) and your API keys.
Passwords are on the **Passwords** page; keys on **APIs and keys** (Settings links to both).

## Your super password
- Created the first time you open Passwords or APIs and keys: typed twice, at least 12 characters, with a
  strength meter. A few random words is strong and easy to remember. Upgrading from 0.4: your old master
  passcode becomes the super password (type it once; the vault moves to the new format).
- A **recovery key** is shown once when you create it. Write it down (not on this PC) and tick "I saved it".
  **If you forget the super password AND lose the recovery key, the vault cannot be opened by anyone.**
- "Forgot it? Use your recovery key" sets a new super password. "New recovery key" replaces the old one.
- "Change super password" asks for the current one only when a vault exists.

## What protects it
- `data/vault.bin`: Argon2id (64 MiB, about 0.7 s per try on this PC) turns the password into a key that
  unlocks two random data keys; each entry is AES-256-GCM. Any change made outside AIRTAC is refused.
- **Locked by default**; locks after 5 minutes idle, when the window is hidden, and with Lock.
- 5 wrong tries, then a wait of 30 s that doubles each time, up to 1 hour (survives restarts).
- Masked: Show reveals for 10 seconds; Copy puts it on the PC's clipboard and clears it after 20 seconds,
  kept out of Windows clipboard history. Each Show and Copy is written to `data/logs/audit.log` (never the value).
- API keys are also sealed to your Windows account, so brains keep working while the vault is locked.
- Never sent to the AI model, the chat, a log, an error, a URL, a PDF, a backup or git. PC only.
- **Encrypted backup:** "Export encrypted backup" writes a `.airtacvault` file (in `outputs/vault-backups/`)
  that opens only with your super password.

## What it cannot protect against
- Malware running as you **while the vault is unlocked**, and someone watching you type the password.

## "Sign in with saved login" (Social and AI pages)
It copies your user name and opens the sign-in page; the next press copies the password (cleared after
20 seconds). AIRTAC never types a password into a website. If the vault is locked, unlock it first.
