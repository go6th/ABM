"""Load dated analyst-reviewed golden accounts without mixing synthetic test fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.db.data_layer import DataLayerError


@dataclass(frozen=True)
class GoldenAccount:
    account_id: str
    account_name: str
    domain: str
    capture_date: date
    facts: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class GoldenSet:
    dataset_type: str
    accounts: tuple[GoldenAccount, ...]


def load_golden_accounts(path: Path, *, allow_synthetic: bool = False) -> GoldenSet:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataLayerError(f"cannot load golden account set {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise DataLayerError("golden account set must be a JSON object")
    dataset_type = raw.get("dataset_type")
    if dataset_type == "synthetic_fixture":
        if not allow_synthetic:
            raise DataLayerError("synthetic golden fixtures require allow_synthetic=True")
    elif dataset_type != "analyst_reviewed_real":
        raise DataLayerError("golden set must be analyst_reviewed_real or synthetic_fixture")
    accounts = raw.get("accounts")
    if not isinstance(accounts, list) or not 3 <= len(accounts) <= 5:
        raise DataLayerError("golden set must contain three to five accounts")
    parsed: list[GoldenAccount] = []
    seen_ids: set[str] = set()
    for item in accounts:
        if not isinstance(item, dict):
            raise DataLayerError("golden account entries must be objects")
        account_id, name, domain = (
            item.get("account_id"),
            item.get("account_name"),
            item.get("domain"),
        )
        if (
            not isinstance(account_id, str)
            or not account_id.strip()
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(domain, str)
            or not domain.strip()
        ):
            raise DataLayerError("each golden account requires an ID, name and domain")
        if account_id in seen_ids:
            raise DataLayerError(f"duplicate golden account ID: {account_id}")
        seen_ids.add(account_id)
        if dataset_type == "synthetic_fixture" and not (
            account_id.startswith("synthetic-") and name.lower().startswith("synthetic ")
        ):
            raise DataLayerError("synthetic fixture accounts must be explicitly named synthetic")
        try:
            capture_date = date.fromisoformat(item["capture_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataLayerError(
                f"golden account {account_id} requires an ISO capture_date"
            ) from exc
        review = item.get("analyst_review")
        if not isinstance(review, dict):
            raise DataLayerError(f"golden account {account_id} requires analyst_review metadata")
        if dataset_type == "analyst_reviewed_real" and (
            review.get("reviewed") is not True
            or not isinstance(review.get("reviewer"), str)
            or not review["reviewer"].strip()
            or not isinstance(review.get("reviewed_at"), str)
        ):
            raise DataLayerError(f"real golden account {account_id} requires dated analyst review")
        if dataset_type == "analyst_reviewed_real":
            try:
                date.fromisoformat(review["reviewed_at"][:10])
            except (TypeError, ValueError) as exc:
                raise DataLayerError(
                    f"real golden account {account_id} requires an ISO review date"
                ) from exc
        facts = item.get("known_correct_facts")
        if not isinstance(facts, list) or not facts:
            raise DataLayerError(f"golden account {account_id} requires known-correct facts")
        for fact in facts:
            if not isinstance(fact, dict) or not all(
                isinstance(fact.get(key), str) and fact[key].strip()
                for key in ("attribute_key", "source_url", "source_code", "quote")
            ):
                raise DataLayerError(
                    f"golden account {account_id} facts require grade and source evidence"
                )
            if not isinstance(fact.get("grade"), str) or fact["grade"] not in {
                "confirmed",
                "probable",
                "hypothesis",
                "lead",
            }:
                raise DataLayerError(
                    f"golden account {account_id} fact has an invalid product grade"
                )
            if "value" not in fact:
                raise DataLayerError(f"golden account {account_id} fact requires its known value")
            try:
                date.fromisoformat(fact["captured_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DataLayerError(
                    f"golden account {account_id} fact requires captured_at"
                ) from exc
        parsed.append(GoldenAccount(account_id, name, domain, capture_date, tuple(facts)))
    return GoldenSet(dataset_type, tuple(parsed))
