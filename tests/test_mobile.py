"""Part G: the Flutter app's files and the APK workflow (Flutter itself isn't installed here; CI analyses, tests and
builds the app). These checks catch a broken workflow, manifest or pubspec before a push."""
import re
import xml.etree.ElementTree as ET  # nosec B405 (our own file)
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")   # comes with bandit (requirements.txt)

ROOT = Path(__file__).resolve().parent.parent
MOBILE = ROOT / "mobile"


def _workflow() -> Path:
    """.github/workflows/ once Ali has moved it there (remote tools may not write .github), else mobile/ci/."""
    moved = ROOT / ".github" / "workflows" / "build_apk.yml"
    return moved if moved.exists() else MOBILE / "ci" / "build_apk.yml"


def test_workflow_is_valid_and_builds_with_flutter():
    wf = yaml.safe_load(_workflow().read_text(encoding="utf-8"))
    steps = wf["jobs"]["build"]["steps"]
    uses = [s.get("uses", "") for s in steps]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert any(u.startswith("subosito/flutter-action@v2") for u in uses)
    assert any(u.startswith("actions/setup-java@") for u in uses)
    for cmd in ("flutter create --platforms=android", "flutter pub get", "flutter analyze", "flutter test",
                "flutter build apk --release", "sha256sum airtac.apk"):
        assert cmd in runs, cmd
    assert "cp -r android_overrides/. android/" in runs
    assert wf["permissions"] == {"contents": "read"}                         # the build can't push anything
    assert "secrets." not in runs                                            # secrets go through env, never inline


def test_android_overrides_are_well_formed():
    manifest = ET.parse(MOBILE / "android_overrides" / "app" / "src" / "main" / "AndroidManifest.xml")  # nosec B314
    ns = "{http://schemas.android.com/apk/res/android}"
    perms = {p.get(ns + "name") for p in manifest.getroot().iter("uses-permission")}
    assert {"android.permission.INTERNET", "android.permission.CAMERA", "android.permission.POST_NOTIFICATIONS"} <= perms
    app = manifest.getroot().find("application")
    assert app.get(ns + "usesCleartextTraffic") == "false" and app.get(ns + "allowBackup") == "false"
    actions = {a.get(ns + "name") for a in manifest.getroot().iter("action")}
    assert "android.intent.action.SEND" in actions                           # Share to Airtac
    gradle = (MOBILE / "android_overrides" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "isCoreLibraryDesugaringEnabled = true" in gradle and "desugar_jdk_libs:2.1.4" in gradle
    assert 'namespace = "com.airtac.mobile"' in gradle
    kt = (MOBILE / "android_overrides" / "app" / "src" / "main" / "kotlin" / "com" / "airtac" / "mobile" / "MainActivity.kt")
    assert "FlutterFragmentActivity" in kt.read_text(encoding="utf-8")


def test_pubspec_pins_every_package():
    spec = yaml.safe_load((MOBILE / "pubspec.yaml").read_text(encoding="utf-8"))
    assert spec["name"] == "airtac_mobile" and spec["publish_to"] == "none"
    for name, ver in spec["dependencies"].items():
        if name != "flutter":
            assert re.fullmatch(r"\^\d+\.\d+\.\d+", str(ver)), (name, ver)


def test_app_trusts_only_the_pinned_certificate_and_keeps_no_secret():
    api = (MOBILE / "lib" / "src" / "api.dart").read_text(encoding="utf-8")
    assert "SecurityContext(withTrustedRoots: false)" in api and "badCertificateCallback" in api
    web = (MOBILE / "lib" / "src" / "screens" / "web_screen.dart").read_text(encoding="utf-8")
    assert "onReceivedServerTrustAuthRequest" in web and "pinMatches" in web
    for f in MOBILE.rglob("*.dart"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"(AIza[0-9A-Za-z_-]{30,}|gsk_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z]{20,})", text), f
        assert "http://" not in text.replace("http://schemas", ""), f      # HTTPS only


def test_the_two_copies_of_the_workflow_match():
    moved = ROOT / ".github" / "workflows" / "build_apk.yml"
    if moved.exists():
        assert moved.read_text(encoding="utf-8") == (MOBILE / "ci" / "build_apk.yml").read_text(encoding="utf-8")


def test_layout_switch_never_marks_the_page_as_a_menu_link():
    """web/view.js (phone: Fit to screen / Desktop layout). app.js treats every [data-view] element as a menu link and
    cancels its clicks, so the switch must not put data-view on <html> (it once did: every click on the page broke)."""
    js = (ROOT / "web" / "view.js").read_text(encoding="utf-8")
    assert 'setAttribute("data-page-view"' in js and 'setAttribute("data-view"' not in js
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    head = html.split("</head>")[0]
    assert '<script src="view.js' in head and 'id="view-toggle"' in html and 'name="page-view"' in html
