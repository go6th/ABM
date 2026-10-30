"""``python -m account_intelligence.ops.purge_account --domain bd.com [--tenant T] [--execute]``

Erase the client content of ONE account the user already deleted in the app (the app only marks
the row; ``deleted_at``). Everything is decided by the database: the migration 0096 functions
``purge_candidates`` and ``purge_deleted_account`` (what is erased, scrubbed and kept is
classified in ``config/deletion.yaml`` ``account_purge``). This command only asks, reports and
then, with ``--execute``, runs the function once per account and removes the stored objects.

* Connects with ``MIGRATOR_DATABASE_URL`` (as the migrations do). The function is the only way in:
  the migrator reads no tenant table itself, so this works on Cloud SQL, where row level security
  also binds the table owner.
* Finds every account with that exact domain across tenants (no wildcard). It REFUSES while any
  of them (within ``--tenant``) is still active, lists them as "still active, not purged" and
  changes nothing.
* Without ``--execute`` it is a dry run: it prints, per table, the rows it would erase, and stops.
* With ``--execute``: for each account, (1) the research-cache index entries that point at the
  account's calls are removed (``REDIS_URL``; skipped with a warning when unset), (2) the database
  function erases the rows in one transaction, all or nothing, and writes one audit row, (3) the
  stored objects of the account are deleted by prefix AFTER the rows (``PAYLOAD_BUCKET``, or
  ``PAYLOAD_DIR`` locally), so a failure leaves orphan objects, never dangling rows. Running it
  again with ``--account A --tenant T --execute`` finishes the objects of an account whose rows are
  already gone.
* Prints counts and identifiers only, never a name, a passage or a domain of the client's data.

Exit codes: 0 done (or dry run); 1 refused (a live account, a shared payload, a research still
open) or a step failed; 2 bad arguments or environment; 3 nothing found for the domain.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO
from uuid import UUID

import psycopg

from tenancy import lifecycle
from tenancy.lifecycle import ObjectEraser

DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
DEFAULT_ACTOR = "operator:purge-account"
REDIS_BATCH = 500

Connect = Callable[[str], "psycopg.Connection[Any]"]
StoreFactory = Callable[[Mapping[str, str]], ObjectEraser | None]
CacheFactory = Callable[[Mapping[str, str]], "CacheIndexEraser | None"]


class CacheIndexEraser:
    """Removes research-cache index entries (``rc:v1:<tenant>:<args hash>``) from Redis."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def delete(self, tenant_id: UUID, args_hashes: Sequence[str]) -> int:
        """Delete the entries; returns how many existed."""
        from account_intelligence.connectors.cache import index_key

        gone = 0
        keys = [index_key(tenant_id, h) for h in args_hashes]
        # LOOP(cache index cleanup): cap=len(keys)/REDIS_BATCH batches; exit=all keys sent
        for i in range(0, len(keys), REDIS_BATCH):
            gone += int(self._client.delete(*keys[i : i + REDIS_BATCH]))
        return gone


