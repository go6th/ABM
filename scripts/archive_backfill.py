"""Re-process the archived page and news copies of every account into cleaned version 2 records.

    python scripts/archive_backfill.py [--tenant UUID] [--account DOMAIN] [--apply] [--force]
                                       [--report Logs/archive_backfill_report.md]

Dry run unless ``--apply``: the report is written either way, nothing is stored without it. Reads
the stored page text only; it never fetches a page and never calls a provider or a model. A copy
with no stored text is listed in the report as needing a re-fetch (a paid call, left to you).
``--force`` rebuilds copies that are already version 2. Without ``--tenant`` every tenant the
operator overview lists is processed. See ``account_intelligence.archive_backfill``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def run(args: argparse.Namespace) -> str:
    """Process the tenants and accounts asked for; the report as markdown."""
    from sqlalchemy import text

    import agents.naming  # noqa: F401 - registers the application's page guard
    from account_intelligence.app.container import build_container
    from account_intelligence.archive import LinkArchive
    from account_intelligence.archive_backfill import Report, backfill_account
    from account_intelligence.archive_facts import Account
    from account_intelligence.db.session import tenant_txn, untenanted_session
    from admin import tenants

    payloads = build_container().payloads
    if args.tenant:
        tenant_ids = [UUID(args.tenant)]
    else:
        async with untenanted_session() as plain:
            tenant_ids = [UUID(t["tenant_id"]) for t in await tenants.overview(plain)]

    def txn(tid: UUID):  # type: ignore[no-untyped-def]
        return tenant_txn(tid, system=True)

    report = Report()
    for tid in tenant_ids:
        async with txn(tid) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, domain, name FROM accounts"
                        " WHERE (CAST(:d AS text) IS NULL OR domain = CAST(:d AS text))"
                    ),
                    {"d": args.account},
                )
            ).all()
        for row in rows:
            archive = LinkArchive(txn, tid, row.id, None, payloads, _Clock())
            await backfill_account(
                archive,
                Account(row.name or row.domain, row.domain),
                report,
                apply=args.apply,
                force=args.force,
            )
    return report.markdown(applied=args.apply, when=datetime.now(UTC).isoformat(timespec="seconds"))


def main() -> int:
    """Run the backfill and write the report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tenant")
    parser.add_argument("--account")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--report", default=str(ROOT / "Logs" / "archive_backfill_report.md"))
    args = parser.parse_args()
    markdown = asyncio.run(run(args))
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
