# ADR-0115: One price table in config/models.yaml

- Status: Accepted
- Date: 2026-11-03
- Deciders: product owner (fix prompt C, task 2, 2026-11-03)

## Context

Every model was priced twice: `config/models.yaml` `models.<id>.pricing` (research calls,
`llm.reply_cache.price`) and `config/chat_models.yaml` (Ask and chat, `chat.registry`, so
`api/routes/ask.py::_turn_cost`); three models a third time on `models.yaml` profiles
(`input_usd_per_mtok`, read by the file-mode estimate). They agreed on 2026-11-03; nothing kept them
equal.

## Decision

`llm/cost.py` is the one price table, read from `models.yaml` `models.<id>.pricing` only (four
prices, all required). `reply_cache.price`, the chat registry (and through it `_turn_cost` and chat
turns) and `documents.estimate` read it. `chat_models.yaml` keeps the chat allow-list, limits,
per-model chat `max_output_tokens` and capabilities; each id must be a priced `models.yaml` model
(an unpriced id fails the registry load); provider, display name and context window come from
`models.yaml`. The profiles carry no price. `test_single_price_table` fails on a model id used in
`src/` or `config/` without exactly one price entry, or priced in both files;
`test_prices_are_numerically_unchanged` pins every price as it was (fixture
`tests/llm/fixtures/prices_2026-11-03.yaml`).

## Consequences

No price changed, so no bill and no replay key changes. A new model needs its prices in
`models.yaml` before chat can list it.
