# Agent Apply Workflow

This workflow is for Claude Code, Codex CLI, or another local agent that can run shell commands and inspect a browser via screenshots.

The invariant is simple: the agent may prepare, fill, replace, screenshot, and record, but the user manually performs the final submission click.

## Token Rules (read this first)

Driving the application page through a browser MCP (Playwright MCP, chrome-devtools MCP, …) costs 10k-30k tokens per page snapshot and is **prohibited** — it is also unsafe against the running fill-only browser (see Browser Profile). All browser interaction goes through the CLI commands in this document. Cheapest source of truth first:

1. **`apply-review.json`** — structured fill result (`filled`, `skipped`, `required_empty`, `validation_issues`, `warnings`, `pdf`). Read this after every fill/refill. When it is clean, do NOT read the screenshot.
2. **`job-hunt apply-status`** — live text report of the open page (URL, Workday step, error banners, required-but-empty fields; `--controls` adds every visible form control with its current value). Use it to answer "where is the form stuck" without images.
3. **`job-hunt apply-do`** — one targeted action (`--click 'label'`, `--fill 'label=value'`, `--select 'label=option'`, `--check 'label'`) inside the live session. Use it to fix a single missed field instead of taking over the browser.
4. **Screenshots** (`apply-review-*.jpg`) — last resort, only when the JSON/status output shows a problem you cannot diagnose from text, or for confirmation-page evidence.

Token rules never apply to the artifacts themselves. The tailored CV, cover letter, and application answers are always generated on the premium tier — do not route them to the cheap tier to save tokens.

This document is the stable handoff contract for application automation. If another agent continues the work, it should preserve the command semantics and safety boundaries here.

## Roles

- `job-hunt apply --fill-only`: opens a persistent Chromium browser, fills all safe fields, keeps the browser open via a heartbeat-driven loop with idle-based timeout (60 min idle exit, not a hard 30/10-min deadline). Agent runs this in the background.
- `job-hunt apply-status [--controls]`: prints a compact text report of the live page (no screenshot). Agent runs this to check where the form stands.
- `job-hunt apply-do --click/--fill/--select/--check`: runs one targeted action in the open session. Agent runs this to fix a single missed field. Submit-like click labels are rejected.
- `job-hunt apply-replace-pdf <pdf>`: replaces the resume in the open browser session without closing or restarting. Agent runs this when the user asks to swap the PDF.
- `job-hunt apply-capture-page`: captures the current browser page in the open session. Agent runs this after the user submits, before recording, to preserve confirmation-page evidence.
- `job-hunt apply --no-browser --confirmed`: records the submission to the tracker, activity log, and Slack after the user confirms they clicked Submit. No browser interaction.
- Agent CLI: orchestration layer. Runs commands, reads screenshots, reports state, handles PDF swaps, records after confirmation.
- User: final reviewer and final submitter. Clicks the Submit button in the visible browser.

## Current Implementation Contract

The current CLI implementation lives in `job_hunt/cli.py`.

- `apply_assist`: public `job-hunt apply` command. Key flags:
  - `--fill-only`: fills form, keeps browser open via a heartbeat loop. Idle exit after 60 minutes with no IPC command / refill (heartbeat alone does not count as activity). Refreshes `.session.json` heartbeat every 5 s.
  - `--confirmed`: skips browser and stdin, records as Applied immediately. When passed with `--pdf`, the new tracker row is recorded with `pdf=✅` (audit fix 2026-05-08).
  - `--no-browser`: skips Playwright, prompts for stdin confirmation
  - `--headless`: Chromium runs headless (smoke tests only)
  - `--auto-submit`: gated by Review-gate-clean; see "Auto-submit" section below.
