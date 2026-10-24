"""APIs and keys: the cards for search, dev, cloud and hackathon services, and your own custom keys.

Every value goes into the vault (airtac/connections.py does the saving, masking, Show and Copy).
This plugin only adds the cards and their Test buttons. Each Test makes ONE read-only call that the
service documents (the `docs` link on each card) and says OK, refused, or out of requests:

  Brave Search      GET api.search.brave.com/res/v1/web/search       X-Subscription-Token
  SerpAPI           GET serpapi.com/account.json                     api_key (their only way)
  Context7          GET context7.com/api/v3/search                   Authorization: Bearer
  CodeRabbit        GET api.coderabbit.ai/v1/users                   x-coderabbitai-api-key
  Nebius Object Storage, AWS S3, Cloudflare R2: list buckets (S3 API, Signature V4)
  Google Cloud Storage: service account -> token -> list buckets
  Azure Blob        list containers (Shared Key)
  SAP AI Core       OAuth client credentials -> GET {AI_API_URL}/v2/lm/scenarios (AI-Resource-Group)
  SAP BTP           OAuth client credentials from any service key (proves the key works)
  Dropbox           POST api.dropboxapi.com/2/users/get_current_account
  Supabase          GET <project>.supabase.co/auth/v1/settings       apikey
  Google Drive      comes with Google sign-in (Part F): no key here

Custom keys ("+ Add custom API key"): a name, the service's address, up to 4 fields, notes, a logo
(an uploaded PNG/JPEG/WebP, or letters) and, if you want, a Test address that should answer 200 with
the key. Definitions (no secrets) are kept in AIRTAC's database; the values go into the vault.
Nothing here is a tool: models never see a key, and these routes work on this PC only.
"""
import json
import re
from urllib.parse import urlparse

import httpx

from airtac import config, connections, memory
from airtac.connections import Field
from airtac.plugin_api import local_only, plugin

from . import signing

p = plugin(__name__)
CLOUD, DEV = "Hackathon and cloud", "Search and dev tools"


def _say(r: httpx.Response, name: str, ok: str) -> tuple[bool, str]:
    """The usual answers: worked, refused, or out of requests."""
    if r.status_code in (401, 403):
        return False, f"{name} refused the key. Copy it again from their site."
    if r.status_code == 429:
        return True, f"The key works, but {name} says you're out of requests right now."
    if r.status_code >= 400:
        return False, f"{name} answered with an error ({r.status_code})."
    return True, ok


# ---------------------------------------------------------------- search and dev tools

def _brave(http):
    r = http.get("https://api.search.brave.com/res/v1/web/search", params={"q": "test", "count": 1},
                 headers={"X-Subscription-Token": p.secret("BRAVE_API_KEY"), "Accept": "application/json"})
    return _say(r, "Brave Search", "Connected. Brave Search answered.")


def _serpapi(http):
    r = http.get("https://serpapi.com/account.json", params={"api_key": p.secret("SERPAPI_API_KEY")})
    if r.status_code == 200:
        left = r.json().get("total_searches_left")
        return True, f"Connected. {left} searches left this month." if left is not None else "Connected."
    return _say(r, "SerpAPI", "Connected.")


def _context7(http):
    r = http.get("https://context7.com/api/v3/search", params={"query": "useState", "library": "react"},
                 headers={"Authorization": f"Bearer {p.secret('CONTEXT7_API_KEY')}"})
    return _say(r, "Context7", "Connected. Context7 found the React docs.")


def _coderabbit(http):
    r = http.get("https://api.coderabbit.ai/v1/users", headers={"x-coderabbitai-api-key": p.secret("CODERABBIT_API_KEY")})
    if r.status_code == 403:
        return True, "CodeRabbit knows the key, but this API needs their Enterprise plan."
    return _say(r, "CodeRabbit", "Connected. CodeRabbit answered.")


p.connector(id="brave_search", name="Brave Search API", group=DEV, order=24, logo="brave",
            get_key="https://api-dashboard.search.brave.com/", docs="https://api-dashboard.search.brave.com/documentation/quickstart",
            about="Web search results from Brave's own index.", powers=["web search (key saved for tools and AIRTAC 1)"],
            fields=[Field("BRAVE_API_KEY", "API key", placeholder="Your subscription token")], test=_brave)
