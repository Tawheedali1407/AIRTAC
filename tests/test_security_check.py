"""scripts/security_check.py: gitleaks finds no secret in the project (skips, saying how to install it, if missing)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import security_check  # noqa: E402


def test_no_secrets_in_the_project():
    if not security_check.gitleaks_path():
        pytest.skip("gitleaks isn't installed: unzip gitleaks.exe (8.30.1, github.com/gitleaks/gitleaks/releases) "
                    "into F:\\AIRTAC Advance\\tools\\gitleaks\\, then run the tests again.")
    status, detail = security_check.check_gitleaks()
    assert status == "PASS", detail


def test_the_checks_say_pass_fail_or_skip():
    status, detail = security_check.check_npm()
    assert status == "SKIP" and "package.json" in detail          # plain JavaScript: nothing to audit
    for name, _fn in security_check.CHECKS:
        assert name in ("pip-audit", "bandit", "gitleaks", "npm audit")
    assert ".env" in security_check.GITLEAKS_CONFIG and "data" in security_check.GITLEAKS_CONFIG
