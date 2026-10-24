# Airtac Mobile (Android app) and pairing

Airtac Mobile is a small Flutter app for your Redmi Note 9 Pro Max. It talks only to AIRTAC on your PC, over
HTTPS. Its native screens are Chat, Today, Learn, Schedule, AIRTAC 1, Apps and Search; **Full app** opens every
other page of the PC's web app inside the app. Your passwords and API keys stay on the PC: the phone never gets them.

The PWA still works without any of this: in Chrome on the phone, open AIRTAC (over Tailscale, see
[PHONE_AND_PC.md](PHONE_AND_PC.md)), then menu > **Add to Home screen**.

## 1. Build the APK (once, then after each change to `mobile/`)

The i3 PC is too small for Flutter and Android Studio, so the APK is built on GitHub's servers.

1. Once: copy `mobile\ci\build_apk.yml` to `.github\workflows\build_apk.yml` (Claude's tools can't write into
   `.github`). In PowerShell, in `F:\AIRTAC Advance`:
   `New-Item -ItemType Directory -Force .github\workflows; Copy-Item mobile\ci\build_apk.yml .github\workflows\`
   Then push the repository yourself (AIRTAC never pushes).
2. On github.com: your repo > **Actions** > **Build Airtac Mobile APK** > **Run workflow**. It takes about
   10-15 minutes and runs `flutter analyze` and `flutter test` before building.
3. Open the finished run > **Artifacts** > `airtac-mobile-apk`. Unzip it: `airtac.apk` and `airtac.apk.sha256`.
4. Put `airtac.apk` in `F:\AIRTAC Advance\mobile\release\`. The **Get mobile app** dialog then shows its size and
   SHA-256 and serves it to the phone.

**First build:** this app was written without a Flutter compiler (none could be installed where it was written),
so the first run may stop at `flutter analyze` with a few errors. Copy the red lines from the run's log and paste
them to Claude; they are usually one-line fixes.

### A signing key that stays the same (recommended)
Without one, every build is signed with a new throwaway key, and Android refuses to install it over the old app
(uninstall first, which also forgets the pairing). Make a key once, with the JDK's `keytool` (on the new laptop,
or anywhere Java is installed). Keep the `.jks` file and its passwords safe and out of git.

```
keytool -genkeypair -v -keystore airtac-release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias airtac
```

Then add these repository secrets (Settings > Secrets and variables > Actions > New repository secret):

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | in PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("airtac-release.jks"))` |
| `ANDROID_KEYSTORE_PASSWORD` | the keystore password |
| `ANDROID_KEY_ALIAS` | `airtac` |
| `ANDROID_KEY_PASSWORD` | the key password |

### Building on the new laptop instead (Windows, PowerShell)
Install Flutter 3.47.7 (stable) and Android Studio's SDK on a drive with space, then:

```
cd "F:\AIRTAC Advance\mobile"
flutter create --platforms=android --org com.airtac --project-name airtac_mobile .
Remove-Item -Recurse -Force android\app\src\main\kotlin
Copy-Item -Recurse -Force android_overrides\* android\
flutter pub get
flutter analyze --no-fatal-infos
flutter test
flutter build apk --release
Copy-Item build\app\outputs\flutter-apk\app-release.apk release\airtac.apk
Get-FileHash release\airtac.apk -Algorithm SHA256
```

For your own key there, create `mobile\android\key.properties` (never commit it):
`storeFile=airtac-release.jks`, `storePassword=...`, `keyAlias=airtac`, `keyPassword=...`, with the `.jks` file
in `mobile\android\`.

## 2. Install it on the phone
1. On the PC: Main chat > **Get mobile app** > **Pair a phone** (this switches on phone access on Wi-Fi).
   The first time, Windows asks whether Python may use the network: tick **Private networks** only.
2. On the phone (same Wi-Fi), open the link the dialog shows:
   `https://<PC's Wi-Fi address>:8766/download/airtac.apk`. Chrome warns about the certificate once (it's AIRTAC's
   own): Advanced > Proceed. Or copy the APK over USB.
