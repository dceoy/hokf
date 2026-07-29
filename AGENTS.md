# AGENTS.md

HOKF is an OKF-first, fully static knowledge framework. Keep changes scoped,
preserve Open Knowledge Format v0.2 semantics, and avoid runtime complexity.

## Repository structure

```text
okf/
  index.md                 Reserved bundle index; declares OKF v0.2
  log.md                   Reserved bundle update history
  concepts/                Canonical concept documents
site/
  hugo.toml                Hugo configuration
  content/                 Ignored, disposable adapter output
  layouts/                 Thin Hugo templates and link render hook
  assets/                  Framework-free source assets
tools/
  okf_common.py            Shared safe YAML parser
  okf_hugo_adapter.py      Reads okf/ and writes site/content/
  okf_validate.py          Conformance and advisory validator
tests/                     Python unit tests
.agents/skills/            Repository-local Agent Skills
.github/workflows/pages.yml
                            Static validation, build, index, and deploy
```

## Source-of-truth rules

- Edit knowledge only under `okf/`.
- Never hand-edit or commit `site/content/`; regenerate it from `okf/`.
- Never commit `site/public/` or `node_modules/`.
- Preserve unknown concept types, unknown keys, and nested metadata.
- Keep Hugo and Pagefind as consumers of OKF rather than a second knowledge
  model.

## Add or update a concept

1. Search `okf/` for an existing path, title, or equivalent concept.
2. Create or edit a non-reserved `.md` file. Its bundle-relative path without
   `.md` is its concept ID.
3. Add YAML front matter with a non-empty `type`. This is the only
   always-required concept field.
4. Add useful recommended `title`, `description`, `resource`, and `tags`
   metadata. Use lowercase kebab-case tags.
5. Add optional `generated`, `verified`, `sources`, `status`, and `stale_after`
   only when the values are known. Use `generated`, never a new legacy
   `timestamp`.
6. Use `human:<id>`, `process:<id>`, or `<producer>/<version>` actors and ISO
   8601 event datetimes.
7. Use standard relative or bundle-root-relative `.md` links.
8. Add a concise entry to the nearest `index.md` and record a meaningful
   bundle-level change in `log.md`.

## Maintain reserved files

- `index.md` uses headings and Markdown link lists for progressive disclosure.
  It has no front matter except at the bundle root, where the only key is
  `okf_version: "0.2"`.
- `log.md` has no front matter. Use `## YYYY-MM-DD` headings ordered newest
  first with prose list entries beneath them.
- Do not use either reserved filename for a concept or recreate a `logs/log.md`
  subtree for bundle history.

## Commands

Set up dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --only-binary=:all: -r requirements.txt
npm ci --ignore-scripts
```

Validate and test:

```sh
python tools/okf_validate.py --src okf
python -m unittest discover -s tests -v
```

Generate and build:

```sh
python tools/okf_hugo_adapter.py --src okf --dst site/content --clean
hugo --source site --destination public --cleanDestinationDir
npm run pagefind
```

Preview:

```sh
hugo server --source site
```

Test project-path links:

```sh
hugo --source site --destination public --cleanDestinationDir \
  --baseURL https://example.github.io/hokf/
```

Validate repository skills:

```sh
npx --yes skills-ref@0.1.5 validate .agents/skills/okf-author
npx --yes skills-ref@0.1.5 validate .agents/skills/okf-curator
npx --yes skills-ref@0.1.5 validate .agents/skills/okf-hugo-site
npx --yes skills-ref@0.1.5 validate .agents/skills/okf-pr-review
```

## Validation policy

`ERROR` findings are blocking format violations. Fix them before generation or
publishing. `WARNING` findings are advisory quality concerns and do not make an
OKF v0.2 bundle nonconformant. Review warnings introduced by a change; use
`--warnings-as-errors` only when an explicit quality gate is requested.

## Local skills

- Use `okf-author` for creating or updating concepts.
- Use `okf-curator` for index, log, freshness, link, tag, orphan, and duplicate
  maintenance.
- Use `okf-hugo-site` for adapter, Hugo, Pagefind, and Pages work.
- Use `okf-pr-review` for reviews spanning OKF and the static toolchain.

## Non-goals

Do not add a backend, database, custom CMS, vector database, server-side search,
heavy Hugo theme, JavaScript application framework, schema registry, or agent
orchestration runtime.
