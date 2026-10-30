"""E2E scenario server for the worker (ai_e2e only, hermetic): same as the stock hermetic
providers, but the job feed carries several long postings that name platforms (the stock cassette
text is 191 chars, under jobs_v1.yaml minimum_text_length 200, so no posting is ever 'read'), and
an optional extra NEW posting read from SCENARIO_FILE at request time. Repository source is not
edited: the handlers are replaced in the hermetic module before the app is built."""
import json, os, sys
from urllib.parse import unquote
import uvicorn

from account_intelligence.connectors import hermetic as H
from account_intelligence.connectors.base import ProviderCode

SCENARIO_FILE = os.environ.get("SCENARIO_FILE", "")

BASE = [
    ("s1", "Salesforce Developer", "open", "2026-08-01", "Pune, India", "Salesforce Apex triggers, Lightning components and MuleSoft integrations"),
    ("s2", "ServiceNow Platform Administrator", "open", "2026-07-15", "Austin, TX", "ServiceNow ITSM administration, flows and catalog items"),
    ("s3", "SAP S/4HANA Finance Consultant", "closed", "2026-03-10", "Frankfurt, Germany", "SAP S/4HANA finance configuration and migration cutover"),
    ("s4", "Tableau Analytics Lead", "open", "2026-09-02", "London, UK", "Tableau dashboards, Snowflake data models and stakeholder reporting"),
    ("s5", "Data Platform Engineer", "closed", "2026-02-01", "Remote, US", "Snowflake, dbt and Airflow pipelines for the analytics platform"),
]
FILLER = (" The team works with the business on delivery, testing and support across the platform,"
          " and the role reports to the head of engineering. You will design, build, test and run"
          " production systems, write clear documentation and mentor colleagues across the group.")


def text_for(title, blurb):
    return f"{title}. You will work on {blurb}." + FILLER


def extra():
    if SCENARIO_FILE and os.path.exists(SCENARIO_FILE):
        try:
            return [tuple(x) for x in json.load(open(SCENARIO_FILE)).get("extra", [])]
        except Exception:
            return []
    return []


def postings():
    return BASE + extra()


def signals(request):
    path = unquote(request.url.path)
    if not path.endswith("job_openings"):
        return STOCK[ProviderCode.P_SIGNALS](request)
    domain = H._domain_of(path)
    out = H.cassette("p_signals__job_openings")
    proto = out["data"][0]
    items = []
    import copy
    for sid, title, status, posted, loc, blurb in postings():
        it = copy.deepcopy(proto)
        it["attributes"].update(
            title=title, normalized_title=title, description=text_for(title, blurb),
            url=f"https://{domain}/careers/jobs/{sid}", status=status, posted_at=posted,
            first_seen_at=posted + "T00:00:00Z", last_seen_at="2026-10-28T00:00:00Z", location=loc,
        )
        it["id"] = sid
        items.append(it)
    out["data"] = items
    out.setdefault("meta", {})["count"] = len(items)
    return H._reply(out)


def scrape(request):
    body = H._json(request)
    url = str(body.get("url") or "")
    sid = url.rsplit("/", 1)[-1]
    row = next((p for p in postings() if p[0] == sid), None)
    out = H.cassette("p_scrape__scrape_job_page")
    out["data"]["markdown"] = text_for(row[1], row[5]) if row else text_for("Open role", "platform delivery")
    out["data"]["html"] = None
    out["data"].setdefault("metadata", {}).update(sourceURL=url, url=url, statusCode=200, title=row[1] if row else "Role")
    return H._reply(out)


STOCK = dict(H.HANDLERS)
H.HANDLERS[ProviderCode.P_SIGNALS] = signals
H.HANDLERS[ProviderCode.P_SCRAPE] = scrape

if __name__ == "__main__":
    port = int(sys.argv[1])
    uvicorn.run("account_intelligence.api.main:app", host="127.0.0.1", port=port, access_log=False)
