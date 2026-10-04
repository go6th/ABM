# ADR-0027: export content rules (D-24)

- Status: Proposed (D-24 open)
- Date: 2026-10-04
- Deciders: TODO (product owner)

## Context

The PRD (s3.4) names the rules: no em dashes and no hyphens or dashes as punctuation, no stale
phrases, abbreviations written out on first use, nothing framed as a customer leaving Salesforce
for a partner client, sensitive findings marked as preparation. It does not give the lists, and
the legacy scripts that held them were not supplied.

## Decision (proposals in `config/export_rules.yaml`)

1. **Banned dashes apply to authored prose only**: the distill step's `report_sections` text and
   the people pipeline's templated Findings claims. The characters are the em and en dash
   (`config/output_spec.yaml`); a spaced hyphen (`a - b`) counts as punctuation, compound words do
   not. Fact values and verbatim quotes are source text and stay verbatim.
2. **Stale phrases and abbreviations: empty lists**, `source: TODO, needs product owner`. The checks
   run; nothing is invented.
3. **Partner framings**: only the rule's own words from the PRD ("leaving Salesforce",
   "Salesforce failure"); more phrases are TODO. The flag is `tenants.policy_json.salesforce_partner`
   (new; a tenant settings follow-up). A framing in authored prose fails the build; inside a quote
   the quote is withheld.
4. **A restricted term inside a verbatim quote** (a broker name, another account, a client staff
   name, an excluded person, a partner framing for a partner tenant): the sentence is withheld, the
   source link stays, the withholding is counted in the manifest and the registry, and the
   Evidence file shows the fixed withheld note. A quote is never altered.
5. **Numbers in authored text** must be registered counts (people, jobs, tracker, file counts) or
   appear in the rows the block cites; a narrative or what-would-change block that cites a fact
   that may not be stated must be phrased as a question.
6. **Text with nothing printable** once control and XML-invalid characters are removed is refused
   (`text_missing`, `name_missing`, `speaker_missing`); it is never replaced by an internal id.

## Consequences

- An empty list means the check cannot fail on that rule today; the open items list says so.
- Evidence: `tests/exports/test_policy.py`, `test_lying.py` (dash, partner, quote withholding,
  unregistered number, unprintable text), `test_mutation_kills.py`.
