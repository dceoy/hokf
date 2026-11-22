---
type: Curation Principle
title: Evidence-backed knowledge curation
description: Promote repeated evidence into durable concepts without treating generated narratives as authoritative facts.
tags:
  - curation
  - evidence
  - provenance
status: stable
generated:
  by: process:chatgpt
  at: 2026-11-22T14:48:00Z
sources:
  - resource: https://github.com/dceoy/aims/blob/cc41093274834e2c972f7fd451ca38267207a0ed/.agents/skills/aims-okf-curator/SKILL.md
    title: AIMS OKF theme curation workflow (historical)
  - resource: https://github.com/dceoy/aims/blob/cc41093274834e2c972f7fd451ca38267207a0ed/src/aims/okf_curation.py
    title: AIMS deterministic theme curation implementation (historical)
---

## Keep facts and interpretation separate

When a producer maintains validated structured records, those records remain
authoritative for measurements, identifiers, dates, scores, availability, and
other machine-checkable claims. An OKF concept captures reusable explanations,
decisions, and methodological context; AI-generated prose must not replace the
source records or silently become a second source of factual truth.

Link to the source artifact, an immutable version or dated snapshot when
available, and the specific evidence identifiers supporting a claim. Distinguish
what the evidence directly establishes from interpretation or hypotheses.
Preserve provenance and verification metadata when known.

## Identify recurring candidates

For producers with dated observations, an optional deterministic curation pass
can group similar topics by normalized terms and supporting evidence. Include
distinct observation dates, source paths and evidence IDs, and any contradictory
observations in the resulting proposal. Avoid interpreting duplicated copies
of one source as independent corroboration.

One domain-specific example from AIMS required a theme to appear on at least
**three distinct dates spanning at least 14 days** before proposing promotion.
Those numbers are illustrative, **not OKF v0.2 requirements or HOKF defaults**.
Choose and document thresholds appropriate to the producing domain, sampling
cadence, and risk of false persistence. A recurring pattern alone does not
prove a causal explanation or warrant a factual assertion.

## Promote through human review

Generate **proposals**, not automatic edits or merges. A reviewer verifies the
underlying evidence, independence, relevance, and uncertainty before approving
a new or revised concept. Keep a durable concept qualitative and link back to
the authoritative source for time-varying measurements. Update the nearest
index and the bundle log as part of the reviewed change.

Use optional `generated`, `verified`, `sources`, `status`, and
`stale_after` metadata when accurately known. Do not make these fields or
any recurrence threshold mandatory for OKF conformance.

## Review obsolescence rather than silently delete

Review concepts when `stale_after` is reached or evidence becomes obsolete,
contradicted, or no longer representative. A producer may flag a recurring
theme unseen for **60 days**, as AIMS did, but the window must reflect its
observation cadence and is not a universal retirement rule.

An absence of new observations can indicate missing data rather than a
disproved concept. Require a human decision to reverify, revise, mark
`status: deprecated`, or retire the concept; preserve relevant history in
the log. Never auto-delete or auto-merge based solely on elapsed time.

This workflow is optional process guidance, not an extension to the OKF v0.2
schema, validator severity rules, or HOKF runtime architecture.
