"""Production bindings of the agent ports onto the platform (one implementation per rule).

Each adapter wraps the existing component: provider calls go through the connector ledger
(``connectors.router.call_need``: routing, pull once, credit gate), facts through the grading
engine (``grade_claims``) and its single write path, rows through ``TrackerService``, approvals
through the credit manager's pause requests, files through the export builder and the quality
gate, delivery through the delivery-note stage. ``wiring.abm_deps`` builds them for one run from
the workflow's ``RunDeps``.
"""
