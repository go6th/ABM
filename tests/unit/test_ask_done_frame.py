"""The Ask stream's done frame: what an answer used, for the token meter in Ask."""

# The Ask stream's last "done" frame: what an answer used, for the token meter in Ask.
from decimal import Decimal
from types import SimpleNamespace

from account_intelligence.api.routes.ask import _done_frame


def test_the_done_frame_carries_what_the_answer_used() -> None:
    usage = SimpleNamespace(
        input_tokens=1200, output_tokens=300, cache_read_tokens=40000, cache_write_tokens=0
    )
    frame = _done_frame("turn-1", "test-model-a", usage, Decimal("0.0200"))
    assert frame == {
        "turn_id": "turn-1",
        "replayed": False,
        "model": "test-model-a",
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_tokens": 40000,
        "cache_write_tokens": 0,
        "cost_usd": "0.0200",
    }
    # a workspace answer (no model call) reports zero tokens and its cost
    local = _done_frame("turn-2", "local", None, Decimal("0"))
    assert local["input_tokens"] == 0 and local["output_tokens"] == 0 and local["cost_usd"] == "0"
