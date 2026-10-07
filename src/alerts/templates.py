"""Alert text by template (AL1, AL5, AL7): built only from a delta item's fields; sensitive events
only through ``alerts.sensitive``; every text passes the wording lint and the client-output scan.
No provider names, credit counts, cost or other accounts can appear: the inputs carry none.

GUARANTEE(test_every_template_is_lint_clean_and_client_safe)
"""

from __future__ import annotations

from dataclasses import dataclass

from account_intelligence.safety.client_output import assert_client_safe
from alerts import lint, sensitive
from signals.lead import LEAD_LABEL

_SUBJECT = {
    "new_build_role": "New role posted at {account}",
    "leadership_change": "Leadership change at {account}",
    "tier_one_news": "News at {account}",
    "funding_ma": "Funding or acquisition news at {account}",
    "budget": "Monitoring or run budget notice",
    "new_tech_detection": "Technology change at {account}",
    "exec_speaking": "Executive speaking at {account}",
    "priority_a_posts": "New post by a priority contact at {account}",
    "lead_regraded_up": "A lead was confirmed at {account}",
    "confidence_drop": "Confidence dropped on a fact at {account}",
    "facility_expansion": "Facility expansion at {account}",
    "vendor_customer_story": "Customer story naming {account}",
}


@dataclass(frozen=True)
class Message:
    """A rendered alert."""

    subject: str
    body: str


def render(
    rule_id: str,
    *,
    account: str,
    summary: str,
    grade: str,
    source_code: str | None,
    event_type: str,
    is_lead: bool,
    app_link: str,
) -> Message:
    """The message for one alert (raises on lint or scan failure: nothing unsafe is returned)."""
    cat = sensitive.category(event_type)
    if cat is not None:
        subject, body = sensitive.subject(cat), sensitive.render(cat, source_code)
    else:
        subject = _SUBJECT[rule_id].format(account=account)
        label = f" ({LEAD_LABEL})" if is_lead else ""
        src = f" Source {source_code}." if source_code else ""
        body = f"{summary} Grade: {grade}{label}.{src}"
    body = f"{body} Open in the app: {app_link}"
    lint.check(subject)
    lint.check(body)
    assert_client_safe([subject, body])
    return Message(subject, body)
