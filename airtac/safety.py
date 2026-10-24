"""Safety checks that run before anything reaches the model or a tool.

Layer 1 is a plain blocklist: cheap, predictable, and easy to read. It is not
clever on purpose. The confirm gate in agent.py is the second layer.
"""
import re
from pathlib import Path

REFUSAL = "I won't do that, because it could destroy files, settings or your work. If you really need it, please do it yourself."
CONTEST_REFUSAL = "That looks like a contest problem, so I won't solve it. I can help once the contest is over."

_BLOCKLIST = [
    r"\bformat\b.*\b(disk|drive|c:|d:|hard ?drive|ssd|partition)",
    r"\brm\s+-(rf|fr)\b",
    r"\brmdir\s+/s\b",
    r"\bdel\s+/[sfq]\b",
    r"\bdiskpart\b",
    r"\bregedit\b|\breg\s+(add|delete)\b|\b(edit|change|delete|modify)\b.*\bregistry\b",
    # "delete my repo", "delete the airtac repo", "delete all repos" (but not "delete the issue in my repo")
    r"\bdelete\s+(?:(?:my|the|all|this|that)\s+)*(?:[\w.-]+\s+)?repo(s|sitory|sitories)?\b",
    r"\bwipe\b.*\b(disk|drive|laptop|computer|pc)\b",
]
_BLOCK_RE = [re.compile(p, re.IGNORECASE) for p in _BLOCKLIST]


def check_request(text: str) -> str | None:
    """Return a refusal sentence if the request is blocked, else None."""
    if any(p.search(text) for p in _BLOCK_RE):
        return REFUSAL
    if is_contest_link(text):
        return CONTEST_REFUSAL
    return None


def is_contest_link(text: str) -> bool:
    # LeetCode and Codeforces both put live contest problems under /contest/.
    return "/contest/" in text.lower()


# --- Secret scanner (used later before publishing a repo)

_SECRET_PATTERNS = {
    "OpenAI-style key": r"\bsk-[A-Za-z0-9_-]{20,}",
    "GitHub token": r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{22,}",
    "Tavily key": r"\btvly-[A-Za-z0-9_-]{16,}",
    "AWS key": r"\bAKIA[0-9A-Z]{16}\b",
    "Key assignment": r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"\s]{16,}['\"]",
}
_SECRET_RE = {name: re.compile(p) for name, p in _SECRET_PATTERNS.items()}
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
# Problem statements, personal PDFs, images, the syllabus and memory live here. Never published.
PRIVATE_DIRS = {"data", "outputs", "workspace"}


def is_private_path(path) -> bool:
    return bool(PRIVATE_DIRS & set(Path(path).parts))


def looks_like_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_RE.values())


def scan_for_secrets(paths: list) -> list[str]:
    """Return findings like 'path: .env file' or 'path:12: GitHub token'.
    The secret itself is never included in the output."""
    findings = []
    for root in map(Path, paths):
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for f in files:
            if _SKIP_DIRS & set(f.parts):
                continue
            if is_private_path(f.relative_to(root) if root.is_dir() else f):
                findings.append(f"{f}: private folder (data/, outputs/ or workspace/ are never published)")
                continue
            if f.name == ".env" or (f.name.startswith(".env.") and f.name != ".env.example"):
                findings.append(f"{f}: .env file (should never be published)")
                continue
            if f.stat().st_size > 1_000_000:
                continue  # skip big binaries
            try:
                lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                for name, pattern in _SECRET_RE.items():
                    if pattern.search(line):
                        findings.append(f"{f}:{n}: {name}")
    return findings
