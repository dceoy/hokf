---
name: okf-curator
description: Curate the canonical HOKF bundle and improve deterministic knowledge quality. Use when maintaining index.md or log.md, validating OKF v0.2, fixing broken links, stale or orphaned concepts, tag inconsistencies, or obvious duplicates under okf/.
---

# Curate the OKF bundle

## Establish the bundle state

Read `okf/index.md`, `okf/log.md`, and the concepts affected by the request.
Run the default validator before editing:

```sh
python3 tools/okf_validate.py --src okf
```

Treat `ERROR` findings as blocking OKF v0.2 conformance defects. Treat `WARNING`
findings as advisory quality work: they do not make a bundle nonconformant.
Use `--warnings-as-errors` only for an explicit quality gate.

## Curate deterministically

- Fix broken internal links while preserving standard relative or
  bundle-root-relative `.md` paths in canonical files.
- Add orphaned concepts to the nearest useful index with concise descriptions.
- Review concepts on or past `stale_after`; update and re-verify, deprecate, or
  deliberately extend the date based on evidence.
- Normalize tags to lowercase kebab-case and merge spelling variants.
- Consolidate or disambiguate exact normalized duplicate titles.
- Add recommended titles and descriptions when they improve discovery.
- Migrate legacy `timestamp` to `generated` only when authorship can be stated
  accurately.

Do not reject missing optional metadata. Preserve unknown `type` values,
producer-defined fields, and nested values exactly in meaning.

## Optional recurring-evidence curation

For bundles fed by dated observations, use the
[evidence-backed curation guidance](../../../okf/concepts/evidence-backed-curation.md)
to propose recurring themes deterministically:

- Collect dated, traceable, independently supported evidence; surface missing
  data and contradictions rather than treating repetition as confirmation.
- Apply producer-specific persistence thresholds. AIMS used at least three
  distinct dates over at least 14 days for a promotion candidate, but these
  values are neither HOKF defaults nor OKF v0.2 requirements.
- Produce a reviewable proposal with source links and evidence identifiers.
  Require human approval before editing canonical concepts or merging a PR.
- Consider obsolescence based on `stale_after`, contradicted evidence, and
  producer-specific absence windows (AIMS used 60 days). Missing observations
  alone do not justify automatic deletion or deprecation.

Keep this optional process separate from the validator: conformance findings
remain blocking or advisory according to the existing rules.

## Maintain reserved documents

Keep indexes as progressive-disclosure headings and link lists. Permit front
matter only in the root `okf/index.md`, and only
`okf_version: "0.2"`. Keep each `log.md` frontmatter-free with ISO
`## YYYY-MM-DD` headings ordered newest first.

## Finish

Run the validator and tests:

```sh
python3 tools/okf_validate.py --src okf
python3 -m unittest discover -s tests -v
```

Report blocking fixes separately from advisory improvements.
