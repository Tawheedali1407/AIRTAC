# Moving AIRTAC to a new PC (checklist)

On the **old PC** first:
- [ ] Settings → Account → **Upload to Drive now** (your vault file, already encrypted with your super
      password, and your settings, encrypted with a key from the vault). Note the "last sync" time.
- [ ] Make sure you know your **super password**, or have your **recovery key**. Without one of them
      the vault can't be opened on any PC. Google sign-in does not replace them.

On the **new PC**:
- [ ] Install AIRTAC Advance (the installer, or copy the code; not `data\`).
- [ ] Install Ollama and pull your models again (`scripts\models.ps1`): models aren't synced.
- [ ] Make your Google client again, or copy the same Client ID and secret (docs/GOOGLE_SETUP.md, step 5).
      It goes in **APIs and keys**. (The new PC has no vault yet: AIRTAC asks you to create one first. Make a
      temporary one; Restore replaces it, and the temporary one is kept in data\backups.)
- [ ] Settings → Account → **Sign in with Google** (the same account; any other is refused).
- [ ] **Switch on Drive sync** → **Restore from Drive**. The current files are backed up first.
- [ ] Open **Passwords** → type your **super password** (or "Use your recovery key").
- [ ] Settings → Account → **Restore settings (after unlocking)**: themes, app list, your learning plan,
      AIRTAC's config file and Your day settings come back.
- [ ] Restart AIRTAC (`scripts\stop.ps1`, then `start_airtac.bat`).
- [ ] Add your library again (Knowledge) and re-import repositories if you want them.
- [ ] Check: chats answer, APIs and keys show "saved", Passwords lists your logins.
- [ ] **Phone:** a new PC makes a new HTTPS certificate, so Airtac Mobile says the certificate doesn't match. In the app:
  More > This phone > Unpair, then pair again from Get mobile app (docs/MOBILE.md).
