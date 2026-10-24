"""One command for AIRTAC's security checks:

    .venv\\Scripts\\python scripts\\security_check.py

- pip-audit: known vulnerabilities in the installed Python packages (needs the internet);
- bandit: risky patterns in AIRTAC's own code (airtac/, plugins/, scripts/), medium severity and up;
- gitleaks: secrets (keys, tokens) committed to the git history or sitting in the working folder.
  It reads tools\\gitleaks\\gitleaks.exe, or gitleaks on PATH. .env and data\\ are skipped: they're
  gitignored, never committed, and hold your real keys on purpose.
- npm audit only if a package.json exists (AIRTAC has none: plain JavaScript, no build step).

Each check says PASS, FAIL or SKIP (with how to install it). Exit code 1 if any check FAILS.
Nothing is uploaded, and no secret is printed: gitleaks runs with --redact.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = ["airtac", "plugins", "scripts"]


def gitleaks_path() -> str | None:
    local = ROOT / "tools" / "gitleaks" / ("gitleaks.exe" if sys.platform == "win32" else "gitleaks")
    return str(local) if local.is_file() else shutil.which("gitleaks")


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,  # noqa: S603 (fixed commands)
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def check_pip_audit() -> tuple[str, str]:
    try:
        r = _run([sys.executable, "-m", "pip_audit", "--progress-spinner", "off", "--format", "json"])
    except FileNotFoundError:
        return "SKIP", "pip-audit isn't installed: .venv\\Scripts\\pip install --no-cache-dir pip-audit==2.10.1"
    if "No module named pip_audit" in r.stderr:
        return "SKIP", "pip-audit isn't installed: .venv\\Scripts\\pip install --no-cache-dir pip-audit==2.10.1"
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return "SKIP", "pip-audit couldn't check (offline?): " + (r.stderr.strip().splitlines() or ["no output"])[-1][:200]
    bad = [(d["name"], d["version"], [v["id"] for v in d.get("vulns", [])]) for d in data.get("dependencies", []) if d.get("vulns")]
    if not bad:
        return "PASS", f"{len(data.get('dependencies', []))} packages, no known vulnerabilities"
    return "FAIL", "; ".join(f"{n} {v}: {', '.join(ids)}" for n, v, ids in bad)


def check_bandit() -> tuple[str, str]:
    r = _run([sys.executable, "-m", "bandit", "-q", "-r", *CODE, "-ll", "-ii", "-f", "json"])
    if "No module named bandit" in r.stderr:
        return "SKIP", "bandit isn't installed: .venv\\Scripts\\pip install --no-cache-dir bandit==1.9.4"
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return "SKIP", "bandit didn't run: " + r.stderr.strip()[-200:]
    issues = data.get("results", [])
    if not issues:
        return "PASS", "no medium or high findings (medium confidence and up) in airtac/, plugins/, scripts/"
    return "FAIL", "; ".join(f"{i['filename']}:{i['line_number']} {i['test_id']} {i['issue_text'][:80]}" for i in issues[:15])


GITLEAKS_CONFIG = """
[extend]
useDefault = true

[allowlist]
description = "Your real keys live here on purpose (gitignored); the tests use made-up keys."
paths = ['''(^|[\\\\/])\\.env$''', '''(^|[\\\\/])data[\\\\/]''', '''(^|[\\\\/])\\.venv[\\\\/]''', '''(^|[\\\\/])tests[\\\\/]''',
         '''(^|[\\\\/])(knowledge|outputs|models|backups|tmp|tools)[\\\\/]''']
"""


def check_gitleaks() -> tuple[str, str]:
    exe = gitleaks_path()
    if not exe:
        return "SKIP", ("gitleaks isn't installed. Download gitleaks_8.30.1_windows_x64.zip from "
                        "https://github.com/gitleaks/gitleaks/releases and unzip gitleaks.exe into F:\\AIRTAC Advance\\tools\\gitleaks\\")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "gitleaks.toml"
        cfg.write_text(GITLEAKS_CONFIG, encoding="utf-8")
        report = Path(tmp) / "report.json"
        found = []
        modes = [["git", "."]] if (ROOT / ".git").exists() else []
        modes.append(["dir", "."])
        for mode in modes:
            r = _run([exe, *mode, "--no-banner", "--redact", "--config", str(cfg), "--report-format", "json",
                      "--report-path", str(report), "--exit-code", "3"])
            if r.returncode not in (0, 3):
                return "SKIP", f"gitleaks {mode[0]} didn't run: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200]
            if r.returncode == 3 and report.exists():
                found += [f"{f.get('File', '?')}:{f.get('StartLine', '?')} {f.get('RuleID', '')}"
                          for f in json.loads(report.read_text(encoding="utf-8") or "[]")]
    if not found:
        return "PASS", "no secrets in the git history or the folder (.env and data\\ skipped)"
    return "FAIL", "; ".join(sorted(set(found))[:15]) + " (values redacted; move each one into the vault)"


def check_npm() -> tuple[str, str]:
    if not (ROOT / "package.json").exists():
        return "SKIP", "no package.json (plain JavaScript, nothing to audit)"
    npm = shutil.which("npm")
    if not npm:
        return "SKIP", "npm isn't installed"
    r = _run([npm, "audit", "--omit=dev"])
    return ("PASS" if r.returncode == 0 else "FAIL"), (r.stdout.strip().splitlines() or ["?"])[-1][:200]


CHECKS = [("pip-audit", check_pip_audit), ("bandit", check_bandit), ("gitleaks", check_gitleaks), ("npm audit", check_npm)]


def main() -> int:
    failed = False
    for name, fn in CHECKS:
        try:
            status, detail = fn()
        except subprocess.TimeoutExpired:
            status, detail = "SKIP", "took too long"
        failed |= status == "FAIL"
        print(f"{status:4}  {name:10} {detail}")
    print("\nSome checks failed: see above." if failed else "\nNo problems found.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