3. Check the SHA-256 if you like (a file manager or `CertUtil -hashfile airtac.apk SHA256` on the PC).
4. Android asks to allow installs from Chrome or the file manager: allow it for this one install. Play Protect may
   say it doesn't know the app: More details > Install anyway.

## 3. Pair
1. Open Airtac, give the phone a name, press **Scan the QR code** and scan the code in the **Get mobile app**
   dialog. It's valid for 2 minutes and works once.
2. The app keeps the PC's certificate fingerprint and its own device token (in Android Keystore). From then on it
   trusts only that certificate, so nobody on the Wi-Fi can pretend to be your PC.
3. **Settings > Devices** on the PC lists paired phones, with **Remove**. Removing works instantly.

**Away from home:** install Tailscale on the PC and the phone, then in the app: More > This phone > add the PC's
Tailscale address (100.x.y.z). The app tries Wi-Fi first, then Tailscale. Never forward a port on your router.

**When the PC is signed in to Google** (Settings > Account), pairing also asks for Google sign-in on the phone, and
it must be the same account (compared by Google's account ID). That needs, in the same Google Cloud project as
[GOOGLE_SETUP.md](GOOGLE_SETUP.md):
1. An **Android** OAuth client: package `com.airtac.mobile`, and the SHA-1 of your signing key
   (`keytool -list -v -keystore airtac-release.jks -alias airtac`, the "SHA1:" line).
2. A **Web application** OAuth client. Its client ID is the `serverClientId` the app asks Google for (Google's
   Android sign-in needs the web client's ID). Add it as the `GOOGLE_SERVER_CLIENT_ID` repository secret, and on the
   PC in `config\airtac.toml`:
   ```
   [account]
   android_server_client_id = "<the web client ID>.apps.googleusercontent.com"
   ```
   Client IDs are not secret; the web client's secret is never needed.

## How it's protected
- HTTPS with a self-signed certificate made on the PC (`data\tls\`, the key readable by you only), pinned by its
  SHA-256 in the app; the app's HTTP client trusts no public certificate authority at all.
- One-time pairing code (2 minutes, single use, 5 wrong codes per address then a 10-minute wait); the device token is
  random and only its SHA-256 is stored on the PC.
- The phone listener (port 8766) runs only while **Phone access on Wi-Fi** is on, and answers only to private and
  Tailscale addresses (not to web pages pointing a domain at your PC). The main port stays localhost only.
- Paired phones have the same request limits as other devices. The vault, API keys, Dev tools, plugin switches and
  pairing itself are PC-only, even for a paired phone.
- The app can ask for your fingerprint each time it opens (More > This phone).

## What's where
| Path | What |
|---|---|
| `airtac/pairing.py` | codes, device tokens, certificate, the Wi-Fi listener |
| `airtac/main.py` | `/api/pair/*`, `/api/devices*`, `/download/airtac.apk` |
| `web/devices.js` | the Get mobile app dialog and Settings > Devices |
| `mobile/lib/` | the Flutter app (`src/api.dart` is the one API client) |
| `mobile/android_overrides/` | Android manifest, Gradle and MainActivity copied over `flutter create`'s output |
| `mobile/ci/build_apk.yml` | the build (copy it to `.github/workflows/`) |

## Troubleshooting
- **Can't reach your PC:** AIRTAC running? Settings > Devices > Phone access on Wi-Fi on? Same Wi-Fi? Windows
  firewall: allow Python on Private networks (Windows Security > Firewall > Allow an app).
- **Certificate doesn't match:** the PC's `data\tls\` was replaced (a reinstall or a new PC). Remove the phone on
  the PC, unpair in the app, and pair again.
- **Port 8766 busy:** set `[devices] port = 8767` in `config\airtac.toml`, restart AIRTAC, and pair again.
- **Reminders don't ring:** allow notifications for Airtac, and turn off battery optimisation for it (MIUI:
  Settings > Apps > Airtac > Battery saver > No restrictions).
