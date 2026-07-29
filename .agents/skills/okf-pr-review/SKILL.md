---
name: okf-pr-review
description: Review HOKF changes for OKF v0.2 conformance, architecture boundaries, static-site regressions, security, and reproducibility. Use for pull requests or diffs touching okf/, adapter or validator code, Hugo templates, Pagefind, GitHub Actions, documentation, dependencies, or repository Agent Skills.
---

# Review HOKF changes

## Scope the review

Inspect `git status --short`, the complete diff, and relevant surrounding files.
Prioritize actionable defects introduced by the change. Do not modify or post
review results unless explicitly requested.

## Check OKF and generation

- Require only a non-empty `type` on non-reserved concepts.
- Accept unknown types, missing optional metadata, and producer extensions.
- Confirm nested v0.2 metadata survives adapter generation without structural
  loss and canonical `type` remains available as Hugo's native page type.
- Confirm `generated` supersedes `timestamp`, with legacy input remaining a
  warning and renderer fallback.
- Enforce reserved `index.md` and `log.md` structures.
- Keep canonical knowledge in `okf/`; reject hand-maintained or committed
  `site/content/`.
- Keep conformance errors blocking and broken links, staleness, orphans, tags,
  and duplicates advisory by default.

## Check the static site and supply chain

- Exercise Hugo with a GitHub Pages project-path base URL and inspect internal
  links, navigation, optional metadata, and unknown types.
- Confirm Pagefind indexes useful content, excludes boilerplate, and remains
  optional for a plain Hugo preview.
- Require pinned Hugo and Pagefind versions, a lockfile, clean installs, and no
  floating `npx -y`.
- Check workflow triggers, untrusted expression interpolation, action sources,
  artifact boundaries, separate build/deploy jobs, and least-privilege
  permissions.
- Challenge new dependencies, runtime services, heavy themes, duplicate
  generated output, and unnecessary custom code.

## Run focused checks

```sh
python3 tools/okf_validate.py --src okf
python3 -m unittest discover -s tests -v
python3 tools/okf_hugo_adapter.py --src okf --dst site/content --clean
hugo --source site --destination public --cleanDestinationDir \
  --baseURL https://example.github.io/hokf/
npm ci --ignore-scripts
npm run pagefind
```

Validate changed skills with the checked-in skill validation command documented
in `AGENTS.md`. Report findings by severity with file and line, impact,
evidence, and a concrete fix. If no high-confidence defect remains, say so
plainly.
