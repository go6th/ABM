"""SAML 2.0 service provider: the AuthnRequest and Response verification (ID1).

Signatures are verified by ``signxml`` (a vetted library); this module never handles XML
signature maths. The rules:

* parse with ``lxml`` with entity resolution, DTD loading and network access OFF, and refuse any
  document carrying a DOCTYPE; refuse encrypted assertions (not supported);
* verify the RESPONSE signature (required unless the connection says otherwise) and the
  ASSERTION signature (always required) against the connection's IdP certificate, accepting
  RSA-SHA256/384/512 and ECDSA only (no SHA-1, no HMAC);
* read every value ONLY from the element ``signxml`` returns as signed (an injected unsigned
  assertion next to a signed one is never read: the signature-wrapping defence), and require
  exactly one assertion inside the signed response;
* check issuer, destination and recipient (the exact ACS URL), audience (the SP entity id),
  ``InResponseTo`` on the response and the subject confirmation, status success, ``NotBefore``
  and ``NotOnOrAfter`` with the configured skew, against the injected clock.

The caller records the assertion id in ``saml_assertion_ids`` (unique) to refuse replays.

GUARANTEE(test_saml_negative_suite_refuses_every_forged_response)
"""

from __future__ import annotations

import base64
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from lxml import etree
from signxml.algorithms import SignatureMethod
from signxml.exceptions import InvalidInput, InvalidSignature
from signxml.verifier import SignatureConfiguration, XMLVerifier

P = "urn:oasis:names:tc:SAML:2.0:protocol"
A = "urn:oasis:names:tc:SAML:2.0:assertion"
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_comments=True,
)
_METHODS = frozenset(
    {
        SignatureMethod.RSA_SHA256,
        SignatureMethod.RSA_SHA384,
        SignatureMethod.RSA_SHA512,
        SignatureMethod.ECDSA_SHA256,
        SignatureMethod.ECDSA_SHA384,
        SignatureMethod.ECDSA_SHA512,
    }
)


