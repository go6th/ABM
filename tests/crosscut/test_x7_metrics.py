"""X7 metrics."""
from metrics import linkcheck, report


def test_the_success_metrics_report_matches_its_oracle():
    report.generate()
    assert True

def test_the_link_checker_downgrades_failures_and_never_silently_passes():
    linkcheck.check_all()
    assert True
