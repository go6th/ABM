# Pilot feedback review: <tenant> <week of YYYY-MM-DD>

Source: `GET /v1/feedback` (admin of the tenant) or a support view-as session with the tenant's
grant. Notes are the customer's data: quote them only inside this tenant's review, never in a
cross-tenant summary.

| Account | Target (account or table/row) | Verdict | Reason | Note (tenant data) | Member | Follow-up |
|---------|-------------------------------|---------|--------|--------------------|--------|-----------|
| | | | | | | |

## Patterns

* Reasons by count (`GET /v1/usefulness/metric` `feedback`):
* Rows marked `wrong` or `out_of_date`: checked against their sources? (yes / no, by whom)
* Rows marked `missing_source`: every row links to its source, so each is a defect to file.

## Actions

| Action | Owner | Due |
|--------|-------|-----|
| | | |
