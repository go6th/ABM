"""Local password policy and Argon2id credential primitives."""

import pytest

from identity.local_password import (
    PasswordRejected,
    hash_password,
    load_policy,
    validate_password,
    verify_password,
)


def test_password_policy_requires_long_noncommon_context_free_passwords() -> None:
    """Reject weak or identifying values while accepting a strong passphrase."""
    with pytest.raises(PasswordRejected):
        validate_password("short")
    with pytest.raises(PasswordRejected):
        validate_password("Password123!")
    with pytest.raises(PasswordRejected):
        validate_password("Alex-Blue-Sky-2026!", email="alex@example.com")
    with pytest.raises(PasswordRejected):
        validate_password("Morgan-river-stone-2026!", display_name="Morgan Lee")
    validate_password("violet orbit cedar lantern 84!")


@pytest.mark.asyncio
async def test_passwords_are_argon2id_and_unknown_accounts_verify_a_dummy_hash() -> None:
    """Hashing returns only an Argon2id PHC digest and all verification cases fail closed."""
    policy = load_policy()
    validate_password("violet orbit cedar lantern 84!")
    stored = await hash_password("violet orbit cedar lantern 84!", policy=policy)

    assert stored.startswith("$argon2id$")
    assert "violet orbit cedar lantern" not in stored
    assert await verify_password(stored, "violet orbit cedar lantern 84!", policy=policy)
    assert not await verify_password(stored, "violet orbit cedar lantern 84?", policy=policy)
    assert not await verify_password(None, "violet orbit cedar lantern 84!", policy=policy)
    assert not await verify_password("not-a-valid-phc-hash", "candidate", policy=policy)
    with pytest.raises(PasswordRejected):
        await hash_password("short", policy=policy)