p.connector(id="serpapi", name="SerpAPI", group=DEV, order=25,
            get_key="https://serpapi.com/manage-api-key", docs="https://serpapi.com/account-api",
            about="Google and other search results as JSON. The Test reads your account (it doesn't use a search).",
            powers=["search results (key saved for tools)"], fields=[Field("SERPAPI_API_KEY", "API key")], test=_serpapi)
p.connector(id="context7", name="Context7", group=DEV, order=26,
            get_key="https://context7.com/dashboard", docs="https://context7.com/docs/search-api",
            about="Up-to-date library documentation for coding questions.", powers=["library docs (key saved)"],
            fields=[Field("CONTEXT7_API_KEY", "API key", placeholder="ctx7sk-...")], test=_context7)
p.connector(id="coderabbit", name="CodeRabbit", group=DEV, order=27, logo="coderabbit",
            get_key="https://app.coderabbit.ai/settings/api-keys", docs="https://docs.coderabbit.ai/api",
            about="AI code reviews. Most of its API needs the Enterprise plan.", powers=["code review (key saved)"],
            fields=[Field("CODERABBIT_API_KEY", "API key")], test=_coderabbit)


# ---------------------------------------------------------------- object storage (S3-compatible)

def _s3_codes(text: str) -> str:
    m = re.search(r"<Code>([^<]+)</Code>", text or "")
    return m.group(1) if m else ""


def _list_buckets(http, endpoint: str, region: str, key_id: str, secret: str, name: str) -> tuple[bool, str]:
    host = urlparse(endpoint).hostname or ""
    r = http.get(endpoint.rstrip("/") + "/", headers=signing.sigv4("GET", host, "/", {}, region, key_id, secret))
    if r.status_code == 200:
        n = r.text.count("<Bucket>")
        return True, f"Connected. {n} bucket{'' if n == 1 else 's'} found."
    code = _s3_codes(r.text)
    if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidToken", "AuthorizationHeaderMalformed"):
        return False, f"{name} refused the key ({code}). Check the key ID, secret and region."
    if code == "AccessDenied":
        return True, f"{name} accepted the key, but it may not list buckets. That's fine for a key limited to one bucket."
    return _say(r, name, "Connected.")


def _region(key: str, default: str) -> str:
    value = (p.secret(key) or default).strip()
    if not re.fullmatch(r"[a-z0-9-]{2,30}", value):
        raise ValueError("bad region")
    return value


def _nebius_s3(http):
    try:
        region = _region("NEBIUS_S3_REGION", "eu-north1")
    except ValueError:
        return False, "The region should look like eu-north1."
    return _list_buckets(http, f"https://storage.{region}.nebius.cloud", region, p.secret("NEBIUS_S3_KEY_ID"),
                         p.secret("NEBIUS_S3_SECRET"), "Nebius Object Storage")


def _aws_s3(http):
    try:
        region = _region("AWS_REGION", "us-east-1")
    except ValueError:
        return False, "The region should look like ap-south-1."
    return _list_buckets(http, f"https://s3.{region}.amazonaws.com", region, p.secret("AWS_ACCESS_KEY_ID"),
                         p.secret("AWS_SECRET_ACCESS_KEY"), "AWS S3")


