"""Local first-party password primitives (AUTH1).

Password verification always performs Argon2id work, including for unknown accounts. Hashing and
verification are moved to a worker thread so a deliberately expensive password cannot block the
async API loop. The caller owns account lookup, rate limits, lockout updates, and authorization;
this module never looks up a user or issues a session.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from anyio import to_thread
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from account_intelligence.config import CONFIG_DIR


class PasswordRejected(ValueError):
    """A fixed local policy refusal (safe to map to a password form field)."""


@dataclass(frozen=True)
class PasswordPolicy:
    """Validated password rules from ``config/auth.yaml``."""

    min_length: int
    max_length: int
    common_passwords: frozenset[str]
    hasher: PasswordHasher


def _terms(value: str | None) -> tuple[str, ...]:
    """Context terms usable for password screening, excluding short and generic fragments."""
    if not value:
        return ()
    normalized = "".join(char for char in value.casefold() if char.isalnum())
    candidates = {normalized}
    candidates.update(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))
    return tuple(sorted((term for term in candidates if len(term) >= 4), key=len, reverse=True))


@lru_cache(maxsize=1)
def load_policy() -> PasswordPolicy:
    """Load and validate the versioned local credential policy."""
    path = Path(CONFIG_DIR) / "auth.yaml"
    raw: Any = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or raw.get("version") != "1.0.0":
        raise ValueError("config/auth.yaml has an unsupported policy version")
    password = raw.get("password")
    if not isinstance(password, dict):
        raise ValueError("config/auth.yaml has no password policy")
    minimum, maximum = password.get("min_length"), password.get("max_length")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or minimum < 12
        or maximum < minimum
        or maximum > 1024
    ):
        raise ValueError("password length bounds must be valid and require at least 12 characters")
    common = password.get("common_passwords")
    argon = password.get("argon2id")
    if not isinstance(common, list) or not common or not isinstance(argon, dict):
        raise ValueError("password policy needs a local common-password list and Argon2id settings")

    def positive_int(key: str) -> int:
        """Read one positive integer from the untrusted YAML mapping."""
        value = argon.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("Argon2id parameters must be positive integers")
        return value

    numbers = {
        key: positive_int(key)
        for key in ("time_cost", "memory_cost_kib", "parallelism", "hash_len", "salt_len")
    }
    if numbers["memory_cost_kib"] < 65536 or numbers["time_cost"] < 2:
        raise ValueError("Argon2id parameters are below the minimum local-auth baseline")
    common_values = frozenset(
        item.strip().casefold() for item in common if isinstance(item, str) and item.strip()
    )
    if len(common_values) != len(common):
        raise ValueError("common-password entries must be unique non-empty strings")
    return PasswordPolicy(
        min_length=minimum,
        max_length=maximum,
        common_passwords=common_values,
        hasher=PasswordHasher(
            time_cost=numbers["time_cost"],
            memory_cost=numbers["memory_cost_kib"],
            parallelism=numbers["parallelism"],
            hash_len=numbers["hash_len"],
            salt_len=numbers["salt_len"],
            type=Type.ID,
        ),
    )


def validate_password(
    password: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
    policy: PasswordPolicy | None = None,
) -> None:
    """Refuse short, overlong, common, or user-context-derived passwords locally."""
    rules = policy or load_policy()
    if not isinstance(password, str) or not rules.min_length <= len(password) <= rules.max_length:
        raise PasswordRejected("Use a password between 12 and 128 characters.")
    if "\x00" in password:
        raise PasswordRejected("Password contains an unsupported character.")
    folded = password.casefold()
    compact = "".join(char for char in folded if char.isalnum())
    if folded in rules.common_passwords or compact in rules.common_passwords:
        raise PasswordRejected("Choose a less common password.")
    context = (*_terms(email), *_terms(display_name))
    if any(term in compact for term in context):
        raise PasswordRejected("Do not include your email address or name in your password.")


def _hash(password: str, policy: PasswordPolicy) -> str:
    return policy.hasher.hash(password)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A random-salted dummy digest used for unknown accounts and malformed stored hashes."""
    return load_policy().hasher.hash("not-a-real-account-password-4b7d9e")


def _verify(stored_hash: str | None, candidate: str, policy: PasswordPolicy) -> bool:
    target = stored_hash or _dummy_hash()
    try:
        verified = policy.hasher.verify(target, candidate)
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError, ValueError):
        if stored_hash:
            with suppress(
                VerifyMismatchError, VerificationError, InvalidHashError, TypeError, ValueError
            ):
                policy.hasher.verify(_dummy_hash(), candidate)
        return False
    return bool(stored_hash) and verified


async def hash_password(
    password: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
    policy: PasswordPolicy | None = None,
) -> str:
    """Validate and hash a password using Argon2id off the event loop."""
    rules = policy or load_policy()
    validate_password(password, email=email, display_name=display_name, policy=rules)
    return await to_thread.run_sync(_hash, password, rules)


async def verify_password(
    stored_hash: str | None, candidate: str, *, policy: PasswordPolicy | None = None
) -> bool:
    """Verify a credential without revealing whether the account or stored hash exists."""
    rules = policy or load_policy()
    # Bound attacker-controlled input before passing it into the hashing backend. The bounded
    # replacement still runs against a real Argon2 digest, preserving the expensive work path.
    bounded = candidate[: rules.max_length + 1]
    return await to_thread.run_sync(_verify, stored_hash, bounded, rules)


def password_context_terms(values: Iterable[str | None]) -> tuple[str, ...]:
    """Expose the normalized terms for a registration form's matching policy tests."""
    return tuple(term for value in values for term in _terms(value))
