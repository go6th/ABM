"""Sensitive wording and the outreach lint (AL5, D-76). Lying scenarios: a breach story written as
a reason to call, outreach phrasing in any template, a sensitive event in an immediate subject."""

from __future__ import annotations

import pytest

from alerts import lint, sensitive

OUTREACH = [
    "Great time to reach out to their CIO",
    "Congratulations on the funding!",
    "Use this as an opening line",
    "Perfect timing to pitch our services",
    "Now is the moment to connect",
    "An opportunity to discuss their recovery",
    "Sorry to hear about the layoffs, we can help them recover",
    "Leverage this news in your next email",
    "Here are three talking points",
    "Send an email to the VP today",
    "Book a meeting with their CISO",
    "a good conversation starter",
    "This is your hook",
]


@pytest.mark.parametrize("text", OUTREACH)
def test_the_wording_lint_rejects_outreach_phrasing(text: str) -> None:
    assert lint.problems(text)
    with pytest.raises(lint.WordingRefused):
        lint.check(text)


def test_neutral_wording_and_the_fixed_label_pass() -> None:
    for ok in (
        "New role posted at acme.example",
        "Grade: confirmed. Source S-0012.",
        sensitive.LABEL,
        "Review it before any contact.",
    ):
        assert lint.check(ok) == ok


def test_sensitive_events_render_only_from_fixed_templates() -> None:
    for event, cat in (
        ("data_breach", "breach"),
        ("layoff", "layoff"),
        ("class_action", "lawsuit"),
        ("ransomware", "breach"),
    ):
        assert sensitive.category(event) == cat
        text = sensitive.render(cat, "S-0007")  # type: ignore[arg-type]
        assert text.startswith(sensitive.LABEL) and "S-0007" in text
        assert lint.check(text) == text
        subject = sensitive.subject(cat)  # type: ignore[arg-type]
        assert cat not in subject.lower() and "breach" not in subject.lower()
    assert sensitive.category("funding_round") is None
    assert "source on file" in sensitive.render("lawsuit", None)
