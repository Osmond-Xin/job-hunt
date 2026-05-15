"""LinkedIn-specific helpers for the apply flow.

Mirrors the layout of ``services/workday/``:

- :mod:`detect`         — URL / DOM probes (``is_linkedin_job_url``, ``is_easy_apply_modal_open``).
- :mod:`fields`         — pure field strategy helpers (label normalisation,
  Yes/No radio mapping, country-code best-match).
- :mod:`easy_apply`     — async multi-step Easy Apply driver (mirrors
  ``services.workday`` advance flow). Stops at the Review step unless an
  auto-submit gate is explicitly passed.

The Playwright surface stays in :mod:`cli`; this package keeps logic
unit-testable with ``AsyncMock`` doubles.
"""
