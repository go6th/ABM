# Scale smoke (report only)

Seeded 50 synthetic tenants, 5000 accounts and 200000 Facts by grade rows (one finished run per tenant) in 7.6 s, on a local PostgreSQL 16 container (laptop; not a production measurement). Queries run as `app_rw` inside the tenant's RLS context; median of 5 per tenant over a sample of 10 tenants, then the median of those.

No latency target was given, so none is asserted. The plan check is asserted by `tests/phase3/test_scale_plan.py`.

| Query | Median ms |
|---|---|
| first page | 0.52 |
| sorted by lane | 3.03 |
| search | 5.72 |
| ten keyset pages | 10.89 |

Plan of the first page:

```
Limit  (cost=0.44..210.34 rows=51 width=300)
  InitPlan 1 (returns $0)
    ->  Result  (cost=0.00..0.02 rows=1 width=16)
  ->  Index Scan using ix_view_rows_table on view_rows  (cost=0.42..300.87 rows=73 width=300)
        Index Cond: ((tenant_id = $0) AND (run_id = '0da5bc0a-335b-4ec9-a530-a5c8d6fcc220'::uuid) AND (table_kind = 'facts'::text))
```
