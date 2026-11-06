"""ADR-0135: ``abm_agents`` with no rule counts as on (the ABM graph is the product path and
Workflow v1 is deprecated); an explicit rule decides like any other flag, so an operator can still
send a client's ``full`` runs back to Workflow v1 until it is removed."""

from __future__ import annotations

from uuid import uuid4

from admin import flags
from agents.production.wiring import FLAG, abm_flag_on


def test_a_missing_abm_agents_rule_runs_the_abm_graph_and_an_explicit_rule_decides() -> None:
    tenant = uuid4()
    assert abm_flag_on(None, tenant, "standard")
    off = flags.validate({})  # a rule that lists nobody
    assert not abm_flag_on(off, tenant, "standard")
    assert abm_flag_on(flags.validate({"tenants": [str(tenant)]}), tenant, "standard")
    assert abm_flag_on(flags.validate({"plans": ["standard"]}), tenant, "standard")
    assert not abm_flag_on(flags.validate({"plans": ["enterprise"]}), tenant, "standard")
    # every other flag stays deny by default
    assert not flags.evaluate(None, "monitoring", tenant, "standard")
    assert FLAG == "abm_agents"
