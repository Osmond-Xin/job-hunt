# Job Hunt Agent Guide

This repository is a personal job-search operations system. Future agents should preserve the human-in-the-loop safety model and keep changes small, testable, and auditable.

## Current Stable Workflow

The main closed loop is:

1. discover or choose a job
2. evaluate the job and generate report/PDF artifacts
3. use an agent-assisted browser flow to fill the application
4. user manually reviews and submits
5. CLI records tracker/event/activity
6. Slack receives a safe notification
7. Gmail polling/reconcile can later ingest employer responses

The application submit button is never clicked by automation.

For a complete executable runbook that another agent can follow end to end, read `docs/full-loop-execution.md`.

## Before You Finish: `job-hunt checkup`

**Run this at the end of every session that produced or sent anything.**

```bash
.venv/bin/job-hunt checkup            # --strict to exit non-zero when work is outstanding
```

Building a résumé and submitting it are two steps; writing the tracker row is a
third, and nothing forces it. `checkup` looks for the evidence that the third
step was skipped:

- **artifacts without a tracker row** — a directory under `output/` holding a
  rendered PDF that no tracker row accounts for. This is the check that would
  have caught the 2026-08-19/20 batch, where thirty-five applications were
  built, sent, and never recorded.
- **event log readable** — see the inbound-mail section below.
- **mailbox agrees with the tracker** — acknowledgements with no row, rows whose
  mail says they are closed, and rows an interview invitation has moved past.
- **outreach follow-ups** — what is due to be chased.

Every check is read-only and prints the command that fixes what it found.

`apply --confirmed --pdf <file under output/>` writes a `.tracker-row` marker
into that directory recording the row number. Once a directory has a marker the
check is exact rather than a guess at the employer's name, so **always pass
`--pdf` when recording** — a slug like `ccl-…` shares no word with "Connor,
Clark & Lunn Financial Group" and will otherwise be reported as unrecorded.

When a check reports something you have confirmed is not a real gap — a brand
name, a hiring platform, a parent company — add the pair to
`config/company-aliases.yml` rather than ignoring the output. A check nobody
trusts is a check nobody runs.

## Inbound Mail: Two Tracks, and How They Fail

Step 7 above has two separate readers of the same mailbox. Know which one you
are using — they do not share data.

| | `email poll` → `data/email-events.jsonl` | `email summarize` → `data/email-summaries.jsonl` |
|---|---|---|
| classifier | regex rules | MiniMax |
| accuracy | noisy; company often `?` | good |
| feeds | `email reconcile`, `review-candidates`, `approve-event` | nothing automatic |

`summarize` output is deliberately **not** wired to auto-update the tracker: an
email body is untrusted input (`docs/design-notes.md`, P1). Use
`email gaps` to read it — a report of applications the mailbox has
acknowledged that have no tracker row, plus rows still marked `Applied` whose
mail says they are closed. It mutates nothing; you record what it finds.

```bash
.venv/bin/job-hunt email summarize --since 21d --concurrency 3 --live  # keep concurrency <= 3
.venv/bin/job-hunt email gaps --since 2026-08-01
.venv/bin/job-hunt email verify     # is the event log readable at all?
```

**The failure that cost 19 days (2026-08-10 → 2026-08-29).** A hand-written row
in `email-events.jsonl` used `source: "email"` and
`event_type: "application_acknowledged"` — neither is in the schema
(`job_hunt/models/events.py`). The reader validated every row eagerly, so that
one line raised a raw pydantic traceback out of *every* inbound command: `poll`,
`events`, `reconcile`, `review-candidates`, `approve-event`, `ignore-event`.
The whole inbound half of the system was dead and nothing said so. Meanwhile
`summarize` kept working, because it does not touch the event log — so the
mailbox looked fine while ~30 applications and ~19 rejections went unrecorded.

The reader now skips unreadable rows instead of dying, warns when it did, and
`email verify` prints the offending line numbers. **Do not hand-edit
`email-events.jsonl`.** If you must, run `email verify` afterwards.

## Outbound Mail Is Invisible To This System

The polled mailbox is a Gmail account with **zero sent messages** — the user
replies to employers from a different mailbox. So no recruiter reply the user
sends will ever appear in `email poll`, `email summarize` or `email gaps`, and
an application can sit at `Applied` in the tracker while a conversation is
already several messages deep. Nothing detects this; you have to be told.

When the user says they replied to someone, record it:

```bash
.venv/bin/job-hunt contacts add --company '<employer>' --name '<person>' \
    --email '<addr>' --relationship recruiter --source email
.venv/bin/job-hunt outreach log <contact-id> --role '<role>' --application-id <row> \
    --channel email --follow-up-at YYYY-MM-DD --notes 'what was sent'
.venv/bin/job-hunt outreach due    # what needs chasing
```

