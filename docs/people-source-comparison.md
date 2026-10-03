# People source comparison protocol (P0-3)

**Status: Blocked.** Needs real accounts, an approved credit cap, analyst time, and the p_people
grid contract (docs/providers/p_people.md). Until then the people stage uses `p_org`
(`config/people_sources.yaml`, `active: p_org`, a proposal).

## The switch

`config/people_sources.yaml` names the active source and its operations; the people stage
(`workflow/handlers.py::people_operations`) may only plan those operations. Changing `active` is a
one-line change, recorded on the run through the config hashes (`config_files.load_all`).

## Protocol

1. Pick 5 accounts with an analyst-known senior team (not the golden-account fixtures).
2. Run the people stage once per source on each account within one approved credit cap; record
   credits per person from the ledger (credits and dollars stay separate).
3. Compare with `python -m evals.people_compare a.json b.json` per account: found, overlap, only
   one source, in scope (people_scope.yaml), each with its base.
4. The analyst marks each person in the union correct, wrong employer or out of scope.
5. Decide on: in-scope correct people per 100 credits, overlap with the analyst list, and the
   source's contract and data-protection terms (P0-7). Record the decision as an ADR.
