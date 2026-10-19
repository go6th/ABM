"""The workspace's table-answer prompt carries the account research classification rules."""

from account_intelligence.replica.model_answer import TABULAR_RULES


def test_platform_classification_uses_role_context_and_applies_the_drop_list_everywhere() -> None:
    rules = " ".join(TABULAR_RULES.split())
    assert "platform-specific responsibility, core duty or requirement is genuine" in rules
    assert "generic office-software skills list" in rules
    assert "does not disqualify a separate platform-specific responsibility" in rules
    assert "SFE/SFI (Sales Force Effectiveness or Sales Force Incentive)" in rules
    assert "former-employer recruiting boilerplate" in rules
    assert "raw name-match counts separate from genuine-platform counts" in rules
    assert "apply each drop list to every count, table and file" in rules
    assert "state the strict zero first with its base" in rules
    assert "clear `Relaxed set` label" in rules
    assert "Every count names its base" in rules
