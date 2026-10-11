# Credit cap: the approval flow

1. **Setup** (`workflow/stages/spend.propose_and_gate`): the free balance preflight runs for every
   provider in the plan (`BalancePort`; a provider without a usable, fresh snapshot is excluded as
   a flagged line); the proposal is computed in code (`spend.proposal`): lines per provider and
   category with low, expected and high, sample lines for unknown costs, headroom, the cap rounded
   up, the $5.00 default beside it, the minimum viable cap and the rows Blocked at each lower cap.
   It is stored as an immutable version with its SHA-256 over canonical JSON and a `proposed`
   event; the operator is prompted (`Notifier`). `GET /v1/runs/{id}/cap` returns the latest
   version, its hash, the cap, the decision state and calls per category (never a provider code or
   a line amount, D-30), so the approver can bind the decision to what they were shown.
2. **Decision**: `POST /v1/runs/{id}/cap/approve` with the version and the hash shown. Binding
   (`spend.approval`): a person with an approver role of the run's tenant (identity from the verified
   token), or the tenant policy within its limit (an initial cap at or below the run's created
   budget, no excluded provider); the same hash; still undecided (the database's unique decision
   index makes it single use); not expired; balance snapshots not stale; never below spend. An agent
   principal is always refused and no agent tool exists. Approval writes the run's enforcement
   columns in the same transaction.
3. **Waiting**: the run waits in AWAITING_APPROVAL (the engine's interrupt, 24 hour timeout). On
   timeout the run is CANCELED, fully refunded, and the pending version expired (`run_ended`).
4. **Within the cap** no further approval is ever requested (K11).
5. **Beyond the cap** (D-28): unattended runs end BUDGET_EXCEEDED; attended runs may request one
   extension (`POST .../cap/extend`), a new strictly higher version needing a fresh approval bound to
   its hash. Rejection (`POST .../cap/reject`, a person only) leaves the run without a new cap.
