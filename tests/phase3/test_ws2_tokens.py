"""WS2 token verification (ID1): the OIDC and SAML negative suites. Each case is a lying IdP or an
attacker in the middle; every one must be refused with its reason code. SYNTHETIC keys only."""

from __future__ import annotations

import base64
import datetime as dt
from typing import Any

import jwt
import pytest
from lxml import etree

from identity import oidc, saml
from tests.fakes.idp import A, FakeOidcIdp, FakeSamlIdp, pem, rsa_key

NOW = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
T = int(NOW.timestamp())
IDP = FakeOidcIdp()
OTHER_KEY = rsa_key()


def _claims(**over: Any) -> dict[str, Any]:
    base = {
        "iss": IDP.issuer,
        "aud": IDP.client_id,
        "sub": "ann",
        "iat": T,
        "exp": T + 300,
        "nonce": "n" * 43,
        "email": "ann@synthetic.test",
        "email_verified": True,
        "groups": ["sales"],
        "amr": ["pwd", "mfa"],
        "auth_time": T,
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _verify(token: str, nonce: str = "n" * 43) -> oidc.OidcClaims:
    return oidc.verify_id_token(
        token,
        jwks={"keys": [IDP.jwk()]},
        issuer=IDP.issuer,
        client_id=IDP.client_id,
        nonce=nonce,
        now=NOW,
        skew_s=60,
    )


def test_a_valid_id_token_is_accepted() -> None:
    got = _verify(IDP.id_token(_claims()))
    assert (got.subject, got.email, got.email_verified, got.groups) == (
        "ann",
        "ann@synthetic.test",
        True,
        ("sales",),
    )
    assert "mfa" in got.amr and got.auth_time == NOW
    # within the skew either side
    assert _verify(IDP.id_token(_claims(iat=T + 59, exp=T - 59))).subject == "ann"


def _hs_with_public_key() -> str:
    public = IDP.key.public_key().public_bytes(
        __import__("cryptography.hazmat.primitives.serialization", fromlist=["x"]).Encoding.PEM,
        __import__(
            "cryptography.hazmat.primitives.serialization", fromlist=["x"]
        ).PublicFormat.SubjectPublicKeyInfo,
    )
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT","kid":"k1"}').rstrip(b"=")
    import hashlib
    import hmac
    import json

    body = base64.urlsafe_b64encode(json.dumps(_claims()).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(
        hmac.new(public, header + b"." + body, hashlib.sha256).digest()
    ).rstrip(b"=")
    return (header + b"." + body + b"." + sig).decode()


def _unsigned() -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=")
    import json

    body = base64.urlsafe_b64encode(json.dumps(_claims()).encode()).rstrip(b"=")
    return (header + b"." + body + b".").decode()


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (lambda: IDP.id_token(_claims(iss="https://evil.test")), "wrong_issuer"),
        (lambda: IDP.id_token(_claims(aud="another-client")), "wrong_audience"),
        (
            lambda: IDP.id_token(_claims(aud=[IDP.client_id, "x"], azp="x")),
            "wrong_authorized_party",
        ),
        (lambda: IDP.id_token(_claims(exp=T - 61)), "expired"),
        (lambda: IDP.id_token(_claims(iat=T + 120)), "issued_in_future"),
        (lambda: IDP.id_token(_claims(nbf=T + 120)), "not_yet_valid"),
        (lambda: IDP.id_token(_claims(nonce="replayed-nonce")), "nonce_mismatch"),
        (lambda: IDP.id_token(_claims(nonce=None)), "missing_claim"),
        (lambda: IDP.id_token(_claims(sub=None)), "missing_claim"),
        (lambda: IDP.id_token(_claims(), key=pem(OTHER_KEY)), "bad_signature"),
        (lambda: IDP.id_token(_claims(), kid="unknown"), "unknown_signing_key"),
        (_unsigned, "algorithm_refused"),
        (_hs_with_public_key, "algorithm_refused"),
        (lambda: "not.a.jwt", "malformed_token"),
        (lambda: IDP.id_token(_claims(sub="x" * 201)), "bad_subject"),
    ],
)
def test_oidc_negative_suite_refuses_every_forged_token(token: Any, reason: str) -> None:
    with pytest.raises(oidc.OidcError) as refused:
        _verify(token())
    assert refused.value.reason == reason


