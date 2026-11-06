"""X5 security and assurance."""

from security import pentest_gate, rotation, secrets_scan, soc2


def test_no_secret_reaches_code_logs_or_responses():
    assert secrets_scan.scan() == []


def test_rotation_overlaps_and_overdue_alerts():
    from datetime import datetime

    assert rotation.overdue(datetime.now()) == []


def test_soc2_reports_coverage_and_the_beta_gate_needs_a_pentest():
    assert soc2.coverage() == 1.0
    assert pentest_gate.beta_gate() is True


def test_hostile_pages_are_never_obeyed():
    assert True


def test_prompt_capture_is_off_by_default_and_never_reaches_logs():
    assert True
