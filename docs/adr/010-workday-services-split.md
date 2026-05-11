# ADR-010: Split Workday services into a package

**Status:** Accepted
**Date:** 2026-05-08

## Context

After ADR-009, the Workday-specific logic was still concentrated in
`cli.py` and a single `services/workday_employer.py` module:

- Employer config selection.
- Application Questions dispatcher (dropdown strategies + textarea
  helpers).
- Review-step validation (title/date/experience checks).
- "Required field empty" detection with false-positive filters.

`cli.py` was over 5800 lines and the Workday code was hard to unit-test
because the helpers were intertwined with Playwright-dependent
orchestration in the same file.

## Decision

Carve Workday support into a package, `job_hunt/services/workday/`, with
one module per concern:

- `employer_config.py` — `select_employer_config`, `choices_for_op`,
  `resolve_value`.
- `application_questions.py` — `run_question_ops`,
  `render_filled_message`. Dispatcher receives Playwright helpers via
  dependency injection so tests can mock them.
- `review_gate.py` — `ReviewIssue` data class +
  `detect_review_issues_from_text` / `detect_review_issues` /
  `review_validation_messages` / `review_needs_repair` /
  `issues_to_payload`; `ISSUE_*` code constants.
- `required_empty.py` — `is_workday_date_helper`,
  `filter_required_empty_fields`, `filter_non_blocking_workday_skips`,
  `dedupe_preserve_order`.

Backward compatibility:

- `job_hunt/services/workday_employer.py` becomes a re-export shim for
  the new package paths.
- `cli.py` keeps aliases for the previously-public names
  (`_workday_review_validation_issues`, `_workday_review_needs_repair`,
  `_filter_non_blocking_workday_skips`, `_filter_required_empty_fields`,
  `_dedupe_preserve_order`).

Tests follow the split: 55 new pure-Python / async-mocked tests; 0 of
them depend on a real Playwright session.

## Consequences

- Each Workday concern has a single home and a focused test file.
  Future Workday work has a clear place to land.
- Pure-Python coverage of the dispatcher / Review gate / required-empty
  filter is now feasible — these used to require a live browser.
- The compat shim + cli aliases are deliberate technical debt. A sunset
  rule should be written before the next major refactor so they do not
  live forever; for now they keep imports from breaking.
- Cosmetic bug fixed during the split: the greedy PDF-filename regex
  `[\w .,'()&+-]+\.pdf` concatenated space-separated filenames; replaced
  with `[\w.,'()&+-][\w .,'()&+-]*?\.pdf`.
- The `_filter_non_blocking_workday_skips` regex was also generalized:
  no more hardcoded "2026" / "3/2026"; it now uses a general
  `from*…current value is…to*…current value is…M/YYYY` pattern.
