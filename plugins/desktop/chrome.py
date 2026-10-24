"""Google Chrome with AIRTAC's own profile, so your sign-ins stay put.

Chrome keeps cookies, saved sessions and site logins in a "user data directory". AIRTAC opens
Chrome with its own one, data/chrome_profile in the AIRTAC folder, so once you log in to LeetCode,
CodeChef, GeeksforGeeks, EduSkills, YouTube or Instagram there, you stay logged in every time
AIRTAC opens them. It is separate from your everyday Chrome profile, and Chrome encrypts its
cookies with your Windows sign-in. The folder is never put in AIRTAC's backups or portable zip.
"""
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from airtac import config

WINDOWS_PATHS = [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                 r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                 r"%LocalAppData%\Google\Chrome\Application\chrome.exe"]


def profile_dir() -> Path:
    return config.DATA_DIR / "chrome_profile"


def find_chrome(setting: str = "") -> str | None:
    """chrome.exe from the setting, the usual Windows places, or the PATH."""
    candidates = [setting] if setting else []
    if sys.platform == "win32":
        candidates += [os.path.expandvars(p) for p in WINDOWS_PATHS]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    for name in ("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def command(url: str, chrome: str) -> list[str]:
    folder = profile_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return [chrome, f"--user-data-dir={folder}", "--profile-directory=Default", "--no-first-run",
            "--no-default-browser-check", url]


def open_url(url: str, setting: str = "", use_profile: bool = True) -> str:
    """Open a web address. Returns "chrome" (AIRTAC's profile) or "browser" (your default browser)."""
    if not url.startswith(("https://", "http://")):
        raise ValueError("Only web addresses can be opened.")
    chrome = find_chrome(setting) if use_profile else None
    if chrome:
        subprocess.Popen(command(url, chrome), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "chrome"
    webbrowser.open(url)
    return "browser"