`outreach draft` also creates an event, but it calls the LLM to write a message
first — no use for a reply that has already gone out. Prefer `outreach log`.

## Recording Work Done Outside the Pipeline

If the user submitted an application by hand, do not append to
`data/applications.md` yourself — that skips the event log, the activity log and
the Slack notification, and it is how rows drift out of sync:

```bash
.venv/bin/job-hunt apply '<application-url>' --company '<company>' --role '<role>' \
    --pdf '<resume.pdf>' --no-browser --confirmed
```

`url` is only required to open a browser. When the application has no URL —
something found while browsing LinkedIn, or a referral into a role with no
posting to link — omit it and pass `--no-browser`; leaving `--no-browser` off
with no URL fails with a message naming the reason instead of silently
skipping the browser:

```bash
.venv/bin/job-hunt apply --company '<company>' --role '<role>' \
    --pdf '<resume.pdf>' --no-browser --confirmed
```

Pass `--pdf` even when the form took the file by hand: it is what links the
`output/` directory to the row, and `job-hunt checkup` relies on that link.
Then write the real detail into the row's notes — verdicts, dates, stated
timelines, what must not be contradicted at interview. The command can only
write a generic note; the row is only as useful as what you add to it.

Finish with `job-hunt checkup` and `job-hunt tracker verify`.

## Important Commands

```bash
.venv/bin/job-hunt config doctor
.venv/bin/job-hunt evaluate '<job-url-or-text>'

# Bulk: evaluate a list of URLs, one summary table
.venv/bin/job-hunt evaluate-batch urls.txt --concurrency 3

# Minimal entry: user only gives URL
.venv/bin/job-hunt loop '<job-or-application-url>'

# Step 1: open browser, fill form, keep open for user review
.venv/bin/job-hunt apply '<application-url>' --company '<company>' --role '<role>' --pdf '<resume.pdf>' --fill-only

# Step 2: replace PDF while browser is still open
.venv/bin/job-hunt apply-replace-pdf '<new-resume.pdf>'

# Re-run fill/upload on the current page after the user logs in or advances a step
.venv/bin/job-hunt apply-refill-current-page

# Gracefully close the visible browser and save login/session cookies
.venv/bin/job-hunt apply-close-session

# Step 3: after user clicks Submit, capture confirmation evidence
.venv/bin/job-hunt apply-capture-page

# Step 4: after user confirms submit, record the application
.venv/bin/job-hunt apply '<application-url>' --company '<company>' --role '<role>' --pdf '<resume.pdf>' --no-browser --confirmed

# Step 5: before you finish — what got done but never recorded?
.venv/bin/job-hunt checkup
.venv/bin/job-hunt tracker verify

.venv/bin/job-hunt activity slack-test
.venv/bin/job-hunt activity list --since 1d
.venv/bin/pytest
```

Use `.venv/bin/job-hunt ...` instead of assuming `job-hunt` is on PATH.

## Agent Apply Loop (Claude Code)

```
1. Run apply --fill-only in background → browser opens, auto-fills, takes screenshot
2. Read screenshot to verify all fields
3. Report to user: filled fields, skipped fields, PDF filename shown
4. User reviews visible browser; asks for changes if needed
5. To swap PDF: run apply-replace-pdf <new-pdf> → browser updates in ~2s, new screenshot taken
6. If the user logs in or advances to a new ATS step, run apply-refill-current-page instead of restarting the browser
7. User clicks Submit Application in browser
8. User tells agent "submitted"
9. Run apply-capture-page → captures thank-you/current page
10. Run apply --no-browser --confirmed --pdf <pdf> → records tracker, activity log, Slack
11. Write the real detail into the row's notes (verdicts, dates, gaps named)
12. Run job-hunt checkup → confirms nothing was left unrecorded
```

Steps 10-12 are the ones that get skipped, and nothing else notices. When the
materials were hand-built with no browser session at all, step 10 is still how
they get recorded — there is no separate path for hand-built work.

Never click the Submit button yourself. Never record before user confirms.

## Application Safety Rules