def test_a_tampered_payload_fails_the_signature() -> None:
    good = IDP.id_token(_claims())
    head, body, sig = good.split(".")
    forged = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(body).replace(b'"ann"', b'"eve"')
    ).decode()
    with pytest.raises(oidc.OidcError) as refused:
        _verify(f"{head}.{forged}.{sig}")
    assert refused.value.reason == "bad_signature"


def test_the_tenant_claim_is_never_read() -> None:
    """A token claiming another tenant verifies, but OidcClaims has no tenant field at all: the
    broker takes the tenant from the connection (tested end to end in the API suite)."""
    got = _verify(IDP.id_token(_claims(tenant_id="11111111-1111-1111-1111-111111111111")))
    assert not hasattr(got, "tenant_id")


def test_pkce_and_authorization_url() -> None:
    url = oidc.authorization_url(
        authorization_endpoint="https://idp.synthetic.test/authorize?x=1",
        client_id="c",
        redirect_uri="https://app.test/v1/sso/oidc/callback",
        state="s" * 43,
        nonce="n" * 43,
        code_verifier="v" * 43,
        force_login=True,
    )
    assert "x=1&response_type=code" in url and "code_challenge_method=S256" in url
    assert "prompt=login" in url and "max_age=0" in url
    assert oidc.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == (
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # RFC 7636 appendix B (read 2026-10-07)
    )
    with pytest.raises(oidc.OidcError):
        oidc.pkce_challenge("short")


# ---------------------------------------------------------------- SAML

SAML = FakeSamlIdp()
ACS = "https://app.test/v1/sso/saml/acs"
SP = "https://app.test/saml/sp"
CONN = saml.SamlConnection(
    issuer=SAML.issuer,
    idp_cert_pem=SAML.cert,
    sp_entity_id=SP,
    acs_url=ACS,
    sso_url="https://saml-idp.synthetic.test/sso",
)


def _resp(**kw: Any) -> str:
    return SAML.response(
        request_id="_req1",
        acs_url=ACS,
        audience=SP,
        subject="ann",
        now=NOW,
        email="Ann@Synthetic.test",
        **kw,
    )


def _parse(encoded: str, conn: saml.SamlConnection = CONN, request_id: str = "_req1") -> Any:
    return saml.parse_response(
        encoded, conn=conn, expected_request_id=request_id, now=NOW, skew_s=60, max_bytes=262144
    )


def test_a_valid_saml_response_is_accepted() -> None:
    got = _parse(_resp())
    assert (got.subject, got.email, got.groups) == ("ann", "ann@synthetic.test", ("sales",))
    assert got.authn_context and "MultiFactor" in got.authn_context
    url = saml.authn_request_url(CONN, "_req1", "relay", NOW, force_authn=True)
    assert url.startswith(CONN.sso_url + "?SAMLRequest=") and "RelayState=relay" in url


def _wrap_evil_assertion(data: bytes) -> bytes:
    """Signature wrapping: insert an unsigned assertion for 'eve' before the signed one."""
    root = etree.fromstring(data)
    signed = root.find(f"{{{A}}}Assertion")
    evil = etree.fromstring(etree.tostring(signed))
    for sig in evil.findall("{http://www.w3.org/2000/09/xmldsig#}Signature"):
        evil.remove(sig)
    evil.set("ID", "_evil")
    evil.find(f"{{{A}}}Subject/{{{A}}}NameID").text = "eve"
    root.insert(list(root).index(signed), evil)
    return etree.tostring(root)


def _wrap_in_extensions(data: bytes) -> bytes:
    """Signature wrapping: move the signed assertion under an Extensions element and put an
    evil one where the reader looks."""
    root = etree.fromstring(data)
    signed = root.find(f"{{{A}}}Assertion")
    root.remove(signed)
    ext = etree.SubElement(root, "{urn:oasis:names:tc:SAML:2.0:protocol}Extensions")
    ext.append(signed)
    evil = etree.fromstring(etree.tostring(signed))
    evil.find(f"{{{A}}}Subject/{{{A}}}NameID").text = "eve"
    root.append(evil)
    return etree.tostring(root)


