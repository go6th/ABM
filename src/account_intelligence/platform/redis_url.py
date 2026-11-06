"""The one way to build a Redis client from ``REDIS_URL`` (ADR-0125).

In staging and production the URL must:

* be ``rediss://`` (TLS) with the pinned Memorystore CA (``ssl_ca_certs``), and must not turn
  certificate checks off (``ssl_cert_reqs=none``);
* name a private IP literal that equals ``REDIS_PINNED_HOST`` (Terraform sets it from
  ``module.cache.host``): the compensating check that holds whatever the hostname check says,
  so a free-form value cannot send the sign-in buckets and the cache index to another server.

Terraform sets ``ssl_check_hostname=true``: Memorystore serves its certificate for the instance
IP, which the client then matches (VERIFY(V-REDIS-1): the staging instance's certificate carries
that IP; the owner checks it before applying). The local profiles keep their plain URL.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

CLOUD_PROFILES = frozenset({"staging", "production"})


def checked_redis_url(url: str, env: Mapping[str, str]) -> str:
    """``url`` unchanged, or ``ValueError`` naming the rule a cloud URL breaks."""
    if env.get("APP_ENV", "dev") not in CLOUD_PROFILES:
        return url
    parts = urlsplit(url)
    query = {k: v[-1].lower() for k, v in parse_qs(parts.query).items()}
    if parts.scheme != "rediss":
        raise ValueError("REDIS_URL must be rediss:// (TLS) in the cloud")
    if not query.get("ssl_ca_certs"):
        raise ValueError("REDIS_URL must pin the Memorystore CA (ssl_ca_certs)")
    if query.get("ssl_cert_reqs", "required") != "required":
        raise ValueError("REDIS_URL must not relax ssl_cert_reqs")
    host = parts.hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("REDIS_URL must name the Memorystore IP, not a name") from None
    if not address.is_private:
        raise ValueError("REDIS_URL must name a private address")
    pinned = env.get("REDIS_PINNED_HOST", "")
    if host != pinned:
        raise ValueError("REDIS_URL host must equal REDIS_PINNED_HOST (Terraform's Memorystore IP)")
    return url


def redis_from_url(url: str, env: Mapping[str, str] | None = None, **options: Any) -> Any:
    """An asyncio Redis client on a checked URL (``sync=True`` for the blocking client)."""
    import os

    checked = checked_redis_url(url, os.environ if env is None else env)
    if options.pop("sync", False):
        import redis

        return redis.Redis.from_url(checked, **options)
    import redis.asyncio as aredis

    return aredis.Redis.from_url(checked, **options)
