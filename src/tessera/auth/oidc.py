"""An OpenID Connect client for single sign-on (PLT-10, ADR-0011), with the standard library.

The authorization-code flow with PKCE: the provider's discovery document, the authorization
address, the code's exchange at the token endpoint, and the ID token's checks (its RS256
signature against the provider's published keys, issuer, audience, authorized party, expiry,
issue time, and nonce). RSA verification is one modular exponentiation and a comparison with
the PKCS #1 v1.5 encoding of the digest (RFC 8017, section 8.2.2).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from tessera import __version__
from tessera.core.errors import TesseraError

LEEWAY = 60  # seconds of clock difference allowed on exp, iat, and nbf
DISCOVERY_SECONDS = 3600  # how long a discovery document is kept
KEYS_RETRY_SECONDS = 300  # an unknown key ID reads the keys again at most this often
MIN_RSA_BITS = 2048

# DER prefixes of the DigestInfo for each hash (RFC 8017, section 9.2, note 1).
_DIGEST_INFO = {
    "RS256": (hashlib.sha256, bytes.fromhex("3031300d060960864801650304020105000420")),
    "RS384": (hashlib.sha384, bytes.fromhex("3041300d060960864801650304020205000430")),
    "RS512": (hashlib.sha512, bytes.fromhex("3051300d060960864801650304020305000440")),
}


class OIDCError(TesseraError):
    """A sign-in that cannot go on: the message says why, for the log and the admin."""


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class PKCE:
    verifier: str
    challenge: str

    @classmethod
    def new(cls) -> PKCE:
        verifier = secrets.token_urlsafe(64)  # 86 characters, within RFC 7636's 43 to 128
        challenge = b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
        return cls(verifier, challenge)


def rsa_verify(alg: str, n: int, e: int, signing_input: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 verification (RS256, RS384, RS512)."""
    if alg not in _DIGEST_INFO or n.bit_length() < MIN_RSA_BITS or e < 3 or e % 2 == 0:
        return False
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    encoded = pow(s, e, n).to_bytes(size, "big")
    digest, prefix = _DIGEST_INFO[alg]
    info = prefix + digest(signing_input).digest()
    if size < len(info) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info
    return hmac.compare_digest(encoded, expected)


def decode_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    """(header, claims, signing input, signature), without checking anything."""
    try:
        head, body, sig = token.split(".")
        header = json.loads(b64url_decode(head))
        claims = json.loads(b64url_decode(body))
        signature = b64url_decode(sig)
    except (ValueError, TypeError) as exc:
        raise OIDCError("The ID token is not a well-formed JWT.") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise OIDCError("The ID token is not a well-formed JWT.")
    return header, claims, f"{head}.{body}".encode("ascii"), signature


