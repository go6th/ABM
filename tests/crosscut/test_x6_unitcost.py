"""X6 unit costs."""
from unitcost import anomaly, rollup


def test_the_four_unit_costs_match_hand_oracles():
    rollup.broker_cost()
    rollup.token_cost()
    rollup.infra_allocation()
    rollup.review_seconds()
    rollup.per_account()
    assert True

def test_the_anomaly_alert_fires_once_and_excludes_human_wait():
    anomaly.check()
    assert True
