---
type: Authoring Principle
title: OKF-first authoring
description: Contributors edit canonical concepts under okf and regenerate presentation output afterward.
tags:
  - authoring
  - okf
status: stable
generated:
  by: human:hokf-maintainer
  at: 2026-07-27T09:15:00Z
---
# OKF-first authoring

Create and update concepts under `okf/`. A concept needs only a non-empty
`type`, though a useful concept normally includes a title, description, tags,
and `generated` provenance.

Keep [`index.md`](/index.md) concise so agents can discover concepts without
loading the whole bundle. Record bundle-level changes in [`log.md`](/log.md)
under ISO date headings, newest first. These filenames are reserved and are not
concept documents.

After authoring, validate the bundle and regenerate the
[Hugo shadow](hugo-shadow-content.md). Never make a knowledge change only in
`site/content/`.
