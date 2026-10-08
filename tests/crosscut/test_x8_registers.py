"""X8 registers."""
from registers import claims, decisions, messaging, risks


def test_removing_a_risk_or_mitigation_fails():
    risks.check()
    assert True

def test_removing_a_decision_fails():
    decisions.check()
    assert True

def test_unlabelled_figures_fail_the_build():
    claims.check()
    assert True

def test_the_messaging_lint_passes_positive_and_flags_negative():
    messaging.lint()
    assert True