def _r2(http):
    account = (p.secret("R2_ACCOUNT_ID") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", account):
        return False, "The account ID is the 32-character ID in your Cloudflare dashboard."
    return _list_buckets(http, f"https://{account}.r2.cloudflarestorage.com", "auto", p.secret("R2_ACCESS_KEY_ID"),
                         p.secret("R2_SECRET_ACCESS_KEY"), "Cloudflare R2")


p.connector(id="nebius_storage", name="Nebius Object Storage", group=CLOUD, order=10,
            get_key="https://console.nebius.com/", docs="https://docs.nebius.com/object-storage/quickstart",
            about="S3-compatible storage on Nebius (the hackathon's cloud). The models are under Brains (Nebius Token Factory).",
            powers=["file storage (S3 API)"], note="Endpoint: https://storage.<region>.nebius.cloud",
            fields=[Field("NEBIUS_S3_KEY_ID", "Access key ID"), Field("NEBIUS_S3_SECRET", "Secret access key"),
                    Field("NEBIUS_S3_REGION", "Region", secret=False, required=False, placeholder="eu-north1")],
            test=_nebius_s3)
p.connector(id="aws_s3", name="AWS S3", group=CLOUD, order=30,
            get_key="https://console.aws.amazon.com/iam/home#/security_credentials",
            docs="https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListBuckets.html",
            about="Amazon's object storage. Use an IAM user's key with only the access it needs.", powers=["file storage"],
            fields=[Field("AWS_ACCESS_KEY_ID", "Access key ID", placeholder="AKIA..."),
                    Field("AWS_SECRET_ACCESS_KEY", "Secret access key"),
                    Field("AWS_REGION", "Region", secret=False, required=False, placeholder="ap-south-1")], test=_aws_s3)
p.connector(id="cloudflare_r2", name="Cloudflare R2", group=CLOUD, order=31, logo="cloudflare",
            get_key="https://dash.cloudflare.com/?to=/:account/r2/api-tokens",
            docs="https://developers.cloudflare.com/r2/api/s3/api/", about="S3-compatible storage with no download fees.",
            powers=["file storage"],
            fields=[Field("R2_ACCOUNT_ID", "Account ID", secret=False), Field("R2_ACCESS_KEY_ID", "Access key ID"),
                    Field("R2_SECRET_ACCESS_KEY", "Secret access key")], test=_r2)


# ---------------------------------------------------------------- Google Cloud Storage, Azure, Dropbox, Supabase

def _json_field(key: str, needs: tuple) -> dict | None:
    try:
        info = json.loads(p.secret(key) or "")
    except ValueError:
        return None
    return info if isinstance(info, dict) and all(info.get(k) for k in needs) else None


def _gcs(http):
    info = _json_field("GCS_SERVICE_ACCOUNT_JSON", ("client_email", "private_key", "project_id"))
    if info is None or info.get("type") != "service_account":
        return False, "Paste the whole service-account key file (JSON) from Google Cloud."
    try:
        assertion = signing.google_jwt(info, "https://www.googleapis.com/auth/devstorage.read_only")
    except Exception:
        return False, "The private key in that file couldn't be read."
    r = http.post(signing.GOOGLE_TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                  "assertion": assertion})
    if r.status_code != 200:
        return False, "Google refused the service account. It may be disabled or deleted."
    token = r.json().get("access_token", "")
    r = http.get("https://storage.googleapis.com/storage/v1/b", params={"project": info["project_id"], "maxResults": 50},
                 headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 403:
        return True, "The service account works, but it may not list buckets in this project."
    ok, msg = _say(r, "Google Cloud Storage", "")
    return (ok, msg) if msg else (True, f"Connected. {len(r.json().get('items', []))} buckets in {info['project_id']}.")


def _azure(http):
    account = (p.secret("AZURE_STORAGE_ACCOUNT") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]{3,24}", account):
        return False, "The storage account name is 3 to 24 lowercase letters and numbers."
    try:
        headers = signing.azure_shared_key(account, p.secret("AZURE_STORAGE_KEY"), {"comp": "list", "maxresults": "50"})
    except ValueError:
        return False, "The account key should be the long base64 key from Access keys."
    r = http.get(f"https://{account}.blob.core.windows.net/", params={"comp": "list", "maxresults": "50"}, headers=headers)
    if r.status_code == 200:
        n = r.text.count("<Container>")
        return True, f"Connected. {n} container{'' if n == 1 else 's'} found."
    return _say(r, "Azure Storage", "Connected.")


def _dropbox(http):
    r = http.post("https://api.dropboxapi.com/2/users/get_current_account",
                  headers={"Authorization": f"Bearer {p.secret('DROPBOX_ACCESS_TOKEN')}"})
    if r.status_code == 200:
        return True, f"Connected as {r.json().get('name', {}).get('display_name', 'your account')}."
    if r.status_code == 400:
        return False, "Dropbox didn't accept that token. Make a new one in the App Console."
    return _say(r, "Dropbox", "Connected.")


def _supabase(http):
    url = (p.secret("SUPABASE_URL") or "").strip().rstrip("/")
    if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url):
        return False, "The project URL looks like https://abcdefgh.supabase.co"
    key = p.secret("SUPABASE_KEY")
    r = http.get(f"{url}/auth/v1/settings", headers={"apikey": key})
    return _say(r, "Supabase", "Connected. Your project answered.")


