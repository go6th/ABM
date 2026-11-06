"""Which process serves which routes (fix prompt B, task 1; ADR-0109).

The api and the worker Cloud Run services run the same FastAPI application. Before this module
the worker also served every ``/v1`` and ``/operator`` route and the api every ``/tasks`` route;
only Cloud Run ingress and IAM kept them apart, settings the code could neither see nor test.
Now ``SERVICE_ROLE`` decides which routers the process registers:

==========  ===========================================
role        planes it serves
==========  ===========================================
``api``     probes, ``api`` (``/v1/*``, ``/operator/*``)
``worker``  probes, ``worker`` (``/tasks/*``)
``job``     probes
``migrate`` probes
``all``     probes, ``api``, ``worker`` (dev and test)
==========  ===========================================

A route the role does not serve is never registered, so it answers 404 like any unknown path:
the route catalogue's default deny is untouched and nothing reveals what another process serves.
``all`` is the default when ``SERVICE_ROLE`` is unset, and it is refused in staging and
production: a cloud process must say which plane it is (Terraform sets it on every service).

GUARANTEE(test_worker_role_does_not_serve_v1)
GUARANTEE(test_api_role_does_not_serve_tasks)
GUARANTEE(test_dev_role_serves_everything)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

Plane = Literal["api", "worker", "both"]
Role = Literal["api", "worker", "job", "migrate", "all"]

ROLE_PLANES: dict[str, frozenset[str]] = {
    "api": frozenset({"api"}),
    "worker": frozenset({"worker"}),
    "job": frozenset(),
    "migrate": frozenset(),
    "all": frozenset({"api", "worker"}),
}
# a path's plane by its first segment; the probes are served by every role
PREFIX_PLANES: tuple[tuple[str, Plane], ...] = (
    ("/v1/", "api"),
    ("/operator/", "api"),
    ("/tasks/", "worker"),
)
PROBES: frozenset[str] = frozenset({"/healthz", "/readyz"})
CLOUD_PROFILES = frozenset({"staging", "production"})


class UnknownServiceRole(RuntimeError):
    """``SERVICE_ROLE`` names no role, or a cloud process left it unset (``all``)."""


def plane_of_path(path: str) -> Plane | None:
    """The plane a route path belongs to (None: no plane, which the catalogue check refuses)."""
    if path in PROBES:
        return "both"
    for prefix, plane in PREFIX_PLANES:
        if path.startswith(prefix):
            return plane
    return None


def serves(role: str, plane: Plane) -> bool:
    """Whether a process in ``role`` registers routes of ``plane``."""
    return plane == "both" or plane in ROLE_PLANES[role]


def service_role(env: Mapping[str, str] | None = None) -> str:
    """The role of this process: ``SERVICE_ROLE`` (default ``all``), refused when unknown and,
    in staging and production, when it is ``all``."""
    e = os.environ if env is None else env
    role = (e.get("SERVICE_ROLE") or "all").strip().lower()
    if role not in ROLE_PLANES:
        raise UnknownServiceRole(f"SERVICE_ROLE={role!r} is not one of {sorted(ROLE_PLANES)}")
    if role == "all" and e.get("APP_ENV", "dev") in CLOUD_PROFILES:
        raise UnknownServiceRole(
            "SERVICE_ROLE must be api, worker, job or migrate in staging and production"
        )
    return role
