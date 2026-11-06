"""One price table (ADR-0115): every model id used in ``src/`` and ``config/`` has exactly one
price entry, in ``config/models.yaml``; research calls, Ask turns and chat price from it, and the
merge kept every price as it was."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from account_intelligence.chat.registry import load_chat_registry
from account_intelligence.chat.service import ChatUsage, actual_cost
from llm.cost import prices
from llm.reply_cache import ReplyUsage, price

ROOT = Path(__file__).resolve().parents[2]
OLD = yaml.safe_load((ROOT / "tests/llm/fixtures/prices_2026-11-03.yaml").read_text())
# a model id as the configuration and code write it; an Ollama tag (":cloud") is the provider's
# name for the same model
MODEL_ID = re.compile(r"\b(claude-[a-z]+-\d+-\d+|kimi-k\d+|grok-\d+(?:\.\d+)?)\b")
PRICE_KEY = re.compile(r"usd_per_(million|mtok)")


def _priced(node: Any, where: str) -> list[tuple[str, str]]:
    """``(model id, where)`` for every mapping that names a model and carries a price."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        model = node.get("id") or node.get("model")
        if isinstance(model, str) and any(PRICE_KEY.search(str(k)) for k in node):
            out.append((model, where))
        for key, value in node.items():
            if isinstance(value, dict) and "pricing" in value and MODEL_ID.fullmatch(str(key)):
                out.append((str(key), f"{where}.{key}"))
            else:
                out += _priced(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out += _priced(value, f"{where}[{i}]")
    return out


def price_entries(config: Path) -> dict[str, list[str]]:
    """Every price entry of every model, across all of ``config/*.yaml``."""
    entries: dict[str, list[str]] = {}
    for path in sorted(config.glob("*.yaml")):
        for model, where in _priced(yaml.safe_load(path.read_text()), path.name):
            entries.setdefault(model, []).append(where)
    return entries


def used_model_ids() -> set[str]:
    """Every model id written in ``src/`` or ``config/``."""
    ids: set[str] = set()
    for top in ("src", "config"):
        for path in (ROOT / top).rglob("*"):
            if path.suffix in (".py", ".yaml", ".yml", ".json", ".md") and path.is_file():
                ids |= set(MODEL_ID.findall(path.read_text(errors="ignore")))
    return ids


def test_single_price_table() -> None:
    entries = price_entries(ROOT / "config")
    used = used_model_ids()
    assert used, "the scan found no model id"
    wrong = {m: entries.get(m, []) for m in used if len(entries.get(m, [])) != 1}
    assert wrong == {}
    assert all(w[0].startswith("models.yaml.models.") for w in entries.values())


def test_a_model_priced_in_both_files_fails(tmp_path: Path) -> None:
    for name in ("models.yaml", "chat_models.yaml"):
        (tmp_path / name).write_text((ROOT / "config" / name).read_text())
    chat = yaml.safe_load((tmp_path / "chat_models.yaml").read_text())
    chat["models"][0]["input_usd_per_million"] = "2.00"
    (tmp_path / "chat_models.yaml").write_text(yaml.safe_dump(chat))
    model = chat["models"][0]["id"]
    assert len(price_entries(tmp_path)[model]) == 2


def test_prices_are_numerically_unchanged() -> None:
    table = prices()
    assert set(table) == set(OLD)
    registry = load_chat_registry()
    for model, (inp, read, write, out) in OLD.items():
        p = table[model]
        assert (p.input, p.cached_read, p.cache_write, p.output) == tuple(
            Decimal(v) for v in (inp, read, write, out)
        ), model
        # research calls: one million tokens of each kind at the old prices
        assert price(model, ReplyUsage(1_000_000, 1_000_000, 1_000_000, 1_000_000)) == (
            Decimal(inp) + Decimal(read) + Decimal(write) + Decimal(out)
        ), model
        # Ask and chat turns (``actual_cost``, ``api.routes.ask._turn_cost``)
        chat = registry.models.get(model)
        if chat is not None:
            assert actual_cost(chat, ChatUsage(1_000_000, 1_000_000, 1_000_000, 1_000_000)) == (
                Decimal(inp) + Decimal(read) + Decimal(write) + Decimal(out)
            ), model


def test_the_chat_catalogue_references_priced_models_only(tmp_path: Path) -> None:
    from account_intelligence.chat.registry import ChatConfigInvalid

    raw = yaml.safe_load((ROOT / "config/chat_models.yaml").read_text())
    assert all(not PRICE_KEY.search(k) for m in raw["models"] for k in m)
    raw["models"].append({**raw["models"][0], "id": "model-without-a-price"})
    (tmp_path / "chat_models.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ChatConfigInvalid, match="models.yaml"):
        load_chat_registry(tmp_path / "chat_models.yaml")
