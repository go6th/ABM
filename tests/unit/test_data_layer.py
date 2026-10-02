from __future__ import annotations

import asyncio
import re
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from src.db.data_layer import (
    DataLayerError,
    fingerprint_value,
    load_checklist,
    normalize_source_url,
    validate_coverage_proof,
)
from src.storage.raw_payloads import LocalRawPayloadStore, load_raw_payload_store

ROOT = Path(__file__).resolve().parents[2]
DENYLIST = (
    ROOT
    / ".claude"
    / "skills"
    / "account-intelligence-platform"
    / "assets"
    / "client_output_denylist.txt"
)


def test_checklist_loader_uses_versioned_config_with_33_rows() -> None:
    version, rows = load_checklist()
    assert version == "1.1.0"
    assert len(rows) == 33
    assert [row.row_id for row in rows[:5]] == [f"P1.{i:02d}" for i in range(1, 6)]
    assert [row.row_id for row in rows[5:]] == [f"P2.{i:02d}" for i in range(1, 29)]


def test_source_url_normalization_preserves_semantic_query() -> None:
    assert normalize_source_url("HTTPS://Example.COM:443/news/?b=2&a=1#section") == (
        "https://example.com/news?a=1&b=2"
    )
    assert normalize_source_url("http://example.com:80") == "http://example.com/"


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "//example.com/path", "https://u:p@example.com"]
)
def test_source_url_normalization_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(DataLayerError):
        normalize_source_url(url)


def test_fingerprint_is_stable_for_object_key_order_and_whitespace() -> None:
    assert fingerprint_value(" Company  Name ", {"b": " two ", "a": 1}) == fingerprint_value(
        "company name", {"a": 1, "b": "two"}
    )
    assert fingerprint_value("company name", {"a": 1}) != fingerprint_value(
        "company name", {"a": 2}
    )


def test_coverage_requires_proof_or_a_blocked_reason() -> None:
    validate_coverage_proof("Blocked", [], [], None, "source access timed out")
    with pytest.raises(DataLayerError, match="Blocked coverage requires a reason"):
        validate_coverage_proof("Blocked", [], [], None)
    with pytest.raises(DataLayerError, match="proof requires"):
        validate_coverage_proof("Done", [], ["web_search"], 0)
    validate_coverage_proof("Done", ["company news"], ["web_search"], 0)


def _denylist_patterns() -> list[re.Pattern[str]]:
    return [
        re.compile(line, re.IGNORECASE)
        for line in DENYLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_raw_payload_store_is_gzipped_content_addressed_and_neutral(tmp_path: Path) -> None:
    store = LocalRawPayloadStore(tmp_path)
    tenant_id, account_id, run_id = uuid4(), uuid4(), uuid4()
    payload = {"results": [{"title": "synthetic result"}]}
    key, content_hash = asyncio.run(store.put_json(tenant_id, account_id, run_id, payload))

    assert key == f"{tenant_id}/{account_id}/{run_id}/{content_hash}.json.gz"
    assert asyncio.run(store.get_json(key)) == payload
    assert asyncio.run(store.put_json(tenant_id, account_id, run_id, payload)) == (
        key,
        content_hash,
    )
    assert not any(pattern.search(key) for pattern in _denylist_patterns())
    assert (tmp_path / key).read_bytes().startswith(b"\x1f\x8b")


def test_raw_payload_store_rejects_path_traversal(tmp_path: Path) -> None:
    store = LocalRawPayloadStore(tmp_path)
    with pytest.raises(DataLayerError, match="escapes"):
        asyncio.run(store.get_json("../../outside.json.gz"))


def test_storage_config_selects_local_backend() -> None:
    store = load_raw_payload_store()
    assert isinstance(store, LocalRawPayloadStore)


def test_checklist_rows_are_semantically_aligned_with_skill_appendix_asset() -> None:
    app = yaml.safe_load((ROOT / "config" / "checklist.yaml").read_text(encoding="utf-8"))
    skill = yaml.safe_load(
        (ROOT / ".claude/skills/account-intelligence-platform/assets/checklist.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert len(app["rows"]) == len(skill["rows"]) == 33
    for app_row, skill_row in zip(app["rows"], skill["rows"], strict=True):
        assert app_row["pass"] == skill_row["pass"]
        assert app_row["row"] == skill_row["label"]
        assert app_row["priority"].lower() == skill_row["priority"]