class DirObjectEraser:
    """``PAYLOAD_DIR`` objects (local development): deletes files under a key prefix, one batch
    per call. Keys are the same strings the cloud store uses."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).resolve()

    async def delete_prefix(self, prefix: str, limit: int) -> int:
        """Delete at most ``limit`` files under ``prefix``; returns how many."""
        from tenancy.storage import is_account_scope_target, is_tenant_prefix

        if not (is_tenant_prefix(prefix) or is_account_scope_target(prefix)):
            raise ValueError("refusing to delete outside a tenant prefix")
        base = (self._root / prefix).resolve()
        if self._root not in base.parents and base != self._root:
            raise ValueError("object key escapes the store root")
        # a key (no trailing slash) names one file; a prefix names a directory tree
        tree = prefix.endswith("/")
        parent = base if tree else base.parent
        if not parent.is_dir():
            return 0
        found = parent.rglob("*") if tree else parent.glob(f"{base.name}*")
        files = sorted(p for p in found if p.is_file())[:limit]
        for path in files:
            path.unlink()
        return len(files)


def default_store(env: Mapping[str, str]) -> ObjectEraser | None:
    """The configured object store: the payload bucket (cloud) or the payload directory (local)."""
    bucket = env.get("PAYLOAD_BUCKET")
    if bucket:
        from google.cloud import storage as gcs  # type: ignore[attr-defined]

        from account_intelligence.api.tenant_plane import GcsObjectStore
        from account_intelligence.platform.gcp.adapters import widen_pool

        return GcsObjectStore(widen_pool(gcs.Client(project=env.get("GCP_PROJECT"))), bucket)
    root = env.get("PAYLOAD_DIR")
    return DirObjectEraser(root) if root else None


def default_cache(env: Mapping[str, str]) -> CacheIndexEraser | None:
    """The research-cache index (Redis) when ``REDIS_URL`` is set."""
    url = env.get("REDIS_URL")
    if not url:
        return None
    import redis

    return CacheIndexEraser(redis.Redis.from_url(url))


def _first_line(error: BaseException) -> str:
    """One line of a database error: its message, never the DETAIL (which can quote row data)."""
    text = str(error).strip().splitlines()
    return text[0] if text else type(error).__name__


def _print_report(out: TextIO, summary: Mapping[str, Any], *, executed: bool) -> None:
    verb = "erased" if executed else "would erase"
    out.write(f"  tenant {summary['tenant_id']}  account {summary['account_id']}\n")
    out.write(f"  deleted_at {summary['deleted_at']}")
    out.write("  (already a tombstone)\n" if summary["already_tombstone"] else "\n")
    tables: Mapping[str, int] = summary["tables"]
    scrubbed: Mapping[str, int] = summary["scrubbed"]
    for name in sorted(tables):
        if tables[name]:
            out.write(f"    {verb:>11} {tables[name]:>8}  {name}\n")
    for name in sorted(scrubbed):
        if scrubbed[name]:
            out.write(f"    {'scrubbed':>11} {scrubbed[name]:>8}  {name}\n")
    quiet = sum(1 for n in (*tables.values(), *scrubbed.values()) if not n)
    out.write(f"    ({quiet} tables or columns have nothing for this account)\n")
    out.write(
        f"    rows total {summary['rows_total']}; stored-object targets "
        f"{len(summary['object_prefixes']) + len(summary['object_keys'])}; cache index entries "
        f"{len(summary['cache_args_hashes'])}\n"
    )
    if summary["shared_payload_refs"]:
        out.write(
            f"    WARNING {summary['shared_payload_refs']} call(s) of OTHER accounts point at "
            "payloads stored under this account; --execute refuses unless "
            "--allow-shared-payloads is given\n"
        )


def _call(
    conn: psycopg.Connection[Any],
    tenant: UUID,
    account: UUID,
    *,
    execute: bool,
    actor: str,
    allow_shared: bool,
) -> dict[str, Any]:
    """One call of ``purge_deleted_account`` in its own transaction."""
    with conn.transaction():
        row = conn.execute(
            "SELECT purge_deleted_account(%s, %s, %s, %s, %s)",
            (tenant, account, execute, actor, allow_shared),
        ).fetchone()
    assert row is not None
    summary: dict[str, Any] = row[0]
    return summary


def _objects(
    store: ObjectEraser, tenant: UUID, account: UUID, summary: Mapping[str, Any], out: TextIO
) -> bool:
    """Delete the account's stored objects; prints what is left on a failure. True when clean."""
    try:
        targets, skipped = lifecycle.account_object_targets(tenant, account, dict(summary))
    except lifecycle.AccountPurgeRefused:
        out.write("  OBJECTS REFUSED: a target is outside this account; nothing was deleted\n")
        return False
    for key in skipped:
        out.write(f"  object key in an unknown shape, left alone: {key}\n")
    try:
        done = asyncio.run(lifecycle.erase_account_objects(store, tenant, account, dict(summary)))
    except Exception as exc:  # noqa: BLE001 - report which targets remain, whatever the cause
        out.write(f"  OBJECTS FAILED ({type(exc).__name__}); targets still to delete:\n")
        for target in targets:
            out.write(f"    {target}\n")
        return False
    out.write(f"  stored objects deleted: {done.deleted}\n")
    if not done.clean:
        out.write("  OBJECTS INCOMPLETE (batch cap); run the same command again; left:\n")
        for target in done.left:
            out.write(f"    {target}\n")
    return done.clean


