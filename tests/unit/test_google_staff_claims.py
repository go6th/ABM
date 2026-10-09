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
        support_emails=frozenset({"support@sixth.example"}),
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


def test_google_claim_without_mfa_is_not_step_up_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = _verifier(monkeypatch, _claims(amr=["pwd"])).verify("header.payload.signature")
    assert identity.role == "operator"
    assert identity.step_up_at is None
