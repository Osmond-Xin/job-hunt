# Architecture Decision Records

One-page records for non-obvious architectural decisions in job-hunt. Each
ADR captures the context, the decision, and its consequences so the
reasoning survives without depending on commit messages or agent memory.

Numbering is historical: ADR-001..008 predate this directory and were
captured implicitly in `docs/design.md` and commit history rather than as
standalone files. ADR-009 is the first written record.

## Index

- [ADR-009: Config-driven Workday employers](009-workday-config-driven.md)
- [ADR-010: Split Workday services into a package](010-workday-services-split.md)
- [ADR-011: Structured Workday Review-gate issues](011-workday-review-issues-structured.md)
- [ADR-012: apply-review.json schema + apply-run.jsonl + heartbeat IPC](012-apply-review-schema-and-event-log.md)
- [ADR-013: Quality-audit regeneration loop + reasoning-model token budget](013-quality-audit-loop-and-reasoning-budget.md) (amended 2026-07-27)
- [ADR-014: Red-team review gate on every outward-facing artifact](014-redteam-gate-on-outward-artifacts.md)
- [ADR-015: Quota-free direct board adapters (discovery tiers 4-7)](015-quota-free-discovery-tiers.md)
- [ADR-016: Deny-by-default `data/` in `.gitignore`](016-deny-by-default-data-gitignore.md)

## Format

Each ADR follows the same short shape:

```
# ADR-NNN: Title

**Status:** Accepted | Superseded | Deprecated
**Date:** YYYY-MM-DD

## Context
Why was this decision needed?

## Decision
What did we do?

## Consequences
What follows from this — both the wins and the costs?
```

Keep them tight. If an ADR needs more than a page, the proposal probably
needs to be split.
