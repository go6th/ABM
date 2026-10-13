"""The stop description never carries a secret and is bounded; the usable-output rule is what
separates PARTIAL from FAILED."""

from __future__ import annotations

import anthropic
import httpx

from account_intelligence.runs.stop import MAX_DETAIL, describe, redact


def test_stop_detail_never_carries_a_secret() -> None:
    key = "sk-ant-api03-" + "A" * 40
    samples = [
        f"Error code: 401 - invalid x-api-key: {key}",
        "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.signature",
        "api_key=abcdefghijklmnop1234 token: zyxwvutsrqponmlkjihg",
        "postgresql://app_rw:local-dev-only@db:5432/ai refused",
        "opaque " + "x" * 48 + " token",
    ]
    for sample in samples:
        out = redact(sample)
        assert "[redacted]" in out, sample
        assert key not in out and "eyJhbGci" not in out and "local-dev-only" not in out
        assert "abcdefghijklmnop1234" not in out and "x" * 48 not in out
    assert redact("prompt is too long: 1127200 tokens > 1000000 maximum") == (
        "prompt is too long: 1127200 tokens > 1000000 maximum"
    )


def test_describe_names_the_class_redacts_and_bounds() -> None:
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://x.invalid"),
        json={"error": {"message": "Your credit balance is too low."}},
    )
    error = anthropic.BadRequestError(
        "Error code: 400 - Your credit balance is too low. key=sk-" + "b" * 30,
        response=response,
        body=None,
    )
    text = describe(error)
    assert text.startswith("BadRequestError: Error code: 400 - Your credit balance is too low.")
    assert "sk-" + "b" * 30 not in text
    long = describe(RuntimeError("y" * 5000))
    assert len(long) <= MAX_DETAIL
    assert describe(KeyboardInterrupt()) == "KeyboardInterrupt"
    assert describe(RuntimeError("a\n  b\t c")) == "RuntimeError: a b c"
