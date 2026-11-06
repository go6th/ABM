"""ADR-0123: each SERVICE_ROLE runs as exactly one database role. The mapping and the startup
verdict are pure: the verdict fails closed when a service holds another service's role (the api
must never carry the worker's rights) and only warns while the IAM user still sits in app_rw
(before migration 0101 and the migrate job moved it)."""

from __future__ import annotations

import pytest

from account_intelligence.platform.gcp.adapters import DB_ROLES, db_role_for, role_verdict


def test_every_service_role_maps_to_its_database_role() -> None:
    assert DB_ROLES == {
        "api": "app_api",
        "worker": "app_worker",
        "job": "app_worker",
        "migrate": None,
        "all": "app_rw",
    }
    with pytest.raises(ValueError):
        db_role_for("sweeper")


@pytest.mark.parametrize(
    ("service", "held", "verdict"),
    [
        ("api", {"app_api"}, "ok"),
        ("worker", {"app_worker"}, "ok"),
        ("job", {"app_worker"}, "ok"),
        ("api", {"app_rw"}, "pending"),  # the migrate job has not moved the IAM user yet
        ("api", {"app_api", "app_rw"}, "pending"),  # moved, app_rw not revoked
        ("migrate", set(), "ok"),  # the migrator signs in as the owner, no runtime role
    ],
)
def test_the_verdict_accepts_the_own_role_and_warns_on_app_rw(
    service: str, held: set[str], verdict: str
) -> None:
    assert role_verdict(service, frozenset(held)) == verdict


@pytest.mark.parametrize(
    ("service", "held"),
    [
        ("api", {"app_worker"}),  # another service's rights
        ("api", {"app_api", "app_worker"}),
        ("worker", {"app_api"}),
        ("api", {"app_sweeper"}),
        ("api", set()),  # no runtime role at all
    ],
)
def test_the_verdict_fails_closed_on_a_foreign_or_missing_role(
    service: str, held: set[str]
) -> None:
    with pytest.raises(RuntimeError, match="database role"):
        role_verdict(service, frozenset(held))
