"""Fake identity providers (SYNTHETIC; tests only).

``FakeOidcIdp`` is both the IdP a browser would visit (``authorize`` reads the request URL and
mints a one-time code bound to the redirect URI and PKCE challenge) and the
``identity.ports.IdentityProvider`` port the broker calls (``exchange_code`` enforces the
verifier and the exact redirect URI, ``jwks`` serves the public key). ``claims`` lets a test forge
any claim; ``sign_with`` lets it sign with another key.

``FakeSamlIdp`` builds signed SAML responses with ``signxml`` and a self-signed certificate;
``mutate`` hooks let a test forge any field or the XML after signing.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner

from identity.ports import IdentityProviderError, OidcEndpoints

EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"


def rsa_key() -> rsa.RSAPrivateKey:
    """A fresh 2048-bit RSA key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def self_signed(key: rsa.RSAPrivateKey, name: str = "fake-idp") -> str:
    """A PEM self-signed certificate for ``key``."""
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def pem(key: rsa.RSAPrivateKey) -> bytes:
    """The private key as PKCS8 PEM."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


@dataclass
class FakeOidcIdp:
    """A scripted OIDC provider."""

    issuer: str = "https://idp.synthetic.test"
    client_id: str = "client-synthetic"
    key: rsa.RSAPrivateKey = field(default_factory=rsa_key)
    kid: str = "k1"
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    exchanged: list[str] = field(default_factory=list)

    def jwk(self) -> dict[str, Any]:
        """The public key as a JWK."""
        data: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        data.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return data

    def id_token(
        self,
        claims: dict[str, Any],
        *,
        key: Any = None,
        alg: str = "RS256",
        kid: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> str:
        """Sign ``claims`` (any forgery is the caller's choice)."""
        signing = key if key is not None else pem(self.key)
        return jwt.encode(
            claims, signing, algorithm=alg, headers={"kid": kid or self.kid, **(headers or {})}
        )

    def authorize(
        self,
        url: str,
        *,
        subject: str,
        now: dt.datetime,
        extra: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[str, str]:
        """What the IdP does when the browser arrives: returns (state, code) for the callback."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        claims = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": subject,
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 300,
            "nonce": q["nonce"],
            "email": f"{subject}@synthetic.test",
            "email_verified": True,
            "groups": ["sales"],
            "amr": ["pwd", "mfa"],
            "auth_time": int(now.timestamp()),
            **(extra or {}),
        }
        code = secrets.token_urlsafe(16)
        self.codes[code] = {
            "redirect_uri": q["redirect_uri"],
            "challenge": q["code_challenge"],
            "token": token if token is not None else self.id_token(claims),
        }
        return q["state"], code

    async def exchange_code(
        self,
        conn: OidcEndpoints,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_secret: str | None,
    ) -> str:
        """The token endpoint: one use per code, exact redirect URI, PKCE S256."""
        entry = self.codes.pop(code, None)
        if entry is None:
            raise IdentityProviderError("idp_refused_code")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if entry["redirect_uri"] != redirect_uri or entry["challenge"] != challenge:
            raise IdentityProviderError("idp_refused_code")
        self.exchanged.append(code)
        return str(entry["token"])

    async def jwks(self, conn: OidcEndpoints) -> dict[str, Any]:
        """The public key set."""
        return {"keys": [self.jwk()]}


P = "urn:oasis:names:tc:SAML:2.0:protocol"
A = "urn:oasis:names:tc:SAML:2.0:assertion"


@dataclass
class FakeSamlIdp:
    """A scripted SAML IdP that signs with signxml."""

    issuer: str = "https://saml-idp.synthetic.test"
    key: rsa.RSAPrivateKey = field(default_factory=rsa_key)
    cert: str = ""

    def __post_init__(self) -> None:
        if not self.cert:
            self.cert = self_signed(self.key)

    def response(
        self,
        *,
        request_id: str,
        acs_url: str,
        audience: str,
        subject: str,
        now: dt.datetime,
        groups: tuple[str, ...] = ("sales",),
        email: str | None = None,
        mfa: bool = True,
        sign_assertion: bool = True,
        sign_response: bool = True,
        fields: dict[str, str] | None = None,
        after_sign: Callable[[bytes], bytes] | None = None,
        key: rsa.RSAPrivateKey | None = None,
    ) -> str:
        """A base64 SAMLResponse; ``fields`` overrides any templated value before signing."""
        f = {
            "issuer": self.issuer,
            "destination": acs_url,
            "recipient": acs_url,
            "in_response_to": request_id,
            "audience": audience,
            "subject": subject,
            "status": "urn:oasis:names:tc:SAML:2.0:status:Success",
            "not_before": (now - dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "not_on_or_after": (now + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "assertion_id": "_a" + secrets.token_hex(16),
            "context": "urn:oasis:names:tc:SAML:2.0:ac:classes:MultiFactor"
            if mfa
            else "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            **(fields or {}),
        }
        attrs = "".join(f"<saml:AttributeValue>{g}</saml:AttributeValue>" for g in groups)
        email_attr = (
            f'<saml:Attribute Name="email"><saml:AttributeValue>{email}</saml:AttributeValue>'
            "</saml:Attribute>"
            if email
            else ""
        )
        assertion = (
            f'<saml:Assertion xmlns:saml="{A}" ID="{f["assertion_id"]}" Version="2.0"'
            f' IssueInstant="{f["not_before"]}"><saml:Issuer>{f["issuer"]}</saml:Issuer>'
            f"<saml:Subject><saml:NameID>{f['subject']}</saml:NameID>"
            '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData Recipient="{f["recipient"]}"'
            f' InResponseTo="{f["in_response_to"]}" NotOnOrAfter="{f["not_on_or_after"]}"/>'
            "</saml:SubjectConfirmation></saml:Subject>"
            f'<saml:Conditions NotBefore="{f["not_before"]}"'
            f' NotOnOrAfter="{f["not_on_or_after"]}"><saml:AudienceRestriction>'
            f"<saml:Audience>{f['audience']}</saml:Audience></saml:AudienceRestriction>"
            "</saml:Conditions><saml:AuthnStatement><saml:AuthnContext>"
            f"<saml:AuthnContextClassRef>{f['context']}</saml:AuthnContextClassRef>"
            "</saml:AuthnContext></saml:AuthnStatement><saml:AttributeStatement>"
            f'<saml:Attribute Name="groups">{attrs}</saml:Attribute>{email_attr}'
            "</saml:AttributeStatement></saml:Assertion>"
        )
        signing = pem(key or self.key)
        signer = XMLSigner(c14n_algorithm=EXC_C14N)
        a_el = etree.fromstring(assertion.encode())
        if sign_assertion:
            a_el = signer.sign(a_el, key=signing, cert=self.cert, reference_uri=f["assertion_id"])
        resp_id = "_r" + secrets.token_hex(16)
        response = etree.fromstring(
            (
                f'<samlp:Response xmlns:samlp="{P}" xmlns:saml="{A}" ID="{resp_id}"'
                f' Version="2.0" IssueInstant="{f["not_before"]}"'
                f' Destination="{f["destination"]}" InResponseTo="{f["in_response_to"]}">'
                f"<saml:Issuer>{f['issuer']}</saml:Issuer><samlp:Status>"
                f'<samlp:StatusCode Value="{f["status"]}"/></samlp:Status></samlp:Response>'
            ).encode()
        )
        response.append(a_el)
        if sign_response:
            response = signer.sign(response, key=signing, cert=self.cert, reference_uri=resp_id)
        data = etree.tostring(response)
        if after_sign:
            data = after_sign(data)
        return base64.b64encode(data).decode()