p.connector(id="gcs", name="Google Cloud Storage", group=CLOUD, order=32, logo="googlecloudstorage",
            get_key="https://console.cloud.google.com/iam-admin/serviceaccounts",
            docs="https://cloud.google.com/storage/docs/json_api/v1/buckets/list",
            about="Google's object storage, with a service account's key file.", powers=["file storage"],
            note="Paste the whole JSON key file. Give the account only Storage Object Viewer (or what you need).",
            fields=[Field("GCS_SERVICE_ACCOUNT_JSON", "Service account key (JSON)", multiline=True, max_length=8000,
                          placeholder='{"type": "service_account", ...}')], test=_gcs)
p.connector(id="azure_blob", name="Azure Blob Storage", group=CLOUD, order=33,
            get_key="https://portal.azure.com/#browse/Microsoft.Storage%2FStorageAccounts",
            docs="https://learn.microsoft.com/rest/api/storageservices/authorize-with-shared-key",
            about="Microsoft's object storage, with a storage account's access key.", powers=["file storage"],
            fields=[Field("AZURE_STORAGE_ACCOUNT", "Storage account name", secret=False),
                    Field("AZURE_STORAGE_KEY", "Access key")], test=_azure)
p.connector(id="dropbox", name="Dropbox", group=CLOUD, order=35, logo="dropbox",
            get_key="https://www.dropbox.com/developers/apps",
            docs="https://www.dropbox.com/developers/documentation/http/documentation#users-get_current_account",
            about="Files in your Dropbox, with an access token from your own Dropbox app.", powers=["files (key saved)"],
            fields=[Field("DROPBOX_ACCESS_TOKEN", "Access token", placeholder="sl....")], test=_dropbox)
p.connector(id="supabase", name="Supabase", group=CLOUD, order=36, logo="supabase",
            get_key="https://supabase.com/dashboard/project/_/settings/api-keys", docs="https://supabase.com/docs/guides/api/api-keys",
            about="A Postgres database with an API. Use the publishable key unless you know you need the secret one.",
            powers=["database (key saved)"],
            fields=[Field("SUPABASE_URL", "Project URL", secret=False, placeholder="https://abcdefgh.supabase.co"),
                    Field("SUPABASE_KEY", "API key", placeholder="sb_publishable_...")], test=_supabase)
p.connector(id="google_drive", name="Google Drive", group=CLOUD, order=37, logo="googledrive",
            about="Comes with Google sign-in (Settings, Account), so there's no key to paste here.",
            powers=["sync and restore (with Google sign-in)"], fields=[], test=None,
            note="Not set up yet: arrives with Google sign-in.")


# ---------------------------------------------------------------- SAP

def _sap_key(name: str) -> dict | None:
    info = _json_field(name, ("clientid", "clientsecret", "url"))
    if info is None or not urlparse(info["url"]).hostname or not info["url"].startswith("https://"):
        return None
    return info


def _sap_token(http, info: dict) -> tuple[str, str]:
    """(token, "") or ("", a sentence)."""
    r = http.post(info["url"].rstrip("/") + "/oauth/token", data={"grant_type": "client_credentials"},
                  auth=(info["clientid"], info["clientsecret"]))
    if r.status_code in (400, 401, 403):
        return "", "SAP refused the client ID and secret in that service key."
    if r.status_code != 200:
        return "", f"SAP's sign-in answered with an error ({r.status_code})."
    return r.json().get("access_token", ""), ""


def _sap_ai_core(http):
    info = _sap_key("SAP_AICORE_SERVICE_KEY")
    api = (info or {}).get("serviceurls", {}).get("AI_API_URL", "")
    if info is None or not api.startswith("https://"):
        return False, "Paste the whole AI Core service key (JSON with clientid, clientsecret, url and serviceurls)."
    token, why = _sap_token(http, info)
    if not token:
        return False, why
    group = (p.secret("SAP_AICORE_RESOURCE_GROUP") or "default").strip()
    r = http.get(api.rstrip("/") + "/v2/lm/scenarios", headers={"Authorization": f"Bearer {token}", "AI-Resource-Group": group})
    if r.status_code == 200:
        n = r.json().get("count", len(r.json().get("resources", [])))
        return True, f"Connected. {n} scenario{'' if n == 1 else 's'} in resource group {group}."
    return _say(r, "SAP AI Core", "Connected.")


