# AGENTS.md

This repository is the foundation for HOKF, the Hugo-based Open Knowledge
Framework. AI coding agents should keep changes small, respect the OKF-first
architecture, and avoid adding runtime complexity unless a later issue
explicitly asks for it.

## Project Structure

```text
okf/
  index.md           Canonical OKF entry point
  concepts/          Canonical concept notes
  logs/              Canonical knowledge logs
site/
  hugo.toml          Minimal Hugo configuration
  content/           Generated Hugo content, not canonical source
  layouts/           Hugo layout shell
  assets/            Hugo asset shell
  static/            Hugo static file shell
tools/               Minimal glue code for future adapters
.agents/skills/      Repository-local Agent Skills
.github/workflows/   Future CI workflows
```

## Source-of-Truth Rules

- Treat `okf/` as the canonical source of knowledge.
- Treat `site/content/` as derived output from future OKF-to-Hugo tooling.
- Do not move canonical knowledge into Hugo content files.
- Keep generated or rendered views separate from source knowledge.
- Keep `.agents/skills/` for reusable agent workflows that are local to this
  repository.

## Expected Local Commands

These commands define the expected workflow surface, even while some are
placeholders until later issues add implementation details:

```sh
# Inspect repository state.
git status --short

# Validate Markdown when a local formatter or linter is available.
# Example future command:
# markdownlint README.md AGENTS.md okf/**/*.md

# Run the Hugo site once layouts and generated content exist.
# Example future command:
# hugo --source site

# Run future OKF-to-Hugo adapter tooling.
# Example future command:
# ./tools/okf-to-hugo
```

## Contribution Conventions

- Keep changes scoped to the requested issue.
- Prefer documentation and placeholders over premature implementation in the
  foundation stage.
- Do not add dependencies unless they are necessary for the issue being solved.
- Preserve the distinction between canonical OKF source and generated Hugo
  output.
- If adding tooling later, make it explicit whether it reads from `okf/`, writes
  to `site/`, or does both.
- Run available checks before finishing. If no checks exist, verify the file tree
  and Markdown structure manually.

## Non-Goals

Do not add these unless a later issue explicitly changes the project scope:

- Backend runtime
- Database
- Custom CMS
- Vector database
- Heavy Hugo theme fork
- Full OKF-to-Hugo adapter
- Hugo layout implementation
- Validation framework
- Search implementation