- `apply_replace_pdf`: public `job-hunt apply-replace-pdf` command. Writes a per-command sentinel `.cmd-<uuid>.json` into the active session's artifact dir plus a compatibility `.replace_pdf`; the running `--fill-only` loop drains pending commands every ~2 s in mtime order, so two replace requests in quick succession process serially without racing.
- `apply_capture_page`: public `job-hunt apply-capture-page` command. Writes a `.cmd-<uuid>.json` (kind=`capture_page`) plus compatibility `.capture_page`; the running loop captures the current page and appends an event to `apply-review.md` and `apply-run.jsonl`.
- `apply_status`: public `job-hunt apply-status` command. Writes a `.cmd-<uuid>.json` (kind=`status`); the loop answers via `.res-<uuid>.json` with URL, title, Workday step, error banners, filtered `required_empty`, visible actions, and (with `--controls`) the full form-control summary. The subcommand polls for the response (30 s timeout) and prints compact text.
- `apply_do`: public `job-hunt apply-do` command. Exactly one of `--click 'label'` / `--fill 'label=value'` / `--select 'label=option'` / `--check 'label'`. Writes a `.cmd-<uuid>.json` (kind=`do`); the loop runs the op via label-based locators (exact match first, ambiguous matches refused, with Workday dropdown/question fallbacks) and answers with `ok`, `detail`, the post-op URL, and the `required_empty` list. Submit-like click labels (`submit/apply/finish/complete/done/confirm/finalize/send`) are rejected at the CLI, at the loop handler, and again against the resolved element's own text before clicking.
- Session security: the fill-only loop stamps a per-session token into `.session.json`; `submit_command` copies it into every sentinel and the loop rejects sentinels whose token does not match (`command.rejected` event). Command/response ids are hex-validated and derived from filenames, and sentinel/response files are written atomically. `apply-status --controls` masks password/OTP/SSN-SIN/DOB/compensation values (`<filled>` instead of the raw value).
- When more than one fill-only session is alive, `apply-status` / `apply-do` require `--session <artifact-dir-substring>`.
- `_open_apply_page`: launches Playwright with `launch_persistent_context` (profile at `storage/browser-profile/`), navigates, runs auto-fill first, then attaches PDF.
- `_attach_resume`: priority 1 = click upload/attach/replace buttons or Airtable-style `browse` links → file chooser; priority 2 = `set_input_files` on all file inputs. Auto-fill always runs before PDF attachment so React components are fully initialized.
- `_auto_fill_application`: fills name, email, phone, LinkedIn/GitHub/portfolio, location, safe text fields, standard textareas, Airtable-style rich textboxes, and radio buttons with safe canned answers. Rich-text answers are only reported as filled after the page is read back and the answer is confirmed to persist.
- Saved draft answers from the previous `apply-review.json` are reused on later openings of the same application artifact directory by fuzzy-matching question text. This prevents generated long answers from disappearing when a form is reopened.
- `_apply_profile_values`: reads `profile/profile.yml`.
- `_record_manual_submission`: fuzzy-matches existing tracker row (company + role, role score ≥ 0.85 when company exact-matches) or appends a new row. Only helper that mutates tracker state for manual submissions.

Stable outputs after a confirmed submission:

- tracker row status: `Applied`
- tracker PDF flag: `✅` when a PDF was supplied
- email/application event source: `system_apply`
- event type: `application_submitted`
- activity type: `apply.submitted`
- Slack: delivered by `ActivityLogger` when activity forwarding is configured

## Standard Agent Loop

```
Step 1 — Fill (run in background):
  .venv/bin/job-hunt apply '<url>' \
    --company '<company>' --role '<role>' --pdf '<resume.pdf>' \
    --fill-only

Step 2 — Verify (agent reads JSON, not the screenshot):
  Read artifacts/apply/{slug}/apply-review.json
  Report to user: filled fields, PDF filename, any skipped questions
  If required_empty / validation_issues / warnings are non-empty (or pdf is
  null when one was expected): run `job-hunt apply-status --controls`, fix
  single fields with `job-hunt apply-do`, and only read the newest
  apply-review-*.jpg if still unclear.

Step 3 — Swap PDF if needed:
  .venv/bin/job-hunt apply-replace-pdf '<other-resume.pdf>'
  Wait ~3 s, confirm from the command output or apply-status

Step 4 — User submits:
  User clicks Submit Application in the open browser

Step 5 — Capture confirmation page:
  .venv/bin/job-hunt apply-capture-page
  Wait ~3 s, read the new apply-page-*.jpg screenshot

Step 6 — Record (after user confirms):
  .venv/bin/job-hunt apply '<url>' \
    --company '<company>' --role '<role>' --pdf '<resume.pdf>' \
    --no-browser --confirmed
```

