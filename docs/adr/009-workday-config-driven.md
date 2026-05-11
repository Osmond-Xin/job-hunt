# ADR-009: Config-driven Workday employers

**Status:** Accepted
**Date:** 2026-05-08

## Context

Each new Workday employer ("QuadReal", "Telus", ...) was forcing a small
patch to `cli.py`: a new question literal, a new dropdown answer, a new
date helper. The pattern was "every new employer = one small Workday
issue", which scaled badly and bled Workday-specific knowledge into the
generic apply orchestrator.

Two specific surfaces drove the cost:

- Per-employer **Application Questions** (legal work permission, prior
  employment, voluntary disclosures) varied just enough that hardcoded
  branches accumulated.
- Per-operator **work experience and education entries** that fill the
  "My Experience" step also drifted (CV updates, new education entries)
  and had no canonical source.

## Decision

Move both surfaces into version-controlled YAML, loaded at runtime.

- `profile/cv-experience.yml` (gitignored) holds the operator's work
  experience + education entries. Loaded by
  `job_hunt/services/profile_loader.py::workday_experience_entries()` /
  `workday_education_entries(values)`.
- `profile/workday-employers/<slug>.yml` (gitignored) holds per-employer
  Application Questions, selected by
  `job_hunt/services/workday/employer_config.py::select_employer_config`.
- Selection is by substring (`detect.url_contains`, str or list).
  Lookup priority: matching yaml → `_default.yml` → embedded fallback.
- Each `ops:` entry is `kind=dropdown|text|date`. Dropdowns carry a
  `strategies: [{type, label/index}]` ladder of
  `by_label` / `in_question` / `containing_label` / `by_index`.
  `choices_by` branches on `values[key]` (e.g. eligibility A/B);
  `value_from` pulls from the values dict.
- Free-form textareas reuse `_answer_for_application_question`
  (saved-answer fuzzy match → report-draft → canned). Q&A pairs persist
  to `apply-review.json::answers` for next-run reuse.

## Consequences

- Onboarding a new Workday employer is a yaml file, not a code change.
  Procedure documented in the project status memo.
- The `_default.yml` covers universal questions (legal work permission,
  student status) so most employers need only a thin override.
- Two new private data surfaces (`cv-experience.yml`, employers
  directory) must stay gitignored. Both are.
- Strategy ladders + `choices_by` are a small DSL. Worth the cost vs
  hardcoded branches; revisit only if the DSL itself starts collecting
  hardcoded special cases.
