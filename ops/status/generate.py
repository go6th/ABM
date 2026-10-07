"""Status page generator (HD3): a static HTML page from health data and scheduled maintenance.

Input: a JSON document ``{"generated_at", "components": [{"name", "status"}], "incidents":
[{"title", "status", "started_at"}], "maintenance": [{"title", "starts_at", "ends_at"}]}``.
Statuses come from a closed set; every text is HTML-escaped and passes the client-output scan
(no provider names, credit counts, cost, tenants or accounts). Publishing is ``NotConfigured``
until a host is chosen.

Usage: ``python ops/status/generate.py health.json > status.html``
"""

from __future__ import annotations

import html
import json
import sys
from typing import Any

from account_intelligence.platform.ports import NotConfigured
from account_intelligence.safety.client_output import assert_client_safe

STATUSES = ("operational", "degraded", "partial_outage", "major_outage", "maintenance")


def render(doc: dict[str, Any]) -> str:
    """The page (ValueError for an unknown status; ClientOutputLeak for unsafe text)."""
    rows, texts = [], []
    for c in doc.get("components", []):
        if c["status"] not in STATUSES:
            raise ValueError(f"unknown status {c['status']!r}")
        texts += [c["name"]]
        rows.append(
            f"<tr><td>{html.escape(c['name'])}</td>"
            f'<td class="{c["status"]}">{c["status"].replace("_", " ")}</td></tr>'
        )
    inc = []
    for i in doc.get("incidents", []):
        texts += [i["title"]]
        inc.append(
            f"<li>{html.escape(i['title'])} ({html.escape(i['status'])}, since "
            f"{html.escape(i['started_at'])})</li>"
        )
    mnt = []
    for m in doc.get("maintenance", []):
        texts += [m["title"]]
        mnt.append(
            f"<li>{html.escape(m['title'])}: {html.escape(m['starts_at'])} to "
            f"{html.escape(m['ends_at'])}</li>"
        )
    assert_client_safe(texts)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Service status'
        "</title></head><body><h1>Service status</h1>"
        f"<p>Updated {html.escape(doc['generated_at'])}</p><table>{''.join(rows)}</table>"
        f"<h2>Incidents</h2><ul>{''.join(inc) or '<li>None</li>'}</ul>"
        f"<h2>Scheduled maintenance</h2><ul>{''.join(mnt) or '<li>None</li>'}</ul>"
        "</body></html>"
    )


def publish(page: str) -> None:
    """Publishing needs a chosen host (D-80)."""
    raise NotConfigured("no status page host is configured")


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = argv if argv is not None else sys.argv[1:]
    with open(args[0]) as f:
        print(render(json.load(f)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
