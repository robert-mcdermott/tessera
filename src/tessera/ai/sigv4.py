"""AWS Signature Version 4, from the standard library, for Amazon Bedrock with access keys.

A request is signed by hashing a canonical form of it (method, path, query, the signed headers,
and the body's SHA-256), and signing that with a key derived from the secret, the date, the
region, and the service. Checked against AWS's published test vectors.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import parse_qsl, quote, urlsplit

ALGORITHM = "AWS4-HMAC-SHA256"


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def _canonical_uri(path: str, service: str) -> str:
    if not path:
        return "/"
    # Every service but S3 encodes each segment of the (already encoded) path once more.
    if service == "s3":
        return path
    return "/".join(quote(segment, safe="-_.~") for segment in path.split("/"))


def _canonical_query(query: str) -> str:
    pairs = sorted(parse_qsl(query, keep_blank_values=True))
    return "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in pairs)


def sign(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    *,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    session_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """The headers to send: those given, plus x-amz-date (and the session token), and the
    Authorization header signing all of them."""
    when = (now or datetime.now(UTC)).astimezone(UTC)
    stamp, day = when.strftime("%Y%m%dT%H%M%SZ"), when.strftime("%Y%m%d")
    parts = urlsplit(url)
    out = {**headers, "x-amz-date": stamp}
    if session_token:
        out["x-amz-security-token"] = session_token
    if not any(k.lower() == "host" for k in out):
        out["host"] = parts.netloc
    lowered = {k.lower(): " ".join(str(v).strip().split()) for k, v in out.items()}
    signed = ";".join(sorted(lowered))
    canonical_headers = "".join(f"{k}:{lowered[k]}\n" for k in sorted(lowered))
    payload = hashlib.sha256(body).hexdigest()
    uri, query = _canonical_uri(parts.path, service), _canonical_query(parts.query)
    canonical = "\n".join([method.upper(), uri, query, canonical_headers, signed, payload])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join([ALGORITHM, stamp, scope,
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest()])  # fmt: skip
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), service),
                "aws4_request")  # fmt: skip
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out["Authorization"] = (f"{ALGORITHM} Credential={access_key}/{scope}, "
                            f"SignedHeaders={signed}, Signature={signature}")  # fmt: skip
    return out


__all__ = ["sign"]
