"""The test scope for a child process a test starts (a killed worker): it acts as a system job
whose rows are owned by ``test-owner``, as direct service calls in tests do (``tests/conftest.py``,
private workspaces of migration 0063)."""

from __future__ import annotations


def act_as_test_system() -> None:
    """Set this process's acting user and scope."""
    from account_intelligence.db.session import set_actor, set_system

    set_system()
    set_actor("test-owner")