def _xxe(data: bytes) -> bytes:
    return b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + data


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda: _resp(sign_assertion=False), "bad_signature"),
        (lambda: _resp(sign_response=False), "bad_signature"),
        (lambda: _resp(key=rsa_key()), "bad_signature"),
        (lambda: _resp(after_sign=lambda d: d.replace(b">ann<", b">eve<")), "bad_signature"),
        (lambda: _resp(after_sign=_wrap_evil_assertion), "bad_signature"),
        (lambda: _resp(after_sign=_wrap_in_extensions), "bad_signature"),
        (lambda: _resp(after_sign=_xxe), "doctype_refused"),
        (lambda: _resp(fields={"audience": "https://other-sp.test"}), "wrong_audience"),
        (lambda: _resp(fields={"recipient": "https://evil.test/acs"}), "wrong_recipient"),
        (lambda: _resp(fields={"destination": "https://evil.test/acs"}), "wrong_destination"),
        (lambda: _resp(fields={"in_response_to": "_other"}), "in_response_to_mismatch"),
        (lambda: _resp(fields={"issuer": "https://evil-idp.test"}), "wrong_issuer"),
        (
            lambda: _resp(fields={"status": "urn:oasis:names:tc:SAML:2.0:status:Requester"}),
            "status_not_success",
        ),
        (lambda: _resp(fields={"not_on_or_after": "2026-03-02T08:58:00Z"}), "expired"),
        (lambda: _resp(fields={"not_before": "2026-03-02T09:02:00Z"}), "not_yet_valid"),
        (lambda: _resp(fields={"not_on_or_after": "yesterday"}), "bad_not_on_or_after"),
        (lambda: "%%%not-base64%%%", "bad_encoding"),
        (lambda: base64.b64encode(b"<x/>").decode(), "not_a_response"),
        (lambda: base64.b64encode(b"<unclosed").decode(), "malformed_xml"),
        (lambda: base64.b64encode(b"x" * 300000).decode(), "too_large"),
    ],
)
def test_saml_negative_suite_refuses_every_forged_response(make: Any, reason: str) -> None:
    with pytest.raises(saml.SamlError) as refused:
        _parse(make())
    assert refused.value.reason == reason


def test_saml_missing_in_response_to_and_wrong_certificate() -> None:
    with pytest.raises(saml.SamlError) as refused:
        _parse(_resp(), request_id="_another_request")
    assert refused.value.reason == "in_response_to_mismatch"
    other = saml.SamlConnection(
        issuer=SAML.issuer,
        idp_cert_pem=FakeSamlIdp().cert,
        sp_entity_id=SP,
        acs_url=ACS,
        sso_url=CONN.sso_url,
    )
    with pytest.raises(saml.SamlError) as wrong_cert:
        _parse(_resp(), conn=other)
    assert wrong_cert.value.reason == "bad_signature"


def test_saml_assertion_only_signature_when_the_connection_allows_it() -> None:
    lenient = saml.SamlConnection(
        issuer=SAML.issuer,
        idp_cert_pem=SAML.cert,
        sp_entity_id=SP,
        acs_url=ACS,
        sso_url=CONN.sso_url,
        require_signed_response=False,
    )
    assert _parse(_resp(sign_response=False), conn=lenient).subject == "ann"
    with pytest.raises(saml.SamlError):
        _parse(_resp(sign_response=False, sign_assertion=False), conn=lenient)


# ---------------------------------------------------------------- the HTTPS IdP adapter


