# ADR-0019: LLM access path (decision D-19)

- Status: Proposed (needs the owner's choice per environment)
- Date: 2026-10-03

## Decision (proposal)

One `LlmPort` with two production adapters over the same verified Anthropic SDK call
(`messages.create(model, max_tokens, system, messages)`, anthropic 1.11.0): **Claude on Vertex
AI** (`LLM_ACCESS=vertex_ai`, default: no key leaves Google Cloud, billing on the project,
service-account auth) or **the Anthropic API** (`LLM_ACCESS=anthropic_api`, key in Secret
Manager). Model ids live only in `config/models.yaml`; tiers (planner, extractor, classifier,
judge...) resolve through the active arm.

## Verified and not verified

- Verified against the installed package: the request parameters, the response's text blocks and
  usage fields (including cache read and write tokens), `AsyncAnthropicVertex(project_id, region)`.
- **`messages.create` in anthropic 1.11.0 has no `temperature` parameter** (OI-LLM-1): the spec's
  fixed temperature cannot be set; determinism rests on schema validation and the bounded re-ask.
- SDK clients are built with `max_retries=0` (W6, tested).
- Not verified: the Vertex model id format and which models are enabled in the chosen region
  (VERIFY(V-LLM-2)).
- The active arm (`single`) routes the judge and the generator to one model; the judge is then not
  distinct and grading caps judged claims (G12). Choosing the `split` arm or a distinct judge
  profile is part of D-19 and P0-2.
