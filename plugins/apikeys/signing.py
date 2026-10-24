"""Request signing for the storage Test buttons, written out so AIRTAC needs no cloud SDKs.

- sigv4(): AWS Signature Version 4, used by AWS S3, Cloudflare R2 and Nebius Object Storage (all S3-compatible).
- azure_shared_key(): Azure Storage's Shared Key header.
- google_jwt(): the signed assertion a Google Cloud service account trades for an access token.
Each one only signs; the caller makes one read-only call (list buckets or containers).
"""
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import quote

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4(method: str, url_host: str, path: str, query: dict, region: str, key_id: str, secret: str,
          service: str = "s3", now: datetime | None = None) -> dict:
    """The headers for one unsigned-body request (GET), per AWS Signature Version 4."""
    now = now or datetime.now(timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(query.items()))
    headers = {"host": url_host, "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical = "\n".join([method, quote(path, safe="/-_.~"), canonical_query, canonical_headers, signed, EMPTY_SHA256])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret).encode("utf-8"), day)
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    signature = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": amz_date,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={signed}, Signature={signature}"}


AZURE_VERSION = "2021-12-02"


def azure_shared_key(account: str, key_b64: str, query: dict, now: datetime | None = None) -> dict:
    """Headers for GET https://<account>.blob.core.windows.net/?<query> with Shared Key auth."""
    now = now or datetime.now(timezone.utc)
    date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
    ms = {"x-ms-date": date, "x-ms-version": AZURE_VERSION}
    canonical_headers = "".join(f"{k}:{ms[k]}\n" for k in sorted(ms))
    canonical_resource = f"/{account}/" + "".join(f"\n{k.lower()}:{v}" for k, v in sorted(query.items()))
    # VERB, then 11 standard headers (all empty for a plain GET), then the x-ms- headers and the resource.
    to_sign = "GET\n" + "\n" * 11 + canonical_headers + canonical_resource
    signature = base64.b64encode(hmac.new(base64.b64decode(key_b64), to_sign.encode("utf-8"), hashlib.sha256).digest()).decode()
    return {**ms, "Authorization": f"SharedKey {account}:{signature}"}


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def google_jwt(info: dict, scope: str, now: float | None = None) -> str:
    """A signed JWT assertion from a service-account key file (RS256)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    now = int(now or time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": info.get("private_key_id", "")}
    claims = {"iss": info["client_email"], "scope": scope, "aud": GOOGLE_TOKEN_URL, "iat": now, "exp": now + 600}
    signing_input = b64(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        b64(json.dumps(claims, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + b64(signature)
