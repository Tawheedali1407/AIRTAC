"""Agent Reach: let AIRTAC read what's on a public page, a tweet, a Reddit thread or a YouTube video.

Two layers, so it works with nothing to set up and gets stronger if you want it to:

1. Always on: any public web address is read through Jina Reader (https://r.jina.ai), a free service that
   turns a page into clean text (no key). That already covers articles, single tweets, Reddit threads,
   GitHub pages and YouTube pages. This is the `agent_read` tool AIRTAC's brain can call.

2. Optional, on this PC: the open-source Agent Reach CLI (MIT, https://github.com/Panniantong/agent-reach)
   adds deeper reads that need a sign-in or a special tool: X search and timelines, YouTube subtitles,
   Reddit, Bilibili and more, using tools like yt-dlp and the gh CLI on your own machine. You install and
   set it up from the Dev tools page (each step waits for your Confirm). When it's installed, `agent_read`
   uses it first and falls back to Jina. Cookies it needs stay on your PC, never in AIRTAC's database.

Nothing here posts anything: it only reads. Reading still goes out to the internet, so AIRTAC asks the
site through its normal permissions (this plugin may reach r.jina.ai; the CLI runs locally on your PC).
"""
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from airtac import config
from airtac.plugin_api import Tool, ToolResult, after, fetch, plugin, schema, unreachable

p = plugin(__name__)
JINA = "https://r.jina.ai/"
MAX_TEXT = 6000


# ---------------------------------------------------------------- the Agent Reach CLI on this PC (optional)

def cli() -> str | None:
    """The agent-reach command, if it's installed (in the venv or on PATH). None otherwise."""
    venv = Path(sys.executable).parent / ("agent-reach.exe" if sys.platform == "win32" else "agent-reach")
    if venv.exists():
        return str(venv)
    return shutil.which("agent-reach")


def _run(args: list[str], timeout: int = 60) -> tuple[int, str]:
    """Run agent-reach with a fixed argument list (never a shell, so nothing in a URL can run as a command)."""
    exe = cli()
    if not exe:
        return 127, "Agent Reach isn't installed."
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)  # noqa: S603 (fixed exe, list args)
        return r.returncode, (r.stdout or r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"Agent Reach couldn't run: {type(e).__name__}."


def doctor() -> dict:
    """What Agent Reach can do right now (which channels are ready). For the Dev tools page."""
    if not cli():
        return {"installed": False, "report": ""}
    code, out = _run(["doctor"], timeout=90)
    return {"installed": True, "ok": code == 0, "report": out[:4000]}


# ---------------------------------------------------------------- reading (the tool)

def _safe_url(url: str) -> str:
    url = (url or "").strip()
    # A scheme other than http/https (file:, ftp:, javascript:, data:) is refused outright.
    if re.match(r"^[a-z][a-z0-9+.\-]*:", url, re.IGNORECASE) and not re.match(r"^https?://", url, re.IGNORECASE):
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or any(c in url for c in " <>\"\n\\"):
        return ""
    if _private_host(parts.hostname):   # never read a local or private-network address (stops SSRF)
        return ""
    return url


def _blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """A non-public address. For IPv4-mapped IPv6 (::ffff:a.b.c.d) judge the inner IPv4 too."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_unspecified or ip.is_multicast)


def _private_host(host: str) -> bool:
    """True for anything that isn't a public address: localhost, a .local name, or any IP in a private,
    loopback, link-local or reserved range (IPv4 and IPv6). Covers 10/8, 172.16/12, 192.168/16, etc.

    A literal IP is judged directly; everything else (a name, and tricky numeric forms like 2130706433
    or 127.1 that are really 127.0.0.1) is resolved first, so a loopback in disguise can't slip through.
    """
    host = (host or "").strip().strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:                                          # a plain IP literal (1.2.3.4 or a full IPv6)
        return _blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:                                          # a name, or a shorthand/decimal IP: resolve it and judge every address
        candidates = [info[4][0] for info in socket.getaddrinfo(host, None)]
    except OSError:
        # Can't resolve. If it was only digits and dots it was meant as an IP we couldn't read, so block it.
        return bool(re.fullmatch(r"[0-9.]+|0x[0-9a-f.]+", host))
    return any(_is_blocked(addr) for addr in candidates)


def _is_blocked(addr: str) -> bool:
    try:
        return _blocked_ip(ipaddress.ip_address(addr))
    except ValueError:
        return False


def _kind(url: str) -> str:
    h = urlsplit(url).hostname or ""
    if "youtube.com" in h or "youtu.be" in h:
        return "YouTube video"
    if "twitter.com" in h or h == "x.com" or h.endswith(".x.com"):
        return "tweet"
    if "reddit.com" in h:
        return "Reddit thread"
    if "github.com" in h:
        return "GitHub page"
    return "page"


def _result(url: str, text: str, how: str) -> ToolResult:
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())[:MAX_TEXT]
    if not text:
        return ToolResult("That page didn't have any readable text.", ok=False)
    kind = _kind(url)
    spoken = f"I read the {kind}. Here's the gist on screen."
    return ToolResult(spoken, text, data={"url": url, "via": how})


def read_fixture(url: str) -> ToolResult:
    return _result(p.sample("page")["url"], p.sample("page")["text"], "sample")


def read_live(url: str) -> ToolResult:
    url = _safe_url(url)
    if not url:
        return ToolResult("Give a full public web address (https://...). I can't open private or local addresses.", ok=False)
    # If the Agent Reach CLI is set up, let it try first (it can read sign-in-only pages, subtitles, etc.).
    if cli():
        code, out = _run(["read", url], timeout=90)
        if code == 0 and len(out) > 40:
            return _result(url, out, "Agent Reach")
    data = fetch(JINA + url, as_text=True, timeout=20)   # Jina Reader: clean text, no key
    if data is None:
        return unreachable("that page")
    return _result(url, data, "Jina Reader")


p.add(Tool("agent_read",
           "Read a public web page, tweet, Reddit thread or YouTube video and return its text, so you can summarise or answer about it.",
           schema({"url": {"type": "string", "description": "The full https address to read"}}, required=("url",)),
           "read", read_fixture, read_live))


def _args(text, _messages):
    m = re.search(r"https?://\S+", text)
    return {"url": m.group(0) if m else after(text, r"\b(read|open|summarise|summarize)\b") or text}


p.mock_rule(13.5, ("read this", "read the", "this tweet", "this video", "this thread", "summarise this", "r.jina", "agent reach"),
            "agent_read", _args)
