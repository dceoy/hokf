---
type: Agent Interface
title: Repository Agent Skills
description: Four local skills guide repeatable authoring, curation, site, and review workflows.
tags:
  - agents
  - workflows
generated:
  by: human:hokf-maintainer
  at: 2026-07-27T09:45:00Z
extension:
  preferred_interface: progressive-disclosure
---

# Repository Agent Skills

Repository-local skills under `.agents/skills/` give coding agents focused
instructions for:

- authoring OKF v0.2 concepts;
- curating indexes, logs, links, and lifecycle metadata;
- generating and troubleshooting the [Hugo shadow](hugo-shadow-content.md); and
- reviewing changes against the [OKF-first boundary](okf-first.md).

The skills call the same checked-in validator, adapter, Hugo, and Pagefind
commands used by contributors and CI. They do not add orchestration software or
a second plugin framework.