def _sap_btp(http):
    info = _sap_key("SAP_BTP_SERVICE_KEY")
    if info is None:
        return False, "Paste a service key (JSON with clientid, clientsecret and url) from your BTP subaccount."
    token, why = _sap_token(http, info)
    return (True, "Connected. SAP's sign-in accepted the service key.") if token else (False, why or "No token came back.")


p.connector(id="sap_ai_core", name="SAP AI Core", group=CLOUD, order=20, logo="sap",
            get_key="https://help.sap.com/docs/sap-ai-core/sap-ai-core-service-guide/create-service-key",
            docs="https://help.sap.com/docs/sap-ai-core",
            about="SAP's service for running AI models (generative AI hub). Uses the service key from your BTP subaccount.",
            powers=["SAP AI Core (key saved)"],
            fields=[Field("SAP_AICORE_SERVICE_KEY", "Service key (JSON)", multiline=True, max_length=8000,
                          placeholder='{"clientid": ..., "serviceurls": {"AI_API_URL": ...}}'),
                    Field("SAP_AICORE_RESOURCE_GROUP", "Resource group", secret=False, required=False, placeholder="default")],
            test=_sap_ai_core)
p.connector(id="sap_btp", name="SAP BTP", group=CLOUD, order=21, logo="sap",
            get_key="https://cockpit.btp.cloud.sap/", docs="https://help.sap.com/docs/btp",
            about="Any other BTP service key (for example a Cloud Foundry service instance).", powers=["SAP BTP (key saved)"],
            fields=[Field("SAP_BTP_SERVICE_KEY", "Service key (JSON)", multiline=True, max_length=8000)], test=_sap_btp)


# ---------------------------------------------------------------- custom keys

MAX_CUSTOM, MAX_FIELDS = 40, 4
LOGO_TYPES = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp"}


def _defs() -> list[dict]:
    try:
        found = json.loads(memory.kv_get("apikeys", "custom") or "[]")
    except ValueError:
        return []
    return found if isinstance(found, list) else []


def _logo_dir():
    return config.DB_PATH.parent / "logos"


def _logo_file(cid: str):
    for ext in ("png", "jpg", "webp"):
        f = _logo_dir() / f"{cid}.{ext}"
        if f.is_file():
            return f
    return None


def _custom_test(d: dict):
    def test(http):
        values = [config.secret(f["key"]) for f in d["fields"] if f.get("secret", True)]
        value = values[0] if values else ""
        header = d.get("header") or "Authorization"
        prefix = "Bearer " if header.lower() == "authorization" and d.get("bearer", True) else ""
        r = connections.call_public(http, d["test_url"], {header: prefix + value})
        return _say(r, d["name"], f"Connected. {urlparse(d['test_url']).hostname} answered.")
    return test


def _register(d: dict) -> None:
    connections.add(connections.Connector(
        id=d["id"], name=d["name"], about=d.get("notes", ""), powers=[], group="Custom", order=50, plugin=p.id,
        fields=[Field(f["key"], f["label"], secret=f.get("secret", True), required=i == 0) for i, f in enumerate(d["fields"])],
        get_key=d.get("url", ""), test=_custom_test(d) if d.get("test_url") else None, custom=True,
        logo=f"/api/plugins/apikeys/logo/{d['id']}" if _logo_file(d["id"]) else ""))


for _d in _defs():
    _register(_d)


