"""The workspace repair stage of "Download everything": every table of every account workspace is
de-duplicated, entity-tagged, dated from the source, filled or explicitly marked, and checked
before it is zipped. Pure and account-neutral: everything an account needs (names, domains,
entity rules, window, lanes, exclusions, consents) arrives in a ``WorkspaceProfile``; nothing here
names an account. Spec: ``docs/repair/workspace_repair_spec.md``."""
