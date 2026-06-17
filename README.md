# HOKF

HOKF is a Hugo-based Open Knowledge Framework. It keeps knowledge in a
canonical Open Knowledge Format (OKF) tree and uses Hugo as a thin rendering
shell for generated views.

## Architecture

HOKF is built around three boundaries:

- `okf/` is the canonical source tree. Authors and agents should treat this as
  the source of truth for concepts, logs, and other structured knowledge.
- `site/` is the Hugo shadow. Its `content/` tree is derived from `okf/` by
  tooling and should not become the primary editing surface.
- `.agents/skills/` is the repository-local Agent Skills area for workflows
  that help maintain, transform, or publish the knowledge base.

The intended flow is OKF-first: write and maintain knowledge in `okf/`, then
generate Hugo-compatible content and assets into `site/` when adapter tooling is
introduced. This keeps the knowledge model independent from the presentation
layer while still allowing Hugo to provide fast static rendering.

## Repository Layout

```text
okf/                 Canonical Open Knowledge Format source
site/                Thin Hugo site shell for rendered/generated views
tools/               Minimal glue code, including future OKF-to-Hugo adapters
.agents/skills/      Repository-local Agent Skills
.github/workflows/   Future CI workflow definitions
```

## Design Principles

HOKF should stay small and predictable:

- Keep custom code minimal.
- Prefer generated Hugo content over hand-maintained `site/content/` files.
- Avoid adding runtime services, databases, custom CMS layers, vector databases,
  or heavy theme forks.
- Add tooling only when it supports the OKF-to-Hugo path or repository
  maintenance workflows.
