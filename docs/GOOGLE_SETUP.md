# Google sign-in: one-time setup (about 10 minutes)

AIRTAC signs in with **your own** Google Cloud client, so no one else's app ever sees your account.
Use your personal Google account (never a company account). Google's console changes its menus now and
then; if a name below differs, look for the same idea.

1. Open <https://console.cloud.google.com/> and create a project, e.g. **AIRTAC**.
2. Open **Google Auth Platform** (it used to be called "OAuth consent screen") and press **Get started**:
   - App name: AIRTAC; support email: your Gmail.
   - Audience: **External**.
   - Contact email: your Gmail. Agree and create.
3. **Audience** page: under Test users, **Add users** and add your own Gmail.
4. **Clients** page: **Create client** → Application type **Desktop app** → name "AIRTAC on my PC" → Create.
   Copy the **Client ID** (ends in `.apps.googleusercontent.com`) and the **Client secret**.
   (For a desktop app Google doesn't treat the secret as truly secret, but AIRTAC still keeps it in the vault.)
5. In AIRTAC: **APIs and keys** → **Google sign-in (your own client)** → paste both → Save.
6. **Settings → Account → Sign in with Google.** A Google page opens in your browser; choose your account.
   Google may say "Google hasn't verified this app": that's your own app, press **Continue**.
7. Later, only when you want them: **Switch on Drive sync** (Drive's hidden app-data folder: AIRTAC sees
   only its own files, not the rest of your Drive) and **Switch on Google Calendar** (read-only).

## Connectors: Gmail, Google Calendar, Google Drive (Menu > Connectors)
1. In the same Google Cloud project: **APIs & Services → Library**, and **Enable** each one you want:
   **Gmail API**, **Google Calendar API**, **Google Drive API**.
2. In AIRTAC: **Connectors → Connect** on Gmail, Google Calendar or Drive. Google asks only for that app's
   permission: Gmail read + drafts, Calendar read + add events, Drive read. Press **Continue** on the
   "unverified app" page (it's your own app).
3. Gmail and Drive read permissions are what Google calls "restricted": fine for your own app and your own
   account; you'd need Google's review only to give the app to other people.

## The 7-day catch
While the project's publishing status is **Testing**, Google's refresh tokens **expire after 7 days**
(Google's own docs). Then AIRTAC shows "Sign in again"; nothing breaks. To stop that, go to
**Audience → Publish app** ("In production"). For personal use you don't need Google's review; you'll
just keep seeing the "unverified app" warning when you sign in.

## What AIRTAC stores
- In the vault (encrypted): the client secret and the refresh token.
- In AIRTAC's database: your Google account id (`sub`), email and name, and which permissions you gave.
- Never in a file, a log, a URL or the page. **Sign out** revokes Google's access and deletes the token.

## Later: the phone app (Airtac Mobile)
Only needed if this PC is signed in to Google and you pair a phone: add an **Android** client (package
`com.airtac.mobile` + your signing key's SHA-1) and a **Web application** client in this same project. The steps
are in [MOBILE.md](MOBILE.md#3-pair).