def _rsa_key(jwk: dict[str, Any]) -> tuple[int, int] | None:
    if jwk.get("kty") != "RSA" or jwk.get("use", "sig") != "sig":
        return None
    try:
        n = int.from_bytes(b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(b64url_decode(jwk["e"]), "big")
    except (KeyError, ValueError, TypeError):
        return None
    return n, e


def check_id_token(
    token: str,
    keys: list[dict[str, Any]],
    *,
    issuer: str,
    client_id: str,
    nonce: str,
    now: float | None = None,
) -> dict[str, Any]:
    """The ID token's claims, once its signature and claims have been checked (OpenID Connect
    Core, section 3.1.3.7)."""
    header, claims, signing_input, signature = decode_jwt(token)
    alg = header.get("alg")
    if alg not in _DIGEST_INFO:
        raise OIDCError(
            f"The ID token is signed with {alg!r}; Tessera accepts RS256, RS384, or RS512. "
            "Set the provider's ID token signing algorithm to RS256."
        )
    kid = header.get("kid")
    candidates = [k for k in keys if kid is None or k.get("kid") == kid]
    verified = any(
        (key := _rsa_key(k)) is not None
        and k.get("alg", alg) == alg
        and rsa_verify(alg, key[0], key[1], signing_input, signature)
        for k in candidates
    )
    if not verified:
        raise OIDCError("The ID token's signature does not match the provider's keys.")
    if claims.get("iss") != issuer:
        raise OIDCError(f"The ID token is from {claims.get('iss')!r}, not {issuer!r}.")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences:
        raise OIDCError("The ID token is not for this application (its audience differs).")
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise OIDCError("The ID token names another application as its authorized party.")
    now = time.time() if now is None else now
    try:
        expires = float(claims["exp"])
        issued = float(claims["iat"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OIDCError("The ID token has no expiry or issue time.") from exc
    if expires < now - LEEWAY:
        raise OIDCError("The ID token has expired; sign in again.")
    if issued > now + LEEWAY:
        raise OIDCError("The ID token was issued in the future; check the server's clock.")
    if "nbf" in claims and float(claims["nbf"]) > now + LEEWAY:
        raise OIDCError("The ID token is not valid yet; check the server's clock.")
    if not nonce or not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("The ID token's nonce does not match this sign-in.")
    if not claims.get("sub"):
        raise OIDCError("The ID token names no subject.")
    return claims


@dataclass
class Provider:
    """One provider's discovery document and keys, read when needed and kept for a while."""

    issuer: str
    transport: Any = None  # an httpx transport, for tests
    _meta: dict[str, Any] | None = None
    _meta_at: float = 0.0
    _keys: list[dict[str, Any]] = field(default_factory=list)
    _keys_at: float = 0.0

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=10.0,
            transport=self.transport,
            headers={"User-Agent": f"Tessera/{__version__}", "Accept": "application/json"},
            follow_redirects=False,
        )

    def _get_json(self, url: str, what: str) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise OIDCError(f"Could not reach the sign-in provider for its {what}: {exc}") from exc
        if response.status_code != 200:
            raise OIDCError(f"The sign-in provider's {what} answered {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise OIDCError(f"The sign-in provider's {what} is not JSON.") from exc
        if not isinstance(data, dict):
            raise OIDCError(f"The sign-in provider's {what} is not a JSON object.")
        return data

    def metadata(self) -> dict[str, Any]:
        if self._meta is None or time.monotonic() - self._meta_at > DISCOVERY_SECONDS:
            url = self.issuer.rstrip("/") + "/.well-known/openid-configuration"
            meta = self._get_json(url, "discovery document")
            if meta.get("issuer", "").rstrip("/") != self.issuer.rstrip("/"):
                raise OIDCError(
                    f"The discovery document is for {meta.get('issuer')!r}, not {self.issuer!r}."
                )
            for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not str(meta.get(name, "")).startswith(("https://", "http://")):
                    raise OIDCError(f"The discovery document has no {name}.")
            self._meta, self._meta_at = meta, time.monotonic()
        return self._meta

    def signing_keys(self, kid: str | None = None) -> list[dict[str, Any]]:
        known = any(k.get("kid") == kid for k in self._keys) if kid else bool(self._keys)
        stale = time.monotonic() - self._keys_at > DISCOVERY_SECONDS
        may_retry = time.monotonic() - self._keys_at > KEYS_RETRY_SECONDS
        if not self._keys or stale or (not known and may_retry):
            data = self._get_json(self.metadata()["jwks_uri"], "signing keys")
            keys = data.get("keys")
            if not isinstance(keys, list):
                raise OIDCError("The provider's signing keys are not a key set.")
            self._keys, self._keys_at = [k for k in keys if isinstance(k, dict)], time.monotonic()
        return self._keys

    def authorization_url(
        self, *, client_id: str, redirect_uri: str, scopes: tuple[str, ...], state: str,
        nonce: str, challenge: str,
    ) -> str:  # fmt: skip
        endpoint = self.metadata()["authorization_endpoint"]
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"

    def exchange(
        self, *, code: str, redirect_uri: str, verifier: str, client_id: str, secret: str
    ) -> dict[str, Any]:
        """The token endpoint's answer for an authorization code."""
        meta = self.metadata()
        methods = meta.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        auth = None
        if "client_secret_basic" in methods:
            # RFC 6749, section 2.3.1: the ID and secret are form-encoded before Basic.
            auth = httpx.BasicAuth(quote(client_id, safe=""), quote(secret, safe=""))
        else:
            form |= {"client_id": client_id, "client_secret": secret}
        try:
            with self._client() as client:
                response = client.post(meta["token_endpoint"], data=form, auth=auth)
        except httpx.HTTPError as exc:
            raise OIDCError(
                f"Could not reach the sign-in provider's token endpoint: {exc}"
            ) from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code != 200:
            reason = data.get("error_description") or data.get("error") or response.status_code
            raise OIDCError(f"The sign-in provider refused the code: {reason}")
        if not isinstance(data.get("id_token"), str):
            raise OIDCError("The sign-in provider returned no ID token; is openid a scope?")
        return data

    def userinfo(self, access_token: str) -> dict[str, Any]:
        """Claims from the userinfo endpoint, or nothing when there is none or it fails."""
        url = self.metadata().get("userinfo_endpoint")
        if not url or not access_token:
            return {}
        try:
            with self._client() as client:
                response = client.get(url, headers={"Authorization": f"Bearer {access_token}"})
            data = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def verify(self, id_token: str, *, client_id: str, nonce: str) -> dict[str, Any]:
        header, *_ = decode_jwt(id_token)
        issuer = self.metadata()["issuer"]
        kid = header.get("kid")
        keys = self.signing_keys(kid)
        if kid and not any(k.get("kid") == kid for k in keys):
            # The provider may have rotated its keys since they were read: read them once more.
            self._keys_at = 0.0
            keys = self.signing_keys(kid)
        return check_id_token(id_token, keys, issuer=issuer, client_id=client_id, nonce=nonce)


__all__ = [
    "PKCE",
    "OIDCError",
    "Provider",
    "b64url_decode",
    "b64url_encode",
    "check_id_token",
    "decode_jwt",
    "rsa_verify",
]
