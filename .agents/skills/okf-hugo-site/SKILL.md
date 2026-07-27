---
name: okf-hugo-site
description: Build, preview, troubleshoot, search, and publish HOKF's static Hugo presentation. Use when changing the OKF-to-Hugo adapter, site layouts or assets, Pagefind integration, GitHub Pages workflow, or diagnosing generated-content and deployment failures.
---

# Operate the HOKF Hugo site

## Build in dependency order

Install Python and Node dependencies, then validate, generate, render, and index:

```sh
python3 -m pip install --only-binary=:all: -r requirements.txt
npm ci --ignore-scripts
python3 tools/okf_validate.py --src okf
python3 tools/okf_hugo_adapter.py --src okf --dst site/content --clean
hugo --source site --destination public --cleanDestinationDir
npm run pagefind
```

Treat `okf/` as canonical. Never hand-edit or commit `site/content/`; it is
disposable shadow content. Do not commit `site/public/` or `node_modules/`.

For a plain local preview, generate content and run:

```sh
hugo server --source site
```

The pages remain usable before Pagefind runs; the search control explains that
the index is unavailable. Build and index first when testing search.

## Preserve presentation boundaries

Keep templates theme-free and framework-free. Render optional metadata only
when present, tolerate unknown types and fields, and read the canonical type
from Hugo's native page type. Keep internal URLs base-path aware through the
link render hook and Hugo `RelPermalink`/`relURL`.

Test project-path behavior with an explicit base URL:

```sh
hugo --source site --destination public --cleanDestinationDir \
  --baseURL https://example.github.io/hokf/
```

Confirm generated links include `/hokf/`, Pagefind indexes titles,
descriptions, tags, and body text, and navigation is excluded where marked.

## Diagnose by stage

- Validation failure: fix canonical YAML or reserved-document structure.
- Generation failure: inspect safe YAML parsing and source/destination paths.
- Missing or stale page: regenerate with `--clean`.
- Template failure: check optional values before dereferencing nested metadata.
- Broken project-path link: use the render hook or Hugo URL functions, not a
  domain-root literal.
- Empty search: rebuild Hugo, run `npm run pagefind`, and confirm
  `site/public/pagefind/pagefind-ui.js` exists.
- CI or deploy failure: compare local commands with
  `.github/workflows/pages.yml`; keep tool versions and the lockfile pinned.

Publishing occurs only through the successful default-branch build and the
separate least-privilege GitHub Pages deploy job.
