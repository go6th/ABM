"""Local-stack identity provider (compose service ``fake-idp``). SYNTHETIC tokens only.

Serves OIDC discovery and a JWKS for the API's bearer-token verifier, and mints RS256 tokens
with the claims ``api/auth.py`` requires (``sub``, ``tenant_id``, ``role``, ``exp``, ``iat``,
optional ``amr``/``auth_time``). The signing key is generated at start and never leaves the
process. It is mounted into the local stack only and is not part of any image.

``POST /token`` JSON ``{"sub": "...", "tenant_id": "<uuid>", "role": "admin", "mfa": true}``
returns ``{"access_token": ..., "token_type": "Bearer", "expires_in": 3600}``.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = os.environ.get("FAKE_IDP_ISSUER", "http://fake-idp:9090/")
AUDIENCE = os.environ.get("FAKE_IDP_AUDIENCE", "account-intelligence-local")
ROLES = {"admin", "analyst", "seller", "viewer"}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "local-1"


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwks() -> dict[str, object]:
    """The public key as a JWK set."""
    pub = KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "use": "sig",
                "alg": "RS256",
                "n": _b64(pub.n),
                "e": _b64(pub.e),
            }
        ]
    }


def mint(body: dict[str, object]) -> str:
    """A signed token for a validated request body (ValueError on bad input)."""
    sub, tenant, role = body.get("sub"), body.get("tenant_id"), body.get("role")
    if not isinstance(sub, str) or not sub or role not in ROLES:
        raise ValueError("sub and a known role are required")
    UUID(str(tenant))
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "tenant_id": str(tenant),
        "role": role,
        "iat": now,
        "exp": now + 3600,
    }
    if body.get("mfa") is True:
        claims |= {"amr": ["pwd", "mfa"], "auth_time": now}
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": KID})


class Handler(BaseHTTPRequestHandler):
    """Discovery, JWKS and the token endpoint."""

    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        """Discovery and keys."""
        if self.path == "/.well-known/openid-configuration":
            self._send(
                200,
                {
                    "issuer": ISSUER,
                    "jwks_uri": f"{ISSUER.rstrip('/')}/jwks",
                    "token_endpoint": f"{ISSUER.rstrip('/')}/token",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        elif self.path == "/jwks":
            self._send(200, jwks())
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        """Mint a token."""
        if self.path != "/token":
            self._send(404, {"error": "not_found"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            token = mint(json.loads(self.rfile.read(length) or b"{}"))
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send(400, {"error": "invalid_request"})
            return
        self._send(200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600})

    def log_message(self, *_: object) -> None:
        """Quiet: tokens are never logged."""


def main() -> int:
    """Serve on 0.0.0.0:9090 (local stack only)."""
    if os.environ.get("APP_ENV", "dev") not in ("dev", "test"):
        print("fake-idp: refused outside dev/test", file=sys.stderr)
        return 2
    ThreadingHTTPServer(("0.0.0.0", 9090), Handler).serve_forever()  # nosec B104 - container port
    return 0


if __name__ == "__main__":
    sys.exit(main())