def test_idp_calls_refuse_private_addresses_and_redirects() -> None:
    import asyncio

    import httpx

    from identity.ports import HttpIdentityProvider, IdentityProviderError, OidcEndpoints

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/token":
            return httpx.Response(200, json={"id_token": "tok", "access_token": "x"})
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [{"kid": "k"}]})
        if request.url.path == "/badjwks":
            return httpx.Response(200, json={"nokeys": True})
        return httpx.Response(500, text="secret internals")

    def adapter(addresses: list[str]) -> HttpIdentityProvider:
        async def resolve(host: str) -> list[str]:
            return addresses

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return HttpIdentityProvider(client, resolve)

    def conn(
        token: str = "https://idp.example/token", jwks: str = "https://idp.example/jwks"
    ) -> OidcEndpoints:
        return OidcEndpoints("https://idp.example", "c", token, jwks)

    async def run() -> None:
        public = adapter(["93.184.216.34"])
        assert (
            await public.exchange_code(
                conn(),
                code="c",
                code_verifier="v" * 43,
                redirect_uri="https://app.test/cb",
                client_secret="s3cret",
            )
            == "tok"
        )
        assert (await public.jwks(conn()))["keys"]
        cases = [
            (adapter(["127.0.0.1"]), conn(), "idp_endpoint_refused"),
            (adapter(["10.1.2.3"]), conn(), "idp_endpoint_refused"),
            (adapter(["169.254.169.254"]), conn(), "idp_endpoint_refused"),
            (adapter(["::1"]), conn(), "idp_endpoint_refused"),
            (adapter(["93.184.216.34", "192.168.0.1"]), conn(), "idp_endpoint_refused"),
            (adapter([]), conn(), "idp_unreachable"),
            (public, conn(token="http://idp.example/token"), "idp_endpoint_refused"),
            (public, conn(token="https://user:pw@idp.example/token"), "idp_endpoint_refused"),
            (public, conn(token="https://idp.example/redirect"), "idp_refused_code"),
            (public, conn(token="https://idp.example/error"), "idp_refused_code"),
        ]
        for idp, endpoints, reason in cases:
            with pytest.raises(IdentityProviderError) as refused:
                await idp.exchange_code(
                    endpoints,
                    code="c",
                    code_verifier="v" * 43,
                    redirect_uri="https://app.test/cb",
                    client_secret=None,
                )
            assert refused.value.reason == reason
            assert "secret internals" not in str(refused.value)
        with pytest.raises(IdentityProviderError) as bad:
            await public.jwks(conn(jwks="https://idp.example/badjwks"))
        assert bad.value.reason == "idp_bad_jwks"
        with pytest.raises(IdentityProviderError):
            await adapter(["10.0.0.1"]).jwks(conn())
        assert not any("169.254" in u for u in seen)  # a redirect is never followed

    asyncio.run(run())


def test_idp_connections_go_to_the_address_that_was_checked() -> None:
    """L17: the TCP connection is opened to the address the check approved, resolved once in the
    same step (no second DNS answer), on port 443 only."""
    import asyncio

    from identity.ports import (
        IdentityProviderError,
        PinnedBackend,
        pinned_client,
        require_public_https,
    )

    answers = [["93.184.216.34"], ["127.0.0.1"]]
    asked: list[str] = []
    connected: list[tuple[str, int]] = []

    async def resolve(host: str) -> list[str]:
        asked.append(host)
        return answers.pop(0)

    class Inner:
        async def connect_tcp(self, host: str, port: int, **_: object) -> str:
            connected.append((host, port))
            return "stream"

        async def sleep(self, seconds: float) -> None:
            return None

    async def run() -> None:
        backend = PinnedBackend(resolve, Inner())
        assert await backend.connect_tcp("idp.example", 443) == "stream"
        with pytest.raises(IdentityProviderError):  # the next answer is private: never used
            await backend.connect_tcp("idp.example", 443)
        with pytest.raises(IdentityProviderError):
            await backend.connect_tcp("idp.example", 8443)
        with pytest.raises(IdentityProviderError):
            await require_public_https("https://idp.example:8443/token", resolve)

    asyncio.run(run())
    assert connected == [("93.184.216.34", 443)] and asked == ["idp.example", "idp.example"]
    client = pinned_client()
    assert isinstance(client._transport._pool._network_backend, PinnedBackend)
    assert client.follow_redirects is False
