"""The Pydantic models and the JSON Schema accept and reject the same documents. ``jsonschema``
is a project dependency, so the schema side runs with Draft 2020-12 (formats are not checked
on either side: ``date`` and ``uri`` are annotations in the schema and plain strings in the
models)."""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
from typing import Any

import jsonschema
import pydantic
import pytest

from parity.bundle import dump_bundle, load_bundle, load_schema, validate_bundle
from tests.parity.conftest import REFERENCE_WS, reference


def schema_accepts(doc: dict[str, Any]) -> bool:
    """Whether the JSON Schema accepts ``doc``."""
    return jsonschema.Draft202012Validator(load_schema()).is_valid(doc)


def models_accept(doc: dict[str, Any]) -> bool:
    """Whether the models accept ``doc``."""
    try:
        validate_bundle(doc)
    except pydantic.ValidationError:
        return False
    return True


def set_path(doc: dict[str, Any], path: list[Any], value: Any) -> dict[str, Any]:
    """A deep copy of ``doc`` with ``value`` at ``path`` (``...`` deletes the key)."""
    out = copy.deepcopy(doc)
    node: Any = out
    for key in path[:-1]:
        node = node[key]
    if value is ...:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


MUTATIONS: list[tuple[str, list[Any], Any]] = [
    ("extra top level key", ["surprise"], 1),
    ("extra meta key", ["meta", "surprise"], "x"),
    ("missing required", ["prose", "glossary"], ...),
    ("bad schema version", ["schema_version"], "2.0.0"),
    ("prefix with a slash", ["meta", "account_short"], "A/B"),
    ("empty lanes", ["meta", "lanes"], []),
    ("null where the schema has no null", ["meta", "people_lanes"], None),
    ("negative count", ["meta", "live_capture_count"], -1),
    (
        "source id pattern",
        ["sources", "X1"],
        {
            "name": "n",
            "url": "https://example.com",
            "type": "t",
            "date": "d",
            "how": "h",
            "supports": "s",
        },
    ),
    ("class enum", ["jobs_rows", 0, "cls"], "Owner"),
    ("status enum", ["jobs_rows", 0, "status"], "Filled"),
    ("level out of range", ["people_rows", 0, "level"], 10),
    ("priority enum", ["people_rows", 0, "priority"], "D"),
    ("news row of four", ["prose", "news", 0], ["a", "b", "c", "d"]),
    ("hypothesis row of three", ["prose", "hypotheses", 0], ["a", "b", "c"]),
    ("in_stack enum", ["ranking", "rows", 0, "in_stack"], "Maybe"),
    ("prio extra key", ["computed", "prio", "D"], 1),
    ("string for an integer", ["computed", "senior"], "5"),
    ("integer for a string", ["meta", "byline"], 5),
    ("boolean in a detection row", ["prose", "evidence", "detections", 0, 3], True),
    ("platform sentences too long", ["jobs_rows", 0, "platform_sentences"], "x" * 3001),
    ("entity without label", ["meta", "entities", 0], {"counted_as_account": True}),
    ("workbook notes extra key", ["prose", "workbook_notes", "extra"], "x"),
    ("internal only not an object", ["prose", "internal_only"], "text"),
]


def test_synthetic_bundle_is_accepted_by_both(synthetic_doc: dict[str, Any]) -> None:
    assert schema_accepts(synthetic_doc)
    assert models_accept(synthetic_doc)


@pytest.mark.parametrize(("name", "path", "value"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_both_reject_the_same_mutations(
    synthetic_doc: dict[str, Any], name: str, path: list[Any], value: Any
) -> None:
    doc = set_path(synthetic_doc, path, value)
    assert not schema_accepts(doc), name
    assert not models_accept(doc), name


def test_dump_round_trips(synthetic_doc: dict[str, Any]) -> None:
    assert dump_bundle(validate_bundle(synthetic_doc)) == synthetic_doc


def test_load_bundle_reads_plain_and_gzip(synthetic_doc: dict[str, Any], tmp_path: Path) -> None:
    plain = tmp_path / "b.json"
    plain.write_text(json.dumps(synthetic_doc), encoding="utf-8")
    packed = tmp_path / "b.json.gz"
    with gzip.open(packed, "wt", encoding="utf-8") as fh:
        json.dump(synthetic_doc, fh)
    assert load_bundle(plain).meta.account_short == "Acme"
    assert load_bundle(packed).meta.account_short == "Acme"


@reference
def test_reference_bundle_is_accepted_by_both() -> None:
    path = REFERENCE_WS / "bundle.json"
    if not path.exists():
        pytest.skip("the reference bundle has not been built")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert schema_accepts(doc)
    assert models_accept(doc)