def run(
    args: argparse.Namespace,
    env: Mapping[str, str],
    out: TextIO,
    err: TextIO,
    connect: Connect,
    store_factory: StoreFactory,
    cache_factory: CacheFactory,
) -> int:
    """The command after argument parsing; see the module docstring for the exit codes."""
    dsn = env.get("MIGRATOR_DATABASE_URL")
    if not dsn:
        err.write("MIGRATOR_DATABASE_URL is not set\n")
        return 2
    store: ObjectEraser | None = None
    if args.execute and not args.skip_objects:
        store = store_factory(env)
        if store is None:
            err.write(
                "no object store is configured (PAYLOAD_BUCKET or PAYLOAD_DIR); nothing was "
                "changed. Set one, or pass --skip-objects to leave the stored objects behind.\n"
            )
            return 2
    conn = connect(dsn)
    try:
        targets: list[tuple[UUID, UUID]]
        if args.account:
            targets = [(args.tenant, args.account)]
            out.write(f"account {args.account} (by id)\n")
        else:
            found = [
                (UUID(str(t)), UUID(str(a)), d)
                for t, a, d in conn.execute("SELECT * FROM purge_candidates(%s)", (args.domain,))
                if args.tenant is None or UUID(str(t)) == args.tenant
            ]
            if not found:
                err.write("no account with this domain\n")
                return 3
            live = [(t, a) for t, a, d in found if d is None]
            if live:
                for t, a in live:
                    out.write(f"still active, not purged: tenant {t} account {a}\n")
                err.write(
                    "refused: an account with this domain is still active in the app; delete it "
                    "there first, or narrow the command with --tenant. Nothing was changed.\n"
                )
                return 1
            targets = [(t, a) for t, a, _d in found]
            out.write(f"{len(targets)} deleted account(s) with this domain\n")
        failed = False
        for tenant, account in targets:
            try:
                plan = _call(
                    conn, tenant, account, execute=False, actor=args.actor, allow_shared=False
                )
            except psycopg.Error as exc:
                err.write(f"refused: {_first_line(exc)}\n")
                failed = True
                continue
            out.write("DRY RUN\n" if not args.execute else "PLAN\n")
            _print_report(out, plan, executed=False)
            if not args.execute:
                continue
            if plan["shared_payload_refs"] and not args.allow_shared_payloads:
                err.write("refused: other accounts' calls share this account's payloads\n")
                failed = True
                continue
            cache = cache_factory(env)
            hashes = list(plan["cache_args_hashes"])
            if hashes and cache is None:
                out.write(
                    f"  WARNING REDIS_URL is not set: {len(hashes)} research-cache index "
                    "entries stay until they expire (7 days); start the first research of a "
                    're-added domain with run_input {"fresh": true}\n'
                )
            elif hashes and cache is not None:
                try:
                    out.write(f"  cache index entries removed: {cache.delete(tenant, hashes)}\n")
                except Exception as exc:  # noqa: BLE001 - nothing is erased yet; stop cleanly
                    err.write(
                        f"cache index cleanup failed ({type(exc).__name__}); nothing erased\n"
                    )
                    failed = True
                    continue
            try:
                done = _call(
                    conn,
                    tenant,
                    account,
                    execute=True,
                    actor=args.actor,
                    allow_shared=args.allow_shared_payloads,
                )
            except psycopg.Error as exc:
                err.write(f"FAILED, nothing was erased: {_first_line(exc)}\n")
                failed = True
                continue
            out.write("EXECUTED (one transaction)\n")
            _print_report(out, done, executed=True)
            if store is not None and not _objects(store, tenant, account, done, out):
                failed = True
        if not args.execute:
            out.write("dry run: nothing was changed. Add --execute to erase.\n")
        return 1 if failed else 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(
        prog="python -m account_intelligence.ops.purge_account",
        description="Erase the client content of an account the user already deleted.",
    )
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--domain", help="exact domain of the deleted account, e.g. bd.com")
    which.add_argument(
        "--account", type=UUID, help="an account id (with --tenant): finish a purge, e.g. objects"
    )
    parser.add_argument("--tenant", type=UUID, help="limit to one tenant (required with --account)")
    parser.add_argument("--execute", action="store_true", help="erase; without it: a dry run")
    parser.add_argument(
        "--skip-objects", action="store_true", help="erase the rows only; leave stored objects"
    )
    parser.add_argument(
        "--allow-shared-payloads",
        action="store_true",
        help="go ahead although other accounts' calls point at this account's payloads",
    )
    parser.add_argument("--actor", default=DEFAULT_ACTOR, help="who runs it (audit row)")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    connect: Connect | None = None,
    store_factory: StoreFactory = default_store,
    cache_factory: CacheFactory = default_cache,
) -> int:
    """Parse, validate, run. ``env``, the streams and the factories are injectable for tests."""
    out = out or sys.stdout
    err = err or sys.stderr
    environment: Mapping[str, str] = os.environ if env is None else env
    parser = build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit:
        return 2
    if args.domain is not None:
        args.domain = args.domain.strip().lower()
        if not DOMAIN.match(args.domain) or len(args.domain) > 253:
            err.write("--domain must be one plain domain such as bd.com (no wildcard)\n")
            return 2
    elif args.tenant is None:
        err.write("--account needs --tenant\n")
        return 2
    return run(
        args,
        environment,
        out,
        err,
        connect or (lambda dsn: psycopg.connect(dsn, autocommit=True)),
        store_factory,
        cache_factory,
    )


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
