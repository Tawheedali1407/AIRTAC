# Use AIRTAC Advance on your phone and other PCs

## Another PC of yours
1. On this PC, run `scripts\make_portable.ps1`. It puts `AIRTAC-Advance-<date>.zip` on your desktop, with no data, no keys and no models.
2. Copy the zip to the other PC, unzip it anywhere and double-click `Install-AIRTAC.cmd`.

Each PC then has its own AIRTAC and its own memory. To carry memory across:
- back up on one (`Back up my data`),
- copy `data/airtac.db` out of the zip into the other's `data/`,
- or sync the `data` folder with **Syncthing** (open source, PC to PC, no cloud). Only sync it while AIRTAC is closed on the other PC.

## Your phone (reach your PC from anywhere, privately)
Use **Tailscale**: it links your devices into a private network, so nothing is opened to the internet.
1. Install Tailscale on the PC and the phone, and sign in with the same account.
2. On the PC, run `tailscale serve --bg 8765`. It gives an https address like `https://ali-pc.tail1234.ts.net`.
3. In `config/airtac.toml`, add that name to `trusted_hosts`:
   `trusted_hosts = ["localhost", "127.0.0.1", "[::1]", "ali-pc.tail1234.ts.net"]`
4. On the PC, open **Connections** in the menu and set a long passcode under **Phone and other devices** (at least 8 characters). It works at once, no restart.
5. On the phone:
   - open the https address and sign in with the passcode;
   - install it: open AIRTAC's menu and choose **Install app** (Chrome on Android installs it at once; on an iPhone it shows the Safari steps: Share > **Add to Home Screen**).
   - On this PC the same menu item installs AIRTAC in Chrome or Edge, in its own window.

The PC must be on, with AIRTAC running. With the PC off, the app opens and says it can't reach your PC.

Why not just Wi-Fi? A phone browser only lets a page use the microphone and install as an app over https. Tailscale gives you https without any certificates to manage.
