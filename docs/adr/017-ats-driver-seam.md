# ADR-017: One driver contract for every ATS

**Status:** Accepted
**Date:** 2026-09-09

## Context

ADR-010 carved four Workday concerns out of `cli.py` into
`job_hunt/services/workday/` so they could be unit-tested, and it worked: 55
tests, none needing Playwright. It stopped short of the step machine itself.
Four months later `cli/apply.py` was 5,197 lines — 19% of the package — of which
only 530 were Typer commands.

The layer check could not see the problem. `cli/` is the top layer, so it may
import anything, and an AST walk of all 119 modules found zero inversions. The
defect was that the logic *lived* in `cli/` rather than being called from it.

Its concrete form: `services/linkedin/easy_apply.py` returned a structured
result the CLI consumed in ten lines, while the Workday path had no such thing,
so `_open_apply_page` hand-rolled that flow inline over ~120 lines and its
auto-submit gate over ~60 more. `"myworkdayjobs.com" in page.url` — the entire
ATS-detection mechanism — appeared thirteen times.

Two auto-submit gates existed, one per ATS. They could disagree, and only one
was tested. What sits on the far side of that gate is a real application, under
a real name, to a real employer.

## Decision

One protocol, `services/web/ats_contract.AtsDriver`, with **`fill` and `submit`
as separate calls**. The session holds the policy — whether to submit — and the
driver holds the DOM knowledge — which button. A driver that could submit inside
`fill` would own the gate, once per ATS, which is the thing being fixed.

- `submit()` returns a three-valued `SubmitOutcome`, not a bool. An ATS can
  accept the click while the confirmation navigation times out; calling that
  failure invites a duplicate application, calling it success records one that
  may not exist. It is `unknown`, persisted to the run log, and a later run
  refuses to click until a human resolves it.
- Detection is two-tier: `matches_url` (pure, pre-navigation, a hint) and
  `matches_page` (post-navigation, authoritative), because employer vanity
  domains redirect into ATS tenants.
- `AtsResult` carries only what both ATSs have. Workday's `ReviewIssue` maps to
  an ATS-neutral `Blocker` at the driver's edge rather than entering the shared
  contract.
- Contract, drivers and registry are three modules: one module defining the
  protocol *and* importing its implementers is a cycle.
- The session takes a `Reporter`; `cli/` supplies a Rich-backed one. No
  `console.print` remains anywhere in `services/`.

Layout: `services/apply/` (report reading, answer lookup, linking),
`services/web/` (`form_fill`, `apply_session`, `ats_*`, `submit_gate`,
`reporter`), `services/workday/` (`steps`, `detect`, `step_decisions`,
`driver`), `services/linkedin/` (`page_helpers`, `driver`).
`cli/apply.py` ends at 709 lines: ten commands, three helpers that print and
exit, and imports.

## Consequences

- Adding a third ATS is a module and one registry entry. Tested by a driver that
  exists only in `tests/test_ats_registry.py`.
- The auto-submit gate is one pure function, asserted on effects rather than log
  lines — a regression can emit the right event and click anyway.
- Workday step *decisions* are testable without a browser, but only where there
  is a decision worth testing. A spike (1,773 differential cases, zero
  mismatches) proved the split on `_workday_current_step`; the survey it
  licensed found 25 functions that write to the DOM and do not fit, and most of
  the rest already delegating to ADR-010's modules. That is the honest ceiling.
- **The alias sunset rule ADR-010 asked for** ("A sunset rule should be written
  before the next major refactor so they do not live forever"):

  > A compatibility alias exists to keep a **test** importing during one
  > refactor. It is deleted in the same PR that updates the last test importing
  > it. No alias survives the PR that created it. An alias is never created for a
  > name a test monkeypatches — patching an alias silently disarms the mock.

  This refactor created none. That is what made five stale patch targets fail
  loudly rather than sit inert.
- `cli/__init__.py` re-exports names from their new owners, so
  `from job_hunt.cli import X` still resolves and points at the module that
  defines X.
- Cost: `ruff` had to become a usable gate first (268 findings, 239 of them
  deliberate re-exports). It then caught two undefined names in moved code that
  the test suite did not reach.
- Not done here: mypy. A type-annotation sweep inside a 4,800-line move makes the
  diff unreviewable. Separate proposal.