- Do not click the final submit/apply button.
- Do not answer legal, sponsorship, relocation, compensation, demographic, or ambiguous required questions without user confirmation.
- Current user preference: fill all required fields and configured consent fields first, then stop at final Submit and summarize for review. The Workday terms/conditions consent is explicitly authorized by `profile/profile.yml` → `workday.consent_terms_and_conditions: true`.
- Do not invent candidate facts. Read `profile/profile.yml`, `profile/cv.md`, selected reports, and selected PDFs.
- Do not fill ATS honeypot fields. In particular, Workday can show a visible `Website` input with text such as "for robots only" / "do not enter if you're human"; filling it can make account creation silently fail.
- Do not print secrets from `.env`, Slack webhooks, OAuth credentials, cookies, or browser storage.
- After the user logs in, first prefer `apply-refill-current-page` so the active session is reused. If the running process is old/stuck or lacks refill support, do not wait blindly; restart quickly and accept the possible relogin tradeoff.
- Screenshots go under `artifacts/apply/{date}-{company}-{role}/`. PDFs go under `output/`.
- Record an application as `Applied` only after the user explicitly says they manually submitted it.

## Stable Apply Implementation

- `job_hunt/cli/apply.py::apply_assist` — the `job-hunt apply` command. `url` is
  an optional positional argument: required to open a browser, not required to
  record. Omitting it without `--no-browser` fails fast with an explanatory
  message rather than opening a browser with nothing to navigate to. Key flags:
  - `--fill-only`: opens visible browser, fills form, keeps open with heartbeat-driven loop. Idle timeout is 60 minutes (no command/refill); not a hard 30-min deadline anymore.
  - `--confirmed`: skips browser and confirmation, records directly as Applied
  - `--no-browser`: skips Playwright, prompts for confirmation via stdin; also what allows `url` to be omitted (e.g. a LinkedIn-browsed or referral application with no URL)
  - `--headless`: runs Chromium headless (smoke tests only)
- `apply-replace-pdf` / `apply-capture-page` / `apply-refill-current-page` / `apply-close-session` write per-command `.cmd-<uuid>.json` sentinels (race-free). Each subcommand warns when the heartbeat is >30s stale ("session likely dead, restart with apply --fill-only").
- `apply-status [--controls]` / `apply-do --click/--fill/--select/--check` are request/response commands: sentinel in, `.res-<uuid>.json` out (30 s poll). They are the token-cheap replacement for reading screenshots or driving a browser MCP; `apply-do` rejects submit-like click labels at both ends.
- Verification order for agents: `apply-review.json` → `apply-status` → screenshot image only when the JSON shows a problem. Never drive application pages through a browser MCP.
- `job_hunt/cli/apply.py::_open_apply_page` — opens Playwright with a persistent browser profile (`storage/browser-profile/`), navigates to the URL, runs auto-fill, then attaches the PDF.
- `job_hunt/cli/apply.py::_attach_resume` — attaches PDF using file chooser (clicking the exact-name "Upload File"/"Replace" button) before falling back to `set_input_files`. Auto-fill runs before PDF attachment so React components are fully initialized.
- `_record_manual_submission` is the only helper that mutates tracker state for manual submissions.
- Application events use source `system_apply` and activity type `apply.submitted`.
- Slack notifications are emitted through `ActivityLogger`; do not call Slack directly from apply code.

## Workday Module Map

Workday helpers were extracted from `cli.py` into a service package; `cli.py` keeps thin re-export aliases (e.g. `_workday_review_validation_issues`) so existing call sites stay valid.

- `job_hunt/services/profile_loader.py` — `workday_experience_entries()` / `workday_education_entries(values)` load from `profile/cv-experience.yml` (gitignored). Yaml accepts `start: "YYYY-MM"` shortcut or split `start_year`/`start_month`. Falls back to embedded defaults if yaml absent.
- `job_hunt/services/workday/employer_config.py` — `select_employer_config(url)` picks `profile/workday-employers/<slug>.yml` by `detect.url_contains` substring. Priority: matching yaml → `_default.yml` → embedded generic fallback.
- `job_hunt/services/workday/application_questions.py` — `run_question_ops(page, values, ops, *, by_label, in_question, containing_label, by_index, fill_text, fill_date, short)` is a yaml-driven dispatcher with injected Playwright helpers; `render_filled_message(op, kind)` formats stable "Workday question:" / "Workday question field:" lines.
- `job_hunt/services/workday/review_gate.py` — `ReviewIssue(code, message, details)` data class + `detect_review_issues_from_text` (pure-text) + async `detect_review_issues` / `review_validation_messages` / `review_needs_repair` / `issues_to_payload`. Issue codes: `WD_REVIEW_EXPERIENCE_MISSING`, `WD_REVIEW_TITLE_MISMATCH`, `WD_REVIEW_DATE_MISMATCH`, `WD_REVIEW_ROLE_DESCRIPTION_MISSING`, `WD_REVIEW_GPA_MISMATCH`, `WD_REVIEW_LINKEDIN_INVALID`, `WD_REVIEW_DUPLICATE_UPLOAD`.
- `job_hunt/services/workday/required_empty.py` — `is_workday_date_helper(label)`, `filter_required_empty_fields`, `filter_non_blocking_workday_skips`, `dedupe_preserve_order`. Date-helper detection is generic (`from*…current value is…to*…current value is…M/YYYY`).
- `job_hunt/services/web/apply_ipc.py` — heartbeat + per-command sentinel + idle timeout primitives.
- `job_hunt/services/web/apply_run_log.py` — `apply-run.jsonl` event emit/read.

