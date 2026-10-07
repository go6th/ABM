"""OIDC relying party: the authorization request and ID-token verification (ID1). Pure.

* ``authorization_url`` builds the request with ``state``, ``nonce`` and a PKCE S256 challenge
  for the connection's EXACT registered redirect URI.
* ``pkce_challenge`` derives the S256 challenge of a verifier (RFC 7636).
* ``verify_id_token`` checks the signature with PyJWT against the connection's key set (RS256 or
  ES256 only; never ``none`` or a shared-secret algorithm), the issuer, the audience (and ``azp``
  when there are several audiences), ``exp``, ``iat`` within the configured skew, and the nonce.
  The tenant is NEVER read from a claim: the caller already knows it from the connection.

GUARANTEE(test_oidc_negative_suite_refuses_every_forged_token)
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import jwt
from jwt import PyJWK

ALGORITHMS = ("RS256", "ES256")


class OidcError(ValueError):
    """An ID token or callback that must be refused; ``reason`` is a fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class OidcClaims:
    """What the broker reads from a verified ID token."""

    subject: str
    email: str | None
    email_verified: bool
    groups: tuple[str, ...]
    amr: tuple[str, ...]
    auth_time: datetime | None


def pkce_challenge(verifier: str) -> str:
    """The S256 code challenge of ``verifier`` (base64url, no padding)."""
    if not 43 <= len(verifier) <= 128:
        raise OidcError("pkce_verifier_length")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_verifier: str,
    force_login: bool = False,
) -> str:
    """The authorization-code request URL (scope ``openid email profile``)."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    if force_login:
        params["prompt"] = "login"
        params["max_age"] = "0"
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(params)}"


def _key_for(token: str, jwks: dict[str, Any]) -> Any:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise OidcError("malformed_token") from None
    if header.get("alg") not in ALGORITHMS:
        raise OidcError("algorithm_refused")
    kid = header.get("kid")
    keys = [k for k in jwks.get("keys", []) if isinstance(k, dict)]
    matching = [k for k in keys if k.get("kid") == kid] if kid else keys
    if len(matching) != 1:
        raise OidcError("unknown_signing_key")
    try:
        return PyJWK(matching[0]).key
    except jwt.PyJWTError:
        raise OidcError("unknown_signing_key") from None


def verify_id_token(
    token: str,
    *,
    jwks: dict[str, Any],
    issuer: str,
    client_id: str,
    nonce: str,
    now: datetime,
    skew_s: int,
    groups_claim: str = "groups",
) -> OidcClaims:
    """Every ID1 check; OidcError(reason) on the first failure."""
    key = _key_for(token, jwks)
    try:
        claims = _decode(token, key, issuer, client_id)
    except jwt.InvalidAudienceError:
        raise OidcError("wrong_audience") from None
    except jwt.InvalidIssuerError:
        raise OidcError("wrong_issuer") from None
    except jwt.MissingRequiredClaimError:
        raise OidcError("missing_claim") from None
    except jwt.InvalidSignatureError:
        raise OidcError("bad_signature") from None
    except jwt.PyJWTError:
        raise OidcError("invalid_token") from None
    aud = claims["aud"]
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id:
        raise OidcError("wrong_authorized_party")
    stamp = now.timestamp()
    if int(claims["exp"]) + skew_s <= stamp:
        raise OidcError("expired")
    if int(claims["iat"]) - skew_s > stamp:
        raise OidcError("issued_in_future")
    if "nbf" in claims and int(claims["nbf"]) - skew_s > stamp:
        raise OidcError("not_yet_valid")
    if not isinstance(claims.get("nonce"), str) or claims["nonce"] != nonce:
        raise OidcError("nonce_mismatch")
    sub = claims["sub"]
    if not isinstance(sub, str) or not 1 <= len(sub) <= 200:
        raise OidcError("bad_subject")
    groups = claims.get(groups_claim) or []
    amr = claims.get("amr") or []
    auth_time = claims.get("auth_time")
    return OidcClaims(
        subject=sub,
        email=str(claims["email"]).lower() if isinstance(claims.get("email"), str) else None,
        email_verified=claims.get("email_verified") is True,
        groups=tuple(str(g) for g in groups if isinstance(g, str))[:200],
        amr=tuple(str(a) for a in amr if isinstance(a, str))[:20],
        auth_time=(
            datetime.fromtimestamp(auth_time, tz=now.tzinfo)
            if isinstance(auth_time, int) and not isinstance(auth_time, bool)
            else None
        ),
    )


def _decode(token: str, key: Any, issuer: str, client_id: str) -> dict[str, Any]:
    """Signature, issuer and audience by PyJWT; time claims are checked by the caller against
    the injected clock (PyJWT would read the wall clock)."""
    decoded: dict[str, Any] = jwt.decode(
        token,
        key,
        algorithms=list(ALGORITHMS),
        audience=client_id,
        issuer=issuer,
        options={
            "require": ["iss", "aud", "exp", "iat", "sub", "nonce"],
            "verify_exp": False,
            "verify_iat": False,
            "verify_nbf": False,
        },
    )
    if not isinstance(decoded, dict):
        raise OidcError("invalid_token")
    return decoded
