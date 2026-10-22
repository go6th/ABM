"""Google Workspace staff identity and role-claim unit tests."""

from datetime import UTC, datetime

import jwt
import pytest

from account_intelligence.api.operator import JwtStaffVerifier


class _Key:
    key = "test-only-signing-key"


class _Jwks:
    def get_signing_key_from_jwt(self, _token: str) -> _Key:
        return _Key()


def _verifier(monkeypatch: pytest.MonkeyPatch, claims: dict[str, object]) -> JwtStaffVerifier:
    verifier = JwtStaffVerifier(
        "https://accounts.google.com",
        "operator-client-id",
        "https://www.googleapis.com/oauth2/v3/certs",
        workspace_domain="sixth.example",
        operator_emails=frozenset({"operator@sixth.example"}),
        support_emails=frozenset({"support@sixth.example", "named.person@gmail.com"}),
        external_emails=frozenset({"named.person@gmail.com"}),
    )
    verifier.jwks = _Jwks()  # type: ignore[assignment]
    monkeypatch.setattr(jwt, "decode", lambda *_args, **_kwargs: claims)
    return verifier


def _claims(**updates: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": "operator-client-id",
        "sub": "google-stable-subject",
        "iat": 1_800_000_000,
        "exp": 1_800_000_600,
        "email": "operator@sixth.example",
        "email_verified": True,
        "hd": "sixth.example",
        "auth_time": int(datetime.now(UTC).timestamp()),
        "amr": ["pwd", "mfa"],
    }
    claims.update(updates)
    return claims


def test_google_workspace_operator_email_maps_to_staff_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _verifier(monkeypatch, _claims()).verify("header.payload.signature")
    assert identity.staff_id == "google-stable-subject"
    assert identity.role == "operator"
    assert identity.step_up_at is not None


@pytest.mark.parametrize(
    "updates",
    [
        {"hd": "attacker.example"},
        {"hd": None},
        {"email_verified": False},
        {"email": "other@sixth.example"},
    ],
)
def test_google_workspace_role_allowlist_rejects_unverified_or_unlisted_users(
    monkeypatch: pytest.MonkeyPatch, updates: dict[str, object]
) -> None:
    with pytest.raises(PermissionError):
        _verifier(monkeypatch, _claims(**updates)).verify("header.payload.signature")


def test_a_google_sign_in_steps_up_at_its_own_time_without_amr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Google never sends amr: the step-up is the Google sign-in itself (auth_time, else iat)
    signed_in = int(datetime.now(UTC).timestamp()) - 30
    claims = _claims(auth_time=signed_in)
    del claims["amr"]
    identity = _verifier(monkeypatch, claims).verify("header.payload.signature")
    assert identity.step_up_at == datetime.fromtimestamp(signed_in, tz=UTC)
    claims = _claims(iat=signed_in)
    del claims["amr"], claims["auth_time"]
    identity = _verifier(monkeypatch, claims).verify("header.payload.signature")
    assert identity.step_up_at == datetime.fromtimestamp(signed_in, tz=UTC)


def test_another_staff_idp_without_mfa_is_not_step_up_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier(monkeypatch, _claims(amr=["pwd"], iss="https://idp.sixth.example"))
    verifier.issuer = "https://idp.sixth.example"
    identity = verifier.verify("header.payload.signature")
    assert identity.role == "operator"
    assert identity.step_up_at is None


def test_a_named_personal_google_account_signs_in_with_its_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _claims(email="Named.Person@gmail.com", hd=None)
    identity = _verifier(monkeypatch, claims).verify("header.payload.signature")
    assert identity.role == "support"


@pytest.mark.parametrize(
    "updates",
    [
        {"email": "someone.else@gmail.com", "hd": None},  # personal, not named
        {"email": "named.person@gmail.com", "hd": None, "email_verified": False},
        {"email": "named.person@gmail.com", "hd": "attacker.example"},  # another Workspace
    ],
)
def test_personal_accounts_need_the_name_a_verified_email_and_no_other_workspace(
    monkeypatch: pytest.MonkeyPatch, updates: dict[str, object]
) -> None:
    with pytest.raises(PermissionError):
        _verifier(monkeypatch, _claims(**updates)).verify("header.payload.signature")


def test_an_idp_asserted_staff_role_is_ignored_unless_the_deployment_opts_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``staff_role`` claim alone never grants the console: the role comes from the allowlists
    (security review 2026-10-22). A deployment whose IdP really asserts roles opts in."""
    outsider = _claims(email="anyone@sixth.example", staff_role="operator")
    with pytest.raises(PermissionError):
        _verifier(monkeypatch, outsider).verify("header.payload.signature")
    trusting = _verifier(monkeypatch, outsider)
    trusting.idp_asserts_role = True
    assert trusting.verify("header.payload.signature").role == "operator"
