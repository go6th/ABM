"""Fresh-MFA step-up policy unit tests."""

from datetime import UTC, datetime, timedelta

from identity.sessions import SessionPolicy, step_up_fresh


def _policy() -> SessionPolicy:
    return SessionPolicy(
        idle_timeout_s=1800,
        absolute_timeout_s=28800,
        touch_interval_s=60,
        step_up_window_s=600,
        admin_requires_mfa=True,
        session_cookie="session",
        preauth_cookie="preauth",
        csrf_header="X-CSRF-Token",
        same_site="Lax",
        state_ttl_s=600,
        clock_skew_s=60,
        max_assertion_bytes=262144,
        membership_cache_s=60,
        download_ttl_s=300,
    )


def test_step_up_requires_a_non_future_timestamp_inside_the_window() -> None:
    now = datetime(2026, 3, 2, 9, 10, tzinfo=UTC)
    assert step_up_fresh(now - timedelta(minutes=9), now, _policy())
    assert not step_up_fresh(now - timedelta(minutes=10), now, _policy())
    assert not step_up_fresh(now + timedelta(seconds=1), now, _policy())
    assert not step_up_fresh(None, now, _policy())
