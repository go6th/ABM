# ADR-0012: source registry and lineage (D-15, D-11 extension)

- Status: Proposed (needs product owner)
- Date: 2026-10-02
- Deciders: TODO (product owner)

## Context
Tiers T1 to T4 and lineage groups need a registry (skill reference 06, step 4). No source document
lists domains, tiers or broker upstreams; D-11 (how lineage is decided) is open.

## Decision (proposal, `config/source_registry.yaml`)
* T1 for the run's account domain, its subdomains and verified careers host, derived from run input;
  they share one lineage group (`account_primary`): the company is their single upstream.
* Other domains only from configuration, each with a `source`; none are listed yet. An unlisted domain
  is T4, its own group, lineage unknown, flagged `unclassified_source`.
* Broker records and feed items are tiered by provider code (never a vendor name), currently T2 and
  aggregator with `source: TODO`. A record that states its upstream (a news event's article URL) uses
  the upstream host as its group; otherwise the provider's group with lineage unknown.
* D-11 extension: an unknown-lineage group counts toward confidence, but if it is what lets a claim
  reach A, the claim is capped at B and flagged `needs_lineage_review`.

## Consequences
Until real entries exist, most third-party sources are T4: grade A needs the account's own pages plus
a configured T1/T2 source. Fill the registry with the product owner; each entry needs a source.
