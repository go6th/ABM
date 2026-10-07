# Delta record and the diff-first Summary (RF6, RF7)

`refresh.delta.compute` compares the facts the account knew with the facts after a probe or
refresh, by identity (the item key):

| Kind | Meaning | Stored with |
|---|---|---|
| new | an identity present only after | fact id, grade, source code |
| regraded | same identity, different product grade | before, after, direction |
| expired | moved to history this refresh | the expired fact |
| count_change | a counted attribute changed | before, after, the base it was counted over |

An empty delta is stored (`no_change`, what was checked, when). The delta is the only input to the
Summary diff and the alert engine: every alert row has a NOT NULL `fact_delta_id`.

`refresh.summary_diff.render` writes the section that opens a refreshed Summary: "What changed
since version N (date)", grouped by kind, every line with its source code, sensitive items in the
fixed preparation wording, "No change. Checked: ..., on ..." when empty; the text passes the
wording lint. Building the refreshed Word and PDF through the export builders, the gate and
Phase 3 parity is OI-P4-2 (Partial).
