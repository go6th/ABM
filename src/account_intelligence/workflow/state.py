"""Small checkpoint state. Provider payloads and document text stay in their stores."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict, cast


def merge_maps(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Deterministic reducer for independent stage result maps."""
    merged = dict(left)
    for key, value in right.items():
        if key in merged and merged[key] != value:
            raise ValueError(f"parallel workflow writes conflict for {key}")
        merged[key] = value
    return merged


def append_unique(left: list[str], right: list[str]) -> list[str]:
    """Stable append reducer suitable for event and reference ids."""
    return list(dict.fromkeys([*left, *right]))


def sum_counters(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    """Add independent branch counter deltas without losing either write."""
    merged = dict(left)
    for key, value in right.items():
        merged[key] = merged.get(key, 0) + value
    return merged


class RunState(TypedDict, total=False):
    schema_version: int
    tenant_id: str
    run_id: str
    graph_version: str
    status: str
    next_stage: str
    completed_stages: Annotated[list[str], append_unique]
    references: Annotated[dict[str, Any], merge_maps]
    coverage_updates: Annotated[dict[str, Any], merge_maps]
    counters: Annotated[dict[str, int], sum_counters]
    loop_exits: Annotated[dict[str, str], merge_maps]
    spent_usd: float
    approved: bool


CURRENT_SCHEMA_VERSION = 1
MAX_SERIALIZED_STATE_BYTES = 64 * 1024


def migrate(state: dict[str, Any]) -> RunState:
    """Upgrade the original starter checkpoint shape without copying document content."""
    version = int(state.get("schema_version", 0))
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"checkpoint schema {version} is newer than supported")
    migrated: dict[str, Any] = {
        key: value for key, value in state.items() if key in RunState.__annotations__
    }
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    migrated.setdefault("completed_stages", [])
    migrated.setdefault("references", {})
    migrated.setdefault("coverage_updates", {})
    migrated.setdefault("counters", {})
    migrated.setdefault("loop_exits", {})
    if "facts" in state:
        raise ValueError(
            "legacy checkpoint contains fact payloads; migrate payloads to the payload store first"
        )
    return cast(RunState, migrated)


def validate_small_state(state: dict[str, Any]) -> None:
    import json

    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_SERIALIZED_STATE_BYTES:
        raise ValueError("workflow checkpoint exceeds the 64 KiB state limit")
    forbidden = {"page_text", "raw_text", "prompt", "document_text", "payload"}

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            if forbidden.intersection(value):
                raise ValueError("checkpoint may contain references, not text or payloads")
            for nested in value.values():
                inspect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                inspect(nested)
        elif isinstance(value, str) and len(value) > 4096:
            raise ValueError("checkpoint string exceeds the 4 KiB reference limit")

    inspect(state)