## Artifact Layout

Each apply session has its own directory:

```
artifacts/apply/{YYYY-MM-DD}-{company-slug}-{role-slug}/
  apply-review-{hash}.jpg    ← after initial fill
  apply-review-{hash}.jpg    ← after each PDF swap
  apply-page-{hash}.jpg      ← manual confirmation/success page capture
  apply-review.md            ← human-readable review log
  apply-review.json          ← machine-readable review state, incl. validation_issues[]
  apply-controls.json        ← form-control summary; written when the fill left
                               required_empty / validation_issues non-empty
  apply-run.jsonl            ← structured event stream (one JSON per line)
  {resume-filename}.pdf      ← copy of the submitted PDF
  .cdp                       ← compatibility sentinel; present while fill-only session is active
  .session.json              ← heartbeat (pid + last_heartbeat)
  .cmd-<uuid>.json           ← per-command sentinel (transient)
  .res-<uuid>.json           ← per-command response (transient; apply-status / apply-do)
  login-modal-unknown.png    ← only present when login modal misbehaves
  login-modal-unknown.html   ← DOM dump matching the screenshot above
```

Screenshots are full-page JPEGs (quality 60) since 2026-07-09; sessions before that wrote `.png`. When a screenshot must be read, take the **most recent** `apply-review-*.jpg` — but prefer `apply-review.json` / `apply-status` first (see Token Rules).

## Browser Profile

`storage/browser-profile/` is a persistent Chromium user-data directory. Login sessions (LinkedIn, Greenhouse, etc.) are preserved between runs — the user can log into a site once in the Playwright browser and subsequent sessions will remember it.

Stale `SingletonLock` / `SingletonCookie` / `SingletonSocket` files are cleared automatically at the start of each session. Do not connect an external Playwright session via `connect_over_cdp` — doing so will kill the running browser when that session's event loop exits.

## PDF Replacement

While a `--fill-only` session is active, the agent can swap the resume without restarting:

```bash
.venv/bin/job-hunt apply-replace-pdf 'output/new-resume.pdf'
```

The running process drains the per-command sentinel queue (sorted by mtime) within ~2 seconds, calls `_attach_resume` (file-chooser click approach), waits 1.5 s, copies the new PDF to the artifact dir, and saves a new screenshot. The agent should read the new screenshot to confirm the swap. Subcommands warn when the heartbeat in `.session.json` is more than 30 s stale ("session likely dead, restart with apply --fill-only").

If the user logs in, completes MFA, or advances to a new ATS step while the
browser is already open, prefer re-running filling on the current page:

```bash
.venv/bin/job-hunt apply-refill-current-page
```

This preserves the live Workday/ATS session and avoids sending the user back
through account creation. When a browser session should be closed after login,
close it gracefully:

```bash
.venv/bin/job-hunt apply-close-session
```

Avoid killing Playwright/Chromium when a graceful close is available because it
can prevent cookies/localStorage from flushing to `storage/browser-profile/`.
However, do not wait blindly on an old or stuck process. If the active process
lacks refill support or blocks the closed loop, restart quickly and accept the
possible relogin tradeoff.

## Confirmation Page Capture

After the user manually clicks Submit and says the page changed, run:

```bash
.venv/bin/job-hunt apply-capture-page
```

The running process captures the current page as `apply-page-*.jpg` and appends an event to `apply-review.md`. This is best-effort evidence; the user confirmation remains the source of truth for recording.

## Fallback Recording

If the user submitted the application outside the normal fill-only flow (e.g., in their own browser), record it with:

```bash
.venv/bin/job-hunt apply '<url>' \
  --company '<company>' --role '<role>' --pdf '<resume.pdf>' \
  --no-browser --confirmed
```

`--confirmed` bypasses both Playwright and the stdin confirmation prompt, recording immediately.

## Auto-submit

`job-hunt apply --auto-submit` opts in to clicking the final `Submit` button automatically. The click only fires when **all** of these gates pass:

