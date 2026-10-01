# ADR-005 — Quarantine extractor for untrusted text

Date: 2026-10-01
Status: Accepted

## Context
Indirect prompt injection through scraped pages, job postings and uploaded documents is a
top risk (risk register R4, attack A2). Guard-prompt-only defences fail.

## Decision
Raw untrusted text is only ever read by a tool-less extractor model step that must emit
schema-validated JSON (quoted spans, typed attributes). Planner and tool-calling nodes see
only validated structured facts. The extractor carries a canary instruction it must never
follow; echoing it marks the source hostile and drops it.

## Consequences
One extra model hop per branch; in exchange the injection path to tools is removed
structurally, not by persuasion.

## Revisit if
The adversarial corpus (T9/T50) shows extraction dropping >10% of legitimate facts while
injection pass rate is already 100% — then revisit source-tier pre-filtering.
