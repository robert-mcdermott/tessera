"""A scripted OpenID Connect provider for the single sign-on tests: a discovery document, its
signing keys, an authorization step, a token endpoint that checks PKCE and the client's secret
the way a real provider does, and a userinfo endpoint. Its RSA key is a test key made with
OpenSSL for these tests alone."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

ISSUER = "https://idp.test"
CLIENT_ID = "tessera-test"
SECRET = "client-secret-for-tests"
KID = "test-key-1"

N = int(
    "f5903ca803634e01f6077ad31f824d6398d3d065503f5a25563cc34cbd8cc0b56eec80e868fde2634e3511ce"
    "2e45a83fb0fa475bd362f424038d89fd026e000433ba9e1a20ecf690beedb89a18819f67630fc977b08e3ec6"
    "424b778b6ce8be1286caacd989809c50583ed6015dc6c57c419cb9ddf1c9c90c2cc2123681c408f0760de4a7"
    "f003afaf70b7365c3bf206e1542f19d30a165067c8c79831fb0de00d3c98b89e0d1733ed4850c3e755f7e8f8"
    "604c46889cda3f116cf2f9484569412dc3d8927e269e12bb46932d4989bb2c29e879edce50b9e4f2f16888b6"
    "9ccd63af6b11db1f0a832b9e8053fba820d34aff8d3a5d29fffedbae854219f14799e7d5",
    16,
)
E = 65537
D = int(
    "1dc539095071d556f906129eae52fc815ed98d09ff56d2ddb46d04eb548e901d5edf86c846544342314ccf46"
    "0d078e5c7c5ef7503b5a23898b31b961264e822b803ef7646909f961767838f95b8fb96cce0e6fa9164b4472"
    "823e86756d8470e8ab953e2bd856f27c6e1a5e1507d2ad86012958423f4a2c98d0834db4747a78efbb40a9cf"
    "1b6fa5f31ce4bb9f05cd9726790e52d0316d31f1669dc4f4f6de6008c386d2c6bfa720548d330cf6326d7bcf"
    "02d7bf38acec03771b9caf34c0e73aa3498d146daf2287e23d2618dd7ccd8e2f615741692ae3a09a11c072cb"
    "84d7311f21c9c6f2d0b83678e6079ed8520fef4dd9b3a074f20b29e3489cfc520bf5a2f9",
    16,
)
# An RS256 signature OpenSSL made of OPENSSL_MESSAGE with this key (openssl dgst -sha256 -sign).
OPENSSL_MESSAGE = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0"
OPENSSL_SIGNATURE = bytes.fromhex(
    "5cf5478fe2e0df9295631b5734e8494b3d7507c68c4b3360e5d4eaab35b5ded3a76fae18415838b69d21d68e"
    "bc644701853999c5ddd9bda094d1f46794933e45bc2bb8431ffae36013a84e77e655fd29d25b157b3373d511"
    "ea13d332140b507f6ed2b8ede66ea3507f531eab55f5aba4bf68666eb87fa5f82e53b2ca137a0c15827aaeb5"
    "96b7e0d7c2bfd28595155016974a2d4eb4501cd97e289df7f9c434b7f8213a434dd0957e04407c3f6d5c9067"
    "120290ffd2517cf4e0bbf3e57e356e0a03b4db2a5082527eb926ea2510f9dd9207a08e7a330f529ea38928bb"
    "32b9e2f3f4818285761a1850c3b4fe284822fca8e7ebb4df722e8e8b40cc116eb2b0028c"
)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(header: dict[str, Any], claims: dict[str, Any], *, key: int = D) -> str:
    """A JWT signed with RS256 (RSASSA-PKCS1-v1_5 with SHA-256)."""
    head = b64(json.dumps(header).encode())
    body = b64(json.dumps(claims).encode())
    size = (N.bit_length() + 7) // 8
    info = (
        bytes.fromhex("3031300d060960864801650304020105000420")
        + hashlib.sha256(f"{head}.{body}".encode()).digest()
    )
    encoded = b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info
    signature = pow(int.from_bytes(encoded, "big"), key, N).to_bytes(size, "big")
    return f"{head}.{body}.{b64(signature)}"


@dataclass
class Grant:
    claims: dict[str, Any]
    nonce: str
    challenge: str
    redirect_uri: str


@dataclass
class FakeProvider:
    """``user`` is who signs in next; ``tamper`` changes the next ID token's claims or header."""

    user: dict[str, Any] = field(default_factory=dict)
    tamper: dict[str, Any] = field(default_factory=dict)
    codes: dict[str, Grant] = field(default_factory=dict)
    token_calls: int = 0

    def metadata(self) -> dict[str, Any]:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
            "userinfo_endpoint": f"{ISSUER}/userinfo",
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    def authorize(self, url: str) -> str:
        """The provider's sign-in page: the person signs in as ``user``; returns the code."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert query["response_type"] == "code" and query["client_id"] == CLIENT_ID
        assert query["code_challenge_method"] == "S256" and "openid" in query["scope"].split()
        code = secrets.token_urlsafe(16)
        self.codes[code] = Grant(
            dict(self.user), query["nonce"], query["code_challenge"], query["redirect_uri"]
        )
        return code

    def id_token(self, grant: Grant) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300,
                  "nonce": grant.nonce, **grant.claims}  # fmt: skip
        header = {"alg": "RS256", "kid": KID, "typ": "JWT"}
        tamper = dict(self.tamper)
        key = tamper.pop("_key", D)
        header |= tamper.pop("_header", {})
        claims |= tamper
        token = sign(header, claims, key=key)
        if tamper.get("_bad_signature"):
            head, body, sig = token.split(".")
            token = f"{head}.{body}.{sig[:-4]}AAAA"
        return token

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.metadata())
        if path == "/jwks":
            key = {"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256",
                   "n": b64(N.to_bytes(256, "big")), "e": b64(E.to_bytes(3, "big"))}  # fmt: skip
            return httpx.Response(200, json={"keys": [key]})
        if path == "/token":
            self.token_calls += 1
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
            if request.headers.get("authorization") != expected:
                return httpx.Response(401, json={"error": "invalid_client"})
            grant = self.codes.pop(form.get("code", ""), None)
            if grant is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            if b64(hashlib.sha256(verifier.encode()).digest()) != grant.challenge:
                failed = {"error": "invalid_grant", "error_description": "PKCE check failed"}
                return httpx.Response(400, json=failed)
            if form.get("redirect_uri") != grant.redirect_uri:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"id_token": self.id_token(grant),
                                             "access_token": "at-" + grant.nonce,
                                             "token_type": "Bearer"})  # fmt: skip
        if path == "/userinfo":
            return httpx.Response(200, json={"sub": self.user.get("sub")})
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)
