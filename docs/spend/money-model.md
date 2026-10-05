# Credit cap: the money model

- **One cap per run, in USD** (D-27, ADR-0031), held in code as integer micro-dollars
  (`spend.money`; 1 USD = 1,000,000). Parsing refuses `float`, NaN, infinities and negative costs;
  a cost rounds **up** to the next micro-dollar. Provider credits are `Decimal` in their own column
  and convert to USD only through a sourced rate (none today, V7).
- **Where the cap lives**: `account_runs.budget_usd`, written from the approved
  `run_cap_versions` row (`cap_version` names it; ADR-0030). Versions are insert-only; an
  extension is a new, strictly higher version.
- **Who increments spend**: only the connector ledger's atomic reserve
  (`UPDATE account_runs SET spent_usd = spent_usd + :est ... WHERE spent_usd + :est <= budget_usd`)
  and its true-up after the call. The credit cap package decides, records, reconciles and reports;
  it never increments a counter.
- **Before every paid call** (`spend.gate.decide`, inside the ledger claim transaction): a proven
  free operation passes (unless it once charged: a defect, refused); a legacy run keeps its old
  rule; no approved cap is refused `no_cap`; a recorded overshoot stops further calls; an unknown
  cost is refused unless the approved cap carries a sample line with calls left; an estimate above
  what remains is refused `cap_reached`. A replay never reaches the gate (it costs nothing).
- **Reserve the upper bound, true up to the actual**: the reservation is the call's upper-bound
  estimate; the actual is never clamped; an actual above the reservation adds to
  `cap_overshoot_usd`, keeping `CHECK (spent_usd <= budget_usd + cap_overshoot_usd)` true and
  stopping further paid calls.
- **Derived spend** (`spend.rollup`): from the insert-only `tool_call_events`: the charge of
  `done`, `failed` and `abandoned` events plus the reservation of every attempt still open (counted
  until reconciled, K9). Parts by provider, category, operation and route always sum to the same
  total, which must equal `spent_usd` and the connector's ledger sum (K7).
- **Settlement**: once per terminal run, in the finishing transaction, with the report
  (`lifecycle.finish`): commit `reserved_credits * min(1, spent / cap)`, refund the rest
  (ADR-0016, D-04 proposal); a FAILED run refunds everything.
