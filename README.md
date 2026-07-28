# HOKF

HOKF is a small Hugo-based Open Knowledge Framework. It targets
[Open Knowledge Format v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
and publishes canonical Markdown as a searchable static site without a backend,
database, CMS, or application framework.

## Architecture

The repository keeps knowledge and presentation separate:

```text
okf/  ── safe YAML adapter ──>  site/content/  ── Hugo ──>  site/public/
  canonical source                disposable       HTML       Pagefind index
```

- `okf/` is the source of truth. It contains the reserved bundle `index.md` and
  `log.md` plus concept documents.
- `site/content/` is generated Hugo shadow content. It is ignored by Git and
  may be deleted and rebuilt at any time.
- `site/layouts/` and `site/assets/` are the thin, theme-free presentation
  layer.
- `.agents/skills/` contains focused local workflows for agents.

The adapter parses front matter with safe YAML loading, preserves nested and
producer-defined metadata, tolerates unknown types, and retains the canonical
type as Hugo's native content type. Every other producer-defined field is
namespaced under `params.okf` in the generated front matter (for example
`resource` becomes `params.okf.resource`), so it cannot acquire unintended
Hugo publishing semantics; templates read it back as `.Params.okf.<field>`.

## Quickstart

Prerequisites are Python 3, Hugo 0.163.3 extended, and Node.js 22.22.1. Create
an isolated Python environment and install the pinned dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --only-binary=:all: -r requirements.txt
npm ci --ignore-scripts
```

Validate and test the canonical bundle:

```sh
python tools/okf_validate.py --src okf
python -m unittest discover -s tests -v
```

Generate, build, and index the site:

```sh
python tools/okf_hugo_adapter.py --src okf --dst site/content --clean
hugo --source site --destination public --cleanDestinationDir
npm run pagefind
```

For local authoring, generate the shadow content and start Hugo:

```sh
python tools/okf_hugo_adapter.py --src okf --dst site/content --clean
hugo server --source site
```

A plain Hugo preview works before Pagefind runs; the search control reports that
the index is unavailable. Run the full build and `npm run pagefind` to exercise
search.

To verify GitHub Pages project-path links locally:

```sh
hugo --source site --destination public --cleanDestinationDir \
  --baseURL https://example.github.io/hokf/
```

## Validation behavior

The validator returns a non-zero status only for objective OKF v0.2 conformance
errors: invalid YAML, a missing or empty concept `type`, an invalid declared
`okf_version`, or invalid reserved documents. `okf_version` itself is optional.
It reports everything else as advisory warnings without failing, including
malformed optional metadata (`resource`, `tags`, `generated`, `verified`,
`sources`, `usage_window`, `status`, computation fields), broken links, stale
or orphaned concepts, inconsistent tags, missing recommended metadata,
duplicates, and legacy `timestamp`.

Use a deliberate quality gate when needed:

```sh
python tools/okf_validate.py --src okf --warnings-as-errors
```

## Repository Agent Skills

- `okf-author` creates and updates canonical OKF v0.2 concepts.
- `okf-curator` maintains indexes, logs, links, lifecycle, and bundle quality.
- `okf-hugo-site` builds and troubleshoots Hugo, Pagefind, and Pages.
- `okf-pr-review` reviews OKF, tooling, presentation, dependency, and workflow
  changes.

Validate the skills with the pinned reference CLI:

```sh
npx --yes skills-ref@0.1.5 validate .agents/skills/okf-author
npx --yes skills-ref@0.1.5 validate .agents/skills/okf-curator
npx --yes skills-ref@0.1.5 validate .agents/skills/okf-hugo-site
npx --yes skills-ref@0.1.5 validate .agents/skills/okf-pr-review
```

## Publishing

`.github/workflows/pages.yml` validates, tests, generates, builds, and indexes
on pull requests and default-branch pushes. Only a successful push to `main`
uploads and deploys the static artifact through a separate least-privilege
GitHub Pages job.
