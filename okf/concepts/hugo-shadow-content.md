---
type: Build Workflow
title: Hugo shadow content
description: The adapter generates disposable Hugo content from the canonical OKF bundle for static rendering.
tags:
  - hugo
  - generation
  - static-site
status: stable
generated:
  by: process:okf-hugo-adapter
  at: 2026-07-27T09:30:00Z
sources:
  - resource: ../concepts/architecture.md
    title: HOKF architecture
stale_after: 2027-01-27
---
# Hugo shadow content

`site/content/` is untracked, disposable output. Generate it with:

```sh
python3 tools/okf_hugo_adapter.py --src okf --dst site/content --clean
```

The adapter retains nested Open Knowledge Format v0.2 metadata, producer-defined
extensions, and unknown concept types. It adds presentation metadata that lets
Hugo render every concept through the same small template set.

The [architecture](architecture.md) keeps generation one-way. Pagefind indexes
the resulting HTML, so neither Hugo nor search introduces a runtime service.
