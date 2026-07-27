---
type: Architecture
title: HOKF architecture
description: HOKF keeps Open Knowledge Format source canonical and uses Hugo as a static presentation layer.
tags:
  - architecture
  - okf
  - hugo
status: stable
generated:
  by: human:hokf-maintainer
  at: 2026-07-27T09:00:00Z
verified:
  - by: human:hokf-maintainer
    at: 2026-07-27T10:00:00Z
sources:
  - id: okf-spec
    resource: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
    title: Open Knowledge Format v0.2 specification
    author: team:google-cloud
    last_modified: 2026-07-25
stale_after: 2027-01-27
audience:
  - maintainers
  - coding-agents
---
# Architecture

HOKF has two deliberately separate layers:

1. [`okf/`](/index.md) is the canonical knowledge bundle.
2. The [Hugo shadow](hugo-shadow-content.md) is generated from that bundle and rendered as a static site.

The adapter reads canonical Markdown, preserves structured metadata and unknown
producer extensions, then maps OKF `type` into Hugo-specific metadata. Hugo and
Pagefind remain consumers of the knowledge rather than alternate sources of
truth.

This separation lets [OKF-first authoring](okf-first.md) evolve independently
from the site presentation.[^okf-spec]

[^okf-spec]: Open Knowledge Format v0.2 specification