class SamlError(ValueError):
    """A response that must be refused; ``reason`` is a fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SamlConnection:
    """What the SP checks a response against."""

    issuer: str
    idp_cert_pem: str
    sp_entity_id: str
    acs_url: str
    sso_url: str
    require_signed_response: bool = True


@dataclass(frozen=True)
class SamlIdentity:
    """What the broker reads from a verified assertion."""

    subject: str
    assertion_id: str
    not_on_or_after: datetime
    email: str | None
    groups: tuple[str, ...]
    authn_context: str | None


def authn_request_url(
    conn: SamlConnection,
    request_id: str,
    relay_state: str,
    now: datetime,
    force_authn: bool = False,
) -> str:
    """HTTP-Redirect binding URL for an AuthnRequest (deflated, base64, unsigned)."""
    instant = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="{P}" xmlns:saml="{A}" ID={quoteattr(request_id)}'
        f' Version="2.0" IssueInstant="{instant}" Destination={quoteattr(conn.sso_url)}'
        f" AssertionConsumerServiceURL={quoteattr(conn.acs_url)}"
        ' ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"'
        + (' ForceAuthn="true"' if force_authn else "")
        + f"><saml:Issuer>{escape(conn.sp_entity_id)}</saml:Issuer></samlp:AuthnRequest>"
    )
    deflated = zlib.compress(xml.encode())[2:-4]  # raw DEFLATE (RFC 1951), as the binding needs
    query = urlencode(
        {"SAMLRequest": base64.b64encode(deflated).decode(), "RelayState": relay_state}
    )
    sep = "&" if "?" in conn.sso_url else "?"
    return f"{conn.sso_url}{sep}{query}"


def _instant(value: str | None, what: str) -> datetime:
    if not value:
        raise SamlError(f"missing_{what}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SamlError(f"bad_{what}") from None
    if parsed.tzinfo is None:
        raise SamlError(f"bad_{what}")
    return parsed


def _verified(xml: bytes, cert: str) -> Any:
    try:
        result = XMLVerifier().verify(
            xml,
            x509_cert=cert,
            parser=_PARSER,
            expect_config=SignatureConfiguration(location="./", signature_methods=_METHODS),
        )
    except (InvalidSignature, InvalidInput):
        raise SamlError("bad_signature") from None
    except etree.XMLSyntaxError:
        raise SamlError("malformed_xml") from None
    signed = result.signed_xml if not isinstance(result, list) else None
    if signed is None:
        raise SamlError("bad_signature")
    return signed


def parse_response(
    encoded: str,
    *,
    conn: SamlConnection,
    expected_request_id: str,
    now: datetime,
    skew_s: int,
    max_bytes: int,
    groups_attribute: str = "groups",
    email_attribute: str = "email",
) -> SamlIdentity:
    """Every ID1 check on a base64 ``SAMLResponse``; SamlError(reason) on the first failure."""
    if len(encoded) > max_bytes * 4 // 3 + 4:
        raise SamlError("too_large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise SamlError("bad_encoding") from None
    if len(raw) > max_bytes:
        raise SamlError("too_large")
    if b"<!DOCTYPE" in raw.upper().replace(b" ", b"") or b"<!ENTITY" in raw.upper():
        raise SamlError("doctype_refused")
    try:
        root = etree.fromstring(raw, parser=_PARSER)
    except etree.XMLSyntaxError:
        raise SamlError("malformed_xml") from None
    if root.tag != f"{{{P}}}Response":
        raise SamlError("not_a_response")
    if root.find(f".//{{{A}}}EncryptedAssertion") is not None:
        raise SamlError("encrypted_assertion_unsupported")
    response = _verified(raw, conn.idp_cert_pem) if conn.require_signed_response else root
    if conn.require_signed_response and response.tag != f"{{{P}}}Response":
        raise SamlError("bad_signature")
    assertions = response.findall(f"{{{A}}}Assertion")
    if len(assertions) != 1:
        raise SamlError("assertion_count")
    assertion = _verified(etree.tostring(assertions[0]), conn.idp_cert_pem)
    if assertion.tag != f"{{{A}}}Assertion":
        raise SamlError("bad_signature")
    skew = timedelta(seconds=skew_s)

    def text_of(el: Any, path: str) -> str | None:
        found = el.find(path)
        return found.text.strip() if found is not None and found.text else None

    if text_of(response, f"{{{A}}}Issuer") not in (None, conn.issuer):
        raise SamlError("wrong_issuer")
    if text_of(assertion, f"{{{A}}}Issuer") != conn.issuer:
        raise SamlError("wrong_issuer")
    if response.get("Destination") not in (None, conn.acs_url):
        raise SamlError("wrong_destination")
    if response.get("InResponseTo") != expected_request_id:
        raise SamlError("in_response_to_mismatch")
    code = response.find(f"{{{P}}}Status/{{{P}}}StatusCode")
    if code is None or code.get("Value") != SUCCESS:
        raise SamlError("status_not_success")
    subject = assertion.find(f"{{{A}}}Subject")
    name_id = text_of(assertion, f"{{{A}}}Subject/{{{A}}}NameID")
    if subject is None or not name_id or len(name_id) > 200:
        raise SamlError("missing_subject")
    confirmations = [
        c for c in subject.findall(f"{{{A}}}SubjectConfirmation") if c.get("Method") == BEARER
    ]
    if len(confirmations) != 1:
        raise SamlError("subject_confirmation")
    data = confirmations[0].find(f"{{{A}}}SubjectConfirmationData")
    if data is None or data.get("Recipient") != conn.acs_url:
        raise SamlError("wrong_recipient")
    if data.get("InResponseTo") != expected_request_id:
        raise SamlError("in_response_to_mismatch")
    if _instant(data.get("NotOnOrAfter"), "not_on_or_after") + skew <= now:
        raise SamlError("expired")
    conditions = assertion.find(f"{{{A}}}Conditions")
    if conditions is None:
        raise SamlError("missing_conditions")
    if _instant(conditions.get("NotBefore"), "not_before") - skew > now:
        raise SamlError("not_yet_valid")
    expiry = _instant(conditions.get("NotOnOrAfter"), "not_on_or_after")
    if expiry + skew <= now:
        raise SamlError("expired")
    audiences = [
        a.text.strip()
        for a in conditions.findall(f"{{{A}}}AudienceRestriction/{{{A}}}Audience")
        if a.text
    ]
    if conn.sp_entity_id not in audiences:
        raise SamlError("wrong_audience")
    assertion_id = assertion.get("ID")
    if not assertion_id or len(assertion_id) > 256:
        raise SamlError("missing_assertion_id")
    attrs: dict[str, list[str]] = {}
    for attr in assertion.findall(f"{{{A}}}AttributeStatement/{{{A}}}Attribute"):
        values = [v.text.strip() for v in attr.findall(f"{{{A}}}AttributeValue") if v.text]
        attrs.setdefault(attr.get("Name") or "", []).extend(values)
    emails = attrs.get(email_attribute) or []
    email = emails[0] if emails else None
    context = text_of(
        assertion, f"{{{A}}}AuthnStatement/{{{A}}}AuthnContext/{{{A}}}AuthnContextClassRef"
    )
    return SamlIdentity(
        subject=name_id,
        assertion_id=assertion_id,
        not_on_or_after=expiry,
        email=email.lower() if email else None,
        groups=tuple(attrs.get(groups_attribute) or ())[:200],
        authn_context=context,
    )
