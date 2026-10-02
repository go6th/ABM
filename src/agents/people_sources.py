"""Provider-neutral people-source routing; adapters are injected and never networked here."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from src.db.data_layer import DataLayerError

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "people_sources.yaml"
PeopleRecord = Mapping[str, Any]


class PeopleSourceAdapter(Protocol):
    async def find_people(self, query: str) -> Sequence[PeopleRecord]: ...

    async def find_organizations(self, query: str) -> Sequence[Mapping[str, Any]]: ...

    async def find_tech_stack(self, query: str) -> Sequence[Mapping[str, Any]]: ...

    async def find_teams(self, query: str) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class PeopleSourceRouter:
    people_adapters: tuple[PeopleSourceAdapter, ...]
    sumble_adapter: PeopleSourceAdapter

    async def find_people(self, query: str) -> list[PeopleRecord]:
        records: list[PeopleRecord] = []
        for adapter in self.people_adapters:
            records.extend(await adapter.find_people(query))
        return records

    async def find_organizations(self, query: str) -> Sequence[Mapping[str, Any]]:
        return await self.sumble_adapter.find_organizations(query)

    async def find_tech_stack(self, query: str) -> Sequence[Mapping[str, Any]]:
        return await self.sumble_adapter.find_tech_stack(query)

    async def find_teams(self, query: str) -> Sequence[Mapping[str, Any]]:
        return await self.sumble_adapter.find_teams(query)


def build_people_source_router(
    bitscale: PeopleSourceAdapter,
    sumble: PeopleSourceAdapter,
    *,
    config_path: Path = CONFIG_PATH,
) -> PeopleSourceRouter:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DataLayerError(f"cannot load people-source routing config: {exc}") from exc
    if not isinstance(config, dict) or config.get("organization_technology_teams") != "sumble":
        raise DataLayerError(
            "Sumble must remain configured for organizations, technology and teams"
        )
    mode = config.get("mode")
    choices = config.get("people")
    if mode not in {"bitscale_only", "bitscale_plus_sumble"} or not isinstance(choices, dict):
        raise DataLayerError("people-source mode must select a supported configuration")
    selected = choices.get(mode)
    adapters: tuple[PeopleSourceAdapter, ...]
    if selected == ["bitscale"]:
        adapters = (bitscale,)
    elif selected == ["bitscale", "sumble"]:
        adapters = (bitscale, sumble)
    else:
        raise DataLayerError(f"invalid adapter list for people-source mode {mode}")
    return PeopleSourceRouter(adapters, sumble)
