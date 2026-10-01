"""Unit tests for the tool-call ledger hashing (pure functions)."""

from __future__ import annotations

from abm_generator.ledger import args_hash


def test_args_hash_is_stable_regardless_of_key_order() -> None:
    a = args_hash("broker:news", {"domain": "example.com", "branch": "news"})
    b = args_hash("broker:news", {"branch": "news", "domain": "example.com"})
    assert a == b


def test_args_hash_differs_by_tool_or_args() -> None:
    base = args_hash("broker:news", {"domain": "example.com"})
    assert args_hash("broker:jobs", {"domain": "example.com"}) != base
    assert args_hash("broker:news", {"domain": "other.com"}) != base


def test_args_hash_handles_nested_and_non_json_types() -> None:
    h1 = args_hash("tool", {"a": {"b": [1, 2]}, "c": None})
    h2 = args_hash("tool", {"c": None, "a": {"b": [1, 2]}})
    assert h1 == h2
    assert len(args_hash("tool", {"obj": object()})) == 64
