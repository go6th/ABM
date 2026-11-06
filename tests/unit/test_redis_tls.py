"""Redis TLS in the cloud (fix prompt D, task 4b; ADR-0125): the CA is pinned, the hostname is
checked against the Memorystore IP, and REDIS_URL cannot point anywhere else.

``ssl_check_hostname=true`` makes the client match the server certificate against the address it
dialled (Memorystore serves its certificate for the instance's IP). The compensating check holds
either way: in staging and production the URL must be ``rediss://``, carry the pinned CA, name a
private IP literal, and that IP must equal ``REDIS_PINNED_HOST`` (Terraform's
``module.cache.host``), so a free-form value cannot send the rate buckets and the cache index to
another server.
"""

from __future__ import annotations

import pytest

from account_intelligence.platform.redis_url import checked_redis_url

CA = "ssl_ca_certs=/etc/redis-ca/ca.pem"
GOOD = f"rediss://10.0.0.3:6378/0?{CA}&ssl_check_hostname=true"
CLOUD = {"APP_ENV": "staging", "REDIS_PINNED_HOST": "10.0.0.3"}


def test_local_profiles_keep_their_url() -> None:
    assert checked_redis_url("redis://localhost:6379/0", {"APP_ENV": "dev"}) == (
        "redis://localhost:6379/0"
    )


def test_the_cloud_url_is_tls_pinned_and_hostname_checked() -> None:
    assert checked_redis_url(GOOD, CLOUD) == GOOD


@pytest.mark.parametrize(
    ("url", "env", "why"),
    [
        ("redis://10.0.0.3:6378/0?" + CA, CLOUD, "rediss"),
        ("rediss://10.0.0.3:6378/0", CLOUD, "ssl_ca_certs"),
        (f"rediss://10.0.0.3:6378/0?{CA}&ssl_cert_reqs=none", CLOUD, "ssl_cert_reqs"),
        (f"rediss://10.0.0.9:6378/0?{CA}", CLOUD, "REDIS_PINNED_HOST"),
        (f"rediss://redis.example.com:6378/0?{CA}", CLOUD, "IP"),
        (f"rediss://34.1.2.3:6378/0?{CA}", {**CLOUD, "REDIS_PINNED_HOST": "34.1.2.3"}, "private"),
        (GOOD, {"APP_ENV": "production"}, "REDIS_PINNED_HOST"),
    ],
)
def test_a_cloud_url_that_could_reach_another_server_is_refused(
    url: str, env: dict[str, str], why: str
) -> None:
    with pytest.raises(ValueError, match=why):
        checked_redis_url(url, env)


def test_every_redis_client_goes_through_the_check() -> None:
    """No module builds a Redis client from a URL except through ``redis_from_url``."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    direct = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if re.search(r"Redis\.from_url\(", p.read_text()) and p.name != "redis_url.py"
    ]
    assert direct == []