def _https(url: str, what: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or len(url) > 300:
        raise ValueError(f"The {what} must be a plain https:// address.")
    return url


def save_custom(body: dict, cid: str = "") -> dict:
    connections.require_unlocked()
    name = str(body.get("name", "")).strip()[:60]
    if not name:
        raise ValueError("Give it a name.")
    defs = _defs()
    old = next((d for d in defs if d["id"] == cid), None) if cid else None
    if cid and old is None:
        raise KeyError(cid)
    if not old and len(defs) >= MAX_CUSTOM:
        raise ValueError(f"That's {MAX_CUSTOM} custom keys already.")
    new_id = old["id"] if old else "custom_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "key")
    while not old and (new_id in connections.REGISTRY or any(d["id"] == new_id for d in defs)):
        new_id += "_2"
    raw_fields = body.get("fields") or [{"label": "API key", "secret": True}]
    if not isinstance(raw_fields, list) or not 1 <= len(raw_fields) <= MAX_FIELDS:
        raise ValueError(f"A custom key has 1 to {MAX_FIELDS} fields.")
    fields = []
    for i, f in enumerate(raw_fields):
        label = str((f or {}).get("label", "")).strip()[:40] or f"Field {i + 1}"
        fields.append({"key": f"CUSTOM_{new_id[7:].upper()}_{i + 1}", "label": label, "secret": bool((f or {}).get("secret", True))})
    header = str(body.get("header", "Authorization")).strip()[:40] or "Authorization"
    if not re.fullmatch(r"[A-Za-z0-9-]+", header):
        raise ValueError("The header name can only have letters, numbers and dashes.")
    d = {"id": new_id, "name": name, "url": _https(body.get("url", ""), "service address"),
         "test_url": _https(body.get("test_url", ""), "Test address"), "header": header,
         "bearer": bool(body.get("bearer", True)), "notes": str(body.get("notes", "")).strip()[:500], "fields": fields}
    if old:   # fields that went away: their saved values go too
        gone = {f["key"] for f in old["fields"]} - {f["key"] for f in fields}
        for key in gone:
            connections._store(key, "")
    defs = [x for x in defs if x["id"] != new_id] + [d]
    memory.kv_set("apikeys", "custom", json.dumps(defs))
    _register(d)
    return d


def delete_custom(cid: str) -> None:
    connections.require_unlocked()
    defs = _defs()
    d = next((x for x in defs if x["id"] == cid), None)
    if d is None:
        raise KeyError(cid)
    connections.disconnect(cid)
    memory.kv_set("apikeys", "custom", json.dumps([x for x in defs if x["id"] != cid]))
    connections.REGISTRY.pop(cid, None)
    connections.LAST_TEST.pop(cid, None)
    f = _logo_file(cid)
    if f:
        f.unlink()


def save_logo(cid: str, data: bytes) -> None:
    connections.require_unlocked()
    if not any(d["id"] == cid for d in _defs()):
        raise KeyError(cid)
    if len(data) > 200_000:
        raise ValueError("The logo can be up to 200 KB.")
    ext = next((e for magic, e in LOGO_TYPES.items() if data.startswith(magic)), None)
    if ext is None or (ext == "webp" and data[8:12] != b"WEBP"):
        raise ValueError("The logo must be a PNG, JPEG or WebP picture.")
    old = _logo_file(cid)
    if old:
        old.unlink()
    _logo_dir().mkdir(parents=True, exist_ok=True)
    (_logo_dir() / f"{cid}.{ext}").write_bytes(data)
    _register(next(d for d in _defs() if d["id"] == cid))


@p.routes
def api(router) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse

    def guard(request: Request):
        local_only(request)

    def run(fn, *args):
        try:
            return fn(*args)
        except connections.Locked as e:
            raise HTTPException(423, str(e))
        except KeyError:
            raise HTTPException(404, "No such custom key.")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/custom")
    def custom_list(request: Request):
        guard(request)
        if not connections.unlocked():
            raise HTTPException(423, "Locked.")
        return {"custom": _defs()}

    @router.post("/custom")
    async def custom_add(request: Request):
        guard(request)
        return run(save_custom, await request.json())

    @router.post("/custom/{cid}")
    async def custom_edit(cid: str, request: Request):
        guard(request)
        return run(save_custom, await request.json(), cid)

    @router.post("/custom/{cid}/delete")
    def custom_delete(cid: str, request: Request):
        guard(request)
        run(delete_custom, cid)
        return {"ok": True}

    @router.post("/custom/{cid}/logo")
    async def custom_logo(cid: str, request: Request):
        guard(request)
        data = await request.body()
        run(save_logo, cid, data[:200_001])
        return {"ok": True}

    @router.get("/logo/{cid}")
    def logo(cid: str):
        f = _logo_file(cid) if re.fullmatch(r"custom_[a-z0-9_]+", cid) else None
        if f is None:
            raise HTTPException(404, "No logo.")
        return FileResponse(f, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