Onboarding a new Workday employer is a yaml edit (drop `profile/workday-employers/<slug>.yml`), not a code change.

## Artifact Layout

Each apply session writes to a per-application directory:

```
artifacts/apply/{YYYY-MM-DD}-{company-slug}-{role-slug}/
  apply-review-{hash}.jpg    ← screenshot taken after fill (and after each PDF replacement)
  apply-page-{hash}.jpg      ← screenshot taken after manual submit / capture request
  apply-review.md            ← review log and drafted answers
  apply-review.json          ← machine-readable review state, incl. validation_issues[]
  apply-controls.json        ← form-control summary, written when fill left problems
  apply-run.jsonl            ← structured event log (one JSON per line)
  {resume-filename}.pdf      ← copy of the PDF submitted
  .cdp                       ← compatibility sentinel present while fill-only session is active
  .session.json              ← heartbeat (pid + last_heartbeat)
  .cmd-<uuid>.json           ← per-command sentinel (transient)
  .res-<uuid>.json           ← per-command response (transient)
  login-modal-unknown.png    ← only present when login modal misbehaves
  login-modal-unknown.html   ← DOM dump matching the screenshot above
```

## Browser Profile

`storage/browser-profile/` is a persistent Chromium user-data directory. Login sessions (LinkedIn, etc.) are preserved between apply runs. Stale `SingletonLock` files are cleared automatically at the start of each `_open_apply_page` call. Do not commit this directory.

## Tracker Fuzzy Match

`TrackerRepository.find_match` uses `fuzz.token_sort_ratio` for role similarity. When the company is an exact match, the role similarity must be ≥ 0.85 to count as the same row — preventing two different roles at the same company from colliding.

## Resume/PDF Link Contract

Generated CV HTML/PDF must keep:

- email rendered as `mailto:`
- portfolio URL clickable
- LinkedIn URL clickable
- GitHub URL clickable

This is enforced by `templates/cv.html.j2` and `tests/test_cv_template.py`.

## Testing Expectations

Run focused tests after small changes:

```bash
.venv/bin/pytest tests/test_apply_assist.py
.venv/bin/pytest tests/test_cv_template.py tests/test_web_extract.py
```

Run the full suite before handing work back:

```bash
.venv/bin/pytest
```

Headless smoke test (no submission, answers `n`):

```bash
printf 'n\n' | .venv/bin/job-hunt apply '<application-url>' --company '<company>' --role '<role>' --pdf '<resume.pdf>' --headless
```

## Apply Smoke Test Pattern

Use a public ATS application URL and a disposable local PDF when smoke-testing
the fill flow. The final submit button must remain human-only.

```bash
printf 'n\n' | .venv/bin/job-hunt apply \
  '<application-url>' \
  --company '<company>' \
  --role '<role>' \
  --pdf '<resume.pdf>' \
  --headless
```

Expected: safe profile fields and resume upload are attempted, the run stops
before submission, and review artifacts are written under `artifacts/apply/`.

## Implementation Notes

- Prefer deterministic parsing and repositories for local state.
- Use Playwright for browser/PDF work.
- Keep tracker updates centralized through `TrackerRepository`.
- Preserve Markdown/YAML state files as auditable sources.
- Avoid broad refactors while the closed loop is being stabilized.
- This workspace may not be a git repository; use `rg --files` and tests to inspect state.
- Do not expose the CDP port (`--remote-debugging-port`) from fill-only sessions. External `connect_over_cdp` connections share the browser process and will close the browser when their event loop exits.
- Workday transcript uploads are supported from either `profile/profile.yml` → `cowork.transcript_pdf` / `candidate.transcript_pdf` or a private local file named `storage/private/workday-transcript.*`. Do not commit transcript files.
- Workday buttons often use an invisible/overlay `role="button"` element above the real `<button>`. Use role/aria-label-safe click helpers for Sign In and Save and Continue; do not assume clicking the visible `<button>` will work.
- Workday implementation notes are documented in `docs/workday-apply-notes.md`.
