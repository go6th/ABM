"""Recorded-response test double (roadmap s7 deliverable, s11.2 connector requirements).

Recordings live at ``fixtures/recorded/<broker>/<cache_key>.json``. A request with no recording
fails loudly; there is no fallback to a network call, so tests can never spend credits.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.brokers.base import BrokerError, BrokerRequest, BrokerResponse, JSONValue

DEFAULT_RECORDINGS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "recorded"


class RecordingNotFound(BrokerError):
    pass


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request: BrokerRequest
    response: dict[str, JSONValue]
    cost_credits: float = Field(ge=0)
    synthetic: bool
    note: str = ""


class RecordedConnector:
    def __init__(self, broker: str, root: Path | str = DEFAULT_RECORDINGS_DIR) -> None:
        self.broker = broker
        self._dir = Path(root) / broker
        self.served: list[str] = []

    def path_for(self, request: BrokerRequest) -> Path:
        return self._dir / f"{request.cache_key()}.json"

    def call(self, request: BrokerRequest) -> BrokerResponse:
        if request.broker != self.broker:
            raise BrokerError(f"{self.broker} connector got a {request.broker} request")
        path = self.path_for(request)
        if not path.exists():
            raise RecordingNotFound(
                f"no recording for {request.broker}.{request.operation} {request.params}"
            )
        rec = Recording.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if rec.request != request:
            raise BrokerError(f"recording {path.name} does not match its request")
        self.served.append(request.cache_key())
        return BrokerResponse(
            request_key=request.cache_key(), payload=rec.response, cost_credits=rec.cost_credits
        )


def write_recording(
    request: BrokerRequest,
    response: dict[str, JSONValue],
    cost_credits: float,
    *,
    synthetic: bool,
    note: str = "",
    root: Path | str = DEFAULT_RECORDINGS_DIR,
) -> Path:
    """Store a recording. Used to create fixtures; live capture arrives with Phase 1 adapters."""
    rec = Recording(
        request=request,
        response=response,
        cost_credits=cost_credits,
        synthetic=synthetic,
        note=note,
    )
    path = Path(root) / request.broker / f"{request.cache_key()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
