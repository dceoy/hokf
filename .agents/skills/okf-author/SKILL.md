---
name: okf-author
description: Create or update canonical Open Knowledge Format v0.2 concepts in HOKF. Use when authoring Markdown under okf/, adding concept metadata or cross-links, or recording a knowledge change in the reserved index.md and log.md files.
---

# Author OKF concepts

## Follow the canonical boundary

Edit knowledge only under `okf/`. Treat `site/content/` as disposable generated
output. Derive a concept ID from its bundle-relative path without `.md`; for
example, `okf/concepts/architecture.md` has ID `concepts/architecture`.

Search existing paths, titles, and descriptions before creating a concept.
Prefer updating or linking an existing concept over introducing a duplicate.
Never use reserved `index.md` or `log.md` filenames for concepts.

## Write OKF v0.2

Begin each concept with YAML front matter. Require only a non-empty, descriptive
`type`; tolerate unknown types and producer-defined fields.

Recommend `title`, `description`, `resource`, and a lowercase kebab-case `tags`
list where useful. Add optional metadata only when known:

- `generated: { by, at }` for authorship and the last meaningful change.
- `verified` as one `{ by, at }` mapping or a list of verification events.
- `sources` as mappings that each contain `resource`.
- `status` as `draft`, `stable`, or `deprecated`.
- `stale_after` as an absolute `YYYY-MM-DD` date.

Use `human:<id>`, `process:<id>`, or `<producer>/<version>` actors. Use an ISO
8601 datetime for event `at` fields. Never add legacy `timestamp`; preserve it
when encountered unless the task includes migrating it to `generated`.

Preserve every unknown type, key, and nested value when editing. Use standard
relative or bundle-root-relative Markdown links ending in `.md`.

## Maintain discovery and history

Add a concise link and description to the nearest `index.md`. Index files use
headings and Markdown link lists without front matter, except the bundle-root
`okf/index.md`, whose only front matter key is `okf_version: "0.2"`.

Record meaningful bundle changes in `okf/log.md` beneath a `## YYYY-MM-DD`
heading. Keep date groups newest first.

## Verify

Run:

```sh
python3 tools/okf_validate.py --src okf
python3 -m unittest discover -s tests -v
```

Fix conformance errors. Review advisory warnings and resolve those introduced by
the change; warnings describe quality, not OKF validity.