1. CLI flag `--auto-submit` was passed.
2. `profile/profile.yml` has `apply.auto_submit_enabled: true`.
3. The page is on a Workday host (`*.myworkdayjobs.com`). Other ATS sites stay manual until they have a structured Review gate.
4. `_collect_workday_review_issues(page)` returns empty (no `WD_REVIEW_*` codes).
5. `_required_empty_fields(page)` returns empty after filtering.

If any gate fails, the run logs an `auto_submit.gated` event with the reason in `apply-run.jsonl` and falls back to the normal "user submits manually" flow with no partial state. When auto-submit fires, two events are emitted: `auto_submit.fired` (button clicked) and `auto_submit.confirmed` (post-click DOM settled). The function returns with `submitted=True` and the tracker row is recorded as Applied.

Auto-submit is **off by default**. Both keys are required so a stray flag in shell history alone cannot trigger submission. Use it only for roles you have already evaluated and reviewed; never combine with `--headless`.

## Safety Rules

- Default behaviour: never click the final Submit/Apply button from automation. Only `--auto-submit` flips this, and only behind the multi-gate safety described above.
- Never answer a required question with invented facts.
- Pause for user input on compensation, legal status, sponsorship, relocation, start date, demographic data, or anything ambiguous.
- Do not paste secrets, cookies, OAuth tokens, Slack webhooks, or raw private config into chat.
- Keep screenshots and artifacts under `artifacts/apply/{slug}/`. Keep generated PDFs under `output/`.
- Do not mark a tracker row `Applied` from a screenshot alone. The user (or `--auto-submit` after a clean Review gate) must signal submission.
- Do not rely on employer confirmation email as proof for this CLI step unless the user explicitly asks to reconcile Gmail events.

## Known Safe Auto-Fill Fields

The implementation may auto-fill:

- name
- email
- phone
- location (including Ashby combobox resolution)
- LinkedIn
- portfolio/other links only when the field is clearly candidate-facing
- resume/CV file input
- textareas only when `_answer_for_application_question` returns a truth-grounded answer
- radio buttons only when `_radio_choice_for_question` returns an unambiguous choice

Never fill ATS anti-bot / honeypot fields. Workday can render a visible `Website`
input with helper text like `This input is for robots only, do not enter if you're human`.
Filling that field can make `Create Account` silently do nothing. The implementation
guards this through `_looks_like_honeypot_context`; if a review summary ever lists
`Website` on a Workday create-account page, treat it as a blocker and restart from a
clean page after fixing the fill rule.

Fields requiring user review:

- work authorization details beyond generic yes/no
- sponsorship questions
- start date
- salary expectations
- relocation
- demographic/self-identification questions
- anything with unclear wording
- any required file other than resume/CV

## Browser Smoke Test

Headless smoke test — no visible browser, auto-answers `n`, no tracker changes:

```bash
printf 'n\n' | .venv/bin/job-hunt apply \
  '<application-url>' \
  --company '<company>' \
  --role '<role>' \
  --pdf '<resume.pdf>' \
  --headless
```

Expected output:
- `Attached PDF` when a file input is present
- `Auto-filled fields` lists safe fields
- `Review screenshot: artifacts/apply/{slug}/apply-review-*.jpg`
- `No tracker changes made.`

Known smoke target (Ashby — Cohere Security Agents):

```bash
printf 'n\n' | .venv/bin/job-hunt apply \
  'https://jobs.ashbyhq.com/cohere/a5bbd015-65a9-48a1-aab1-b266bdbc9905/application' \
  --company 'Cohere' \
  --role 'Senior Software Engineer, Security Agents' \
  --pdf output/example-resume.pdf \
  --headless
```

At the time of validation (2026-04-29), this Ashby form fills Name, Phone, Email, Location, LinkedIn, and Resume (via "Upload File" button file chooser).

## Verification Commands

After changing apply code:

```bash
.venv/bin/pytest tests/test_apply_assist.py
.venv/bin/pytest
```

After a real manually submitted application:

```bash
.venv/bin/job-hunt activity list --since 1d
```

The activity list should include `apply.submitted`. If Slack is enabled, it should receive a safe summary.
