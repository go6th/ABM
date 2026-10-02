"""Rebuild the contract cassettes from the committed provider source snapshots.

Every cassette body is copied verbatim from a first-party example in
docs/providers/sources/ (see docs/providers/sources/INDEX.md for URL, date and hash).
Run: python tests/connectors/build_cassettes.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "providers" / "sources"
OUT = ROOT / "docs" / "providers" / "contract" / "cassettes"


def _openapi_yaml(md: Path) -> dict[str, Any]:
    text = md.read_text(encoding="utf-8")
    block = re.search(r"````yaml[^\n]*\n(.*?)````", text, re.S)
    assert block, md
    return yaml.safe_load(block.group(1))


def _json_blocks(md: Path) -> list[Any]:
    out = []
    for block in re.findall(r"```json[^\n]*\n(.*?)```", md.read_text(encoding="utf-8"), re.S):
        try:
            out.append(json.loads(block))
        except json.JSONDecodeError:
            continue  # documentation blocks with comments or trailing commas
    return out


def write(code: str, name: str, body: Any, source: str) -> None:
    path = OUT / code / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    cassette = {
        "source": source,
        "response": {"status": 200, "content_type": "application/json", "body": body},
    }
    path.write_text(json.dumps(cassette, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def main() -> None:
    search = _openapi_yaml(SRC / "p_search" / "search.openapi.md")
    write(
        "p_search",
        "web_search",
        search["paths"]["/v1/search"]["post"]["responses"]["200"]["content"]["application/json"][
            "example"
        ],
        "docs/providers/sources/p_search/search.openapi.md"
        " paths./v1/search.post.responses.200.example",
    )
    extract = _openapi_yaml(SRC / "p_search" / "extract.openapi.md")
    write(
        "p_search",
        "read_page",
        extract["paths"]["/v1/extract"]["post"]["responses"]["200"]["content"]["application/json"][
            "example"
        ],
        "docs/providers/sources/p_search/extract.openapi.md"
        " paths./v1/extract.post.responses.200.example",
    )
    scrape = _json_blocks(SRC / "p_scrape" / "scrape-feature.md")[0]
    write(
        "p_scrape",
        "scrape_job_page",
        scrape,
        "docs/providers/sources/p_scrape/scrape-feature.md first JSON response example",
    )
    pl = json.loads((SRC / "p_signals" / "v3-openapi.company-feeds-extract.json").read_text())
    for op, schema in [
        ("news_events", "NewsEventsDataset"),
        ("job_openings", "JobOpeningsDataset"),
        ("tech_detections", "TechnologyDetectionsDataset"),
    ]:
        write(
            "p_signals",
            op,
            pl["refs"][f"#/components/schemas/{schema}"]["example"],
            "docs/providers/sources/p_signals/v3-openapi.company-feeds-extract.json"
            f" {schema}.example",
        )
    so = json.loads((SRC / "p_org" / "v9-openapi.extract.json").read_text())
    paths = so["paths"]
    write(
        "p_org",
        "org_resolve",
        paths["/v9/organizations"]["post"]["responses"]["200"]["content"]["application/json"][
            "example"
        ],
        "docs/providers/sources/p_org/v9-openapi.extract.json /v9/organizations 200 example",
    )
    write(
        "p_org",
        "org_tech_stack",
        paths["/v9/organizations/{organization_id}/techs"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["example"],
        "docs/providers/sources/p_org/v9-openapi.extract.json"
        " /v9/organizations/{id}/techs 200 example",
    )
    people = paths["/v9/people"]["post"]["responses"]["200"]["content"]["application/json"][
        "example"
    ]
    write(
        "p_org",
        "people_by_technology",
        people,
        "docs/providers/sources/p_org/v9-openapi.extract.json /v9/people 200 example (verbatim;"
        " no async status field)",
    )
    # people.md "Example: start then poll": the start reply is quoted verbatim; the poll reply
    # is the OpenAPI example plus the two async fields people.md documents for a succeeded poll.
    write(
        "p_org",
        "people_start",
        {
            "request_id": "019d...",
            "status": "pending",
            "credits_used": 0,
            "credits_remaining": 6571,
        },
        "docs/providers/sources/p_org/people.md 'Example: start then poll' Start tab",
    )
    write(
        "p_org",
        "people_poll_succeeded",
        {**people, "request_id": "019d...", "status": "succeeded"},
        "OpenAPI /v9/people 200 example + {request_id, status: succeeded} per people.md"
        " 'Asynchronous requests'",
    )


if __name__ == "__main__":
    main()
