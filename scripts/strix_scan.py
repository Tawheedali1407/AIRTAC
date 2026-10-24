"""Scan AIRTAC on this PC with Strix (an open-source AI security agent), to find weaknesses.

Strix drives a sandbox (Docker) that probes a running web app for common web vulnerabilities (injection,
auth problems, CSRF, SSRF, and so on). This helper points it at AIRTAC on your own machine.

Before running:
1. Install Strix from the Dev tools page (or: pipx install strix-agent). Docker must be running.
2. Set a model and key (Strix calls an LLM to do the testing), e.g. in PowerShell:
       $env:STRIX_LLM = "openrouter/z-ai/glm-4.6"
       $env:LLM_API_KEY = "<your key>"
3. Start AIRTAC (AIRTAC-Advance.cmd). If you set a passcode, pass it so Strix can sign in:
       python scripts/strix_scan.py --passcode <your passcode>

Only scan AIRTAC on your own PC. Scanning a site you don't own may be illegal. The report is saved under
strix_runs/ in this folder; read it, then fix what it finds (or ask AIRTAC to help).
"""
import argparse
import os
import shutil
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan AIRTAC on this PC with Strix.")
    ap.add_argument("--port", default=os.environ.get("AIRTAC_PORT", "8765"))
    ap.add_argument("--passcode", default="", help="your AIRTAC passcode, if you set one (so Strix can sign in)")
    args = ap.parse_args()

    strix = shutil.which("strix")
    if not strix:
        print("Strix isn't installed. Install it from AIRTAC's Dev tools page, or run: pipx install strix-agent")
        return 1
    if not os.environ.get("LLM_API_KEY"):
        print("Set an LLM key first:  set LLM_API_KEY=...   and a model:  set STRIX_LLM=openrouter/z-ai/glm-4.6")
        return 1

    target = f"http://localhost:{args.port}"
    instruction = ("Security-test this personal assistant web app running locally. Check the login/passcode flow, "
                   "CSRF and trusted-host protections, the connection/key routes, the password vault routes, and "
                   "the dev-tools setup routes. Try to reach them from a non-local origin. Do not attempt to break "
                   "the host machine; stay within the web app.")
    if args.passcode:
        instruction += f" The app passcode for signing in as an allowed device is: {args.passcode}"

    cmd = [strix, "--target", target, "--instruction", instruction]
    print("Running:", " ".join(cmd[:3]), "... (this can take a while; Docker pulls the sandbox on first run)")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
