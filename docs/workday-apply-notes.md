# Workday Apply Notes

This document captures generic implementation lessons for Workday application
forms. It intentionally avoids company-specific application history and local
candidate facts.

## Safety Boundary

The apply assistant may fill safe fields, upload selected files, and stop at the
review page. The final Submit button remains human-only unless a separate,
explicit two-key auto-submit gate is enabled for a supported flow.

## Login And Session

- Use the normal `job-hunt apply --fill-only` flow for visible browser sessions.
- Login state persists in `storage/browser-profile/`, which is local and ignored.
- If the user logs in or advances to another ATS step, prefer
  `job-hunt apply-refill-current-page` to reuse the active browser session.
- Do not connect external Playwright sessions through CDP to a running fill-only
  browser; that can close the shared browser process when the external session
  exits.

## Buttons

Workday often renders an invisible or overlay `role="button"` control above the
visible `<button>`. Use role/aria-label-safe click helpers that search
`[role="button"], button, input[type="submit"]` instead of assuming that
Playwright's visible button click will land on the actionable element.

## Date Inputs

Some Workday `MM/DD/YYYY` fields are split into Month, Day, and Year spinbutton
segments. Fill the segment controls directly when available:

- `[data-automation-id="dateSectionMonth-input"]`
- `[data-automation-id="dateSectionDay-input"]`
- `[data-automation-id="dateSectionYear-input"]`

Do not assume a Workday date field is a single normal text input.

## File Uploads

- Resume upload can report success even after the visible file input disappears.
- Transcript uploads are optional and should come from either
  `profile/profile.yml` (`candidate.transcript_pdf` or `cowork.transcript_pdf`)
  or `storage/private/workday-transcript.*`.
- Do not upload a resume as a transcript.
- Avoid re-uploading a file when the same filename is already visible on the
  page.

## Employer-Specific Questions

Employer-specific Workday answers belong in YAML under
`profile/workday-employers/`. Use `_default.yml` for safe defaults, and add a
company slug file only when a form needs targeted question handling.

Legal, sponsorship, relocation, compensation, demographic, and ambiguous
required questions require user confirmation before filling.
