"""API fuzz: every OpenAPI operation under hostile inputs never fails with a server error.

For each operation in the application's own OpenAPI schema, Hypothesis generates path parameters,
query parameters and JSON bodies of hostile shapes (wrong types, huge numbers, control characters,
very long strings, deep nesting, unicode, injection-looking text) and sends them through the real
application (routes, validation, guard, RLS on the test database) as an admin of a synthetic tenant
with a fresh MFA step-up, so authorisation does not mask the handlers.

Asserted for every request:
* never 500, 502 or 504 (a 503 is allowed only as a ``problem+json`` dependency answer: external
  ports in the test profile are unconfigured by design);
* every JSON error is a problem document (``type`` and ``title`` or ``detail``);
* no stack trace, SQL text or Python exception name reaches the client.

GUARANTEE(test_no_operation_fails_with_a_server_error): fuzz.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.requires_postgres]

LEAKS = re.compile(
    r"Traceback|File \"/|sqlalchemy\.|psycopg|asyncpg|SELECT .* FROM|KeyError|TypeError"
)
HOSTILE_TEXT = st.one_of(
    st.text(max_size=40),
    st.sampled_from(
        [
            "",
            " ",
            "../../etc/passwd",
            "' OR 1=1 --",
            "<script>alert(1)</script>",
            "\x00\x01\x1f",
            "%00",
            "a" * 5000,
            "00000000-0000-0000-0000-000000000000",
            "${jndi:ldap://x}",
            "😀" * 50,
        ]
    ),
)
SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**70), max_value=2**70),
    st.floats(allow_nan=False),
    HOSTILE_TEXT,
)
JSON = st.recursive(
    SCALARS,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=12), inner, max_size=5)
    ),
    max_leaves=12,
)


def _operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    ops = []
    for path, item in schema["paths"].items():
        if path.startswith("/tasks"):
            continue  # worker endpoints: OIDC-authenticated machine calls, fuzzed separately
        for method, op in item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                ops.append((method.upper(), path, op))
    return ops


def _body_for(op: dict[str, Any], schema: dict[str, Any]) -> st.SearchStrategy[Any]:
    content = (op.get("requestBody") or {}).get("content") or {}
    if "application/json" not in content:
        return st.none()
    props: dict[str, Any] = {}
    ref = content["application/json"].get("schema", {}).get("$ref")
    if ref:
        props = schema["components"]["schemas"][ref.rsplit("/", 1)[1]].get("properties", {})
    # half the time a well-keyed object with hostile values, half the time arbitrary JSON
    keyed = st.fixed_dictionaries({k: JSON for k in props}) if props else JSON
    return st.one_of(keyed, JSON)


@pytest.fixture(scope="module")
def fuzz_app() -> Any:
    from account_intelligence.api.auth import Identity, identity
    from tests.phase3.conftest import add_member, make_app, new_tenant

    plane = make_app()
    tenant = new_tenant("fuzz")
    add_member(tenant, "fuzz-admin", "admin")  # the membership re-check must pass

    async def admin() -> Identity:
        # a fresh MFA step-up on every call, so step-up (settings, users, caps) never hides a
        # route from the fuzz
        fresh = plane.clock.now()
        return Identity("fuzz-admin", tenant, "admin", fresh, "jwt", None, ("pwd", "mfa"))

    plane.app.dependency_overrides[identity] = admin
    return plane.app


SEEN: list[int] = []


def _check(response: httpx.Response, method: str, path: str) -> None:
    SEEN.append(response.status_code)
    where = f"{method} {path} -> {response.status_code}"
    assert response.status_code not in (500, 502, 504), where
    text = response.text
    assert not LEAKS.search(text), f"{where}: internals leaked"
    if response.status_code >= 400 and "json" in response.headers.get("content-type", ""):
        body = json.loads(text)
        assert isinstance(body, dict) and ("title" in body or "detail" in body), where
    if response.status_code == 503:
        assert "problem+json" in response.headers.get("content-type", ""), where


def _case(
    loop: asyncio.AbstractEventLoop,
    client: httpx.AsyncClient,
    schema: dict[str, Any],
    method: str,
    path: str,
    op: dict[str, Any],
) -> Any:
    """A Hypothesis test for one operation (closure, so @given sees no defaults)."""
    names = re.findall(r"{([^}]+)}", path)
    query = [p["name"] for p in op.get("parameters", []) if p.get("in") == "query"]

    @settings(
        max_examples=12, deadline=None, derandomize=True, suppress_health_check=list(HealthCheck)
    )
    @given(
        params=st.fixed_dictionaries({n: HOSTILE_TEXT for n in names}),
        q=st.fixed_dictionaries({n: HOSTILE_TEXT for n in query}),
        body=_body_for(op, schema),
    )
    def one(params: dict[str, str], q: dict[str, str], body: Any) -> None:
        url = path
        for name, value in params.items():
            url = url.replace("{" + name + "}", quote(value or "x", safe=""))
        kwargs: dict[str, Any] = {"params": q}
        if body is not None and method != "GET":
            kwargs["content"] = json.dumps(body)
            kwargs["headers"] = {
                "Content-Type": "application/json",
                "Idempotency-Key": "fuzz-key-0001",
            }
        response = loop.run_until_complete(client.request(method, url, **kwargs))
        _check(response, method, path)

    return one


def test_no_operation_fails_with_a_server_error(fuzz_app: Any) -> None:
    schema = fuzz_app.openapi()
    ops = _operations(schema)
    assert len(ops) > 50  # the whole surface, not a sample
    loop = asyncio.new_event_loop()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fuzz_app, raise_app_exceptions=False),
        base_url="http://t",
    )
    # asyncpg connections belong to the loop that opened them: the suite's shared engine pool was
    # opened on the session loop, so this loop gets its own engine for the duration
    from sqlalchemy.ext.asyncio import create_async_engine

    from account_intelligence.config import get_settings
    from account_intelligence.db import session as db

    engine = create_async_engine(get_settings().runtime_database_url, pool_size=2)
    previous = list(db._BOUND)
    db.bind_engine(engine)
    try:
        for method, path, op in ops:
            _case(loop, client, schema, method, path, op)()
    finally:
        loop.run_until_complete(client.aclose())
        loop.run_until_complete(engine.dispose())
        loop.close()
        db._BOUND[:] = previous
    # the fuzz reached handlers, not only the validators: some answers succeed
    assert len(SEEN) >= len(ops) * 5, len(SEEN)
    assert sum(1 for c in SEEN if c < 300) >= len(ops) // 4, sorted(set(SEEN))


def test_non_standard_json_is_a_422_problem_not_a_500(fuzz_app: Any) -> None:
    """Regression (found by the fuzz): ``Infinity`` in a body made the 422 unserialisable."""

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=fuzz_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.put(
                "/v1/settings/branding",
                content='{"expected_version": 0, "display_name": "x", "accent_hex": "#fff",'
                ' "logo_sha256": Infinity}',
                headers={"Content-Type": "application/json"},
            )

    from sqlalchemy.ext.asyncio import create_async_engine

    from account_intelligence.config import get_settings
    from account_intelligence.db import session as db

    async def isolated() -> httpx.Response:
        engine = create_async_engine(get_settings().runtime_database_url, pool_size=1)
        previous = list(db._BOUND)
        db.bind_engine(engine)
        try:
            return await go()
        finally:
            await engine.dispose()
            db._BOUND[:] = previous

    response = asyncio.run(isolated())
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["errors"] and "Infinity" not in response.text and "inf" not in str(body["errors"])
