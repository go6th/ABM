"""p_people: people by title and profile, post and comment enrichment.

Transport is sourced (docs/providers/p_people.md: grid run and run status, auth,
rate limit, error codes). The *operations* are not: the provider runs a grid that
the workspace owner builds, and the people fields come back as grid columns keyed by
column UUIDs that only that workspace defines. No public schema exists for them, so
both operations raise ``NotSupportedYet`` until the grid contract is supplied (see
the spec file, "What to supply").
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from account_intelligence.connectors.base import (
    Operation,
    PageRequest,
    ParsedPage,
    ProviderCode,
    RawResponse,
)
from account_intelligence.connectors.errors import NotSupportedYet
from account_intelligence.connectors.models import OperationArgs
from account_intelligence.connectors.providers._common import classify_status

CODE = ProviderCode.P_PEOPLE

MISSING = (
    "p_people operations need the workspace grid contract: grid id per operation, the "
    "output of GET /grids/:gridId/curl (input column keys and output column UUIDs with "
    "names and types), and two or three real run responses per operation in "
    "docs/providers/samples/p_people/"
)


# VERIFY(p_people): the grid output contract (column UUIDs, names, types) and cost per run.
class PeopleConnector:
    """The p_people adapter: every operation is NotSupportedYet until the grid contract arrives."""

    code = CODE

    def supports(self, op: Operation) -> bool:
        """Always False until the contract is sourced."""
        return False

    def build_request(self, op: Operation, args: OperationArgs, cursor: str | None) -> PageRequest:
        """Raise NotSupportedYet with what must be supplied."""
        raise NotSupportedYet(MISSING, provider_code=CODE, operation=op)

    def poll_request(self, op: Operation, job_id: str) -> PageRequest:
        """Raise NotSupportedYet with what must be supplied."""
        raise NotSupportedYet(MISSING, provider_code=CODE, operation=op)

    def classify_error(self, op: Operation, raw: RawResponse) -> Exception:
        """Map statuses to typed errors (auth on 401 and 403)."""
        # api-reference.md section 7: 401 INVALID_API_KEY, 403 FEATURE_DISABLED or
        # GRID_NOT_FOUND, 404, 429 RATE_LIMIT_EXCEEDED (Retry-After), 500 RUN_FAILED.
        return classify_status(raw, CODE, op, auth=frozenset({401, 403}))

    def parse(
        self,
        op: Operation,
        args: OperationArgs,
        raw: RawResponse,
        base: Mapping[str, Any],
        cursor: str | None,
    ) -> ParsedPage:
        """Raise NotSupportedYet with what must be supplied."""
        raise NotSupportedYet(MISSING, provider_code=CODE, operation=op)
