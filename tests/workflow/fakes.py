"""Deterministic fakes for workflow-level contract tests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


class FakeLlm:
    def __init__(self, scripted: dict[tuple[str, str], Any] | None = None) -> None:
        self.scripted = scripted or {}
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def input_hash(input_data: dict[str, Any]) -> str:
        payload = json.dumps(input_data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    async def generate(
        self,
        tier: str,
        prompt_id: str,
        input_data: dict[str, Any],
        output_schema: type[Any],
    ) -> BaseModel:
        del tier  # routing choice is supplied by config and not part of fixture selection
        input_hash = self.input_hash(input_data)
        key = (prompt_id, input_hash)
        self.calls.append(key)
        if key not in self.scripted:
            raise AssertionError(f"unrecorded FakeLlm request: {key}")
        if not issubclass(output_schema, BaseModel):
            raise TypeError("FakeLlm requires a Pydantic output schema")
        return output_schema.model_validate(self.scripted[key])


class FakePayloadStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.reads = 0
        self.writes = 0

    async def get(self, tenant_id: str, run_id: str, reference: str) -> dict[str, Any] | None:
        self.reads += 1
        return self.values.get((tenant_id, run_id, reference))

    async def put(self, tenant_id: str, run_id: str, reference: str, value: dict[str, Any]) -> None:
        self.writes += 1
        self.values[(tenant_id, run_id, reference)] = value
