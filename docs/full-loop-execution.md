# Full Closed-Loop Execution Runbook

Use this file as the prompt/runbook for an agent that will execute one complete job-hunt loop:

1. choose or verify a job
2. evaluate fit and artifacts
3. open the application form
4. auto-fill safe fields
5. let the user review and manually submit
6. capture submission evidence
7. record tracker/event/activity/Slack
8. optionally reconcile Gmail feedback later

The agent may fill, inspect, revise, replace PDFs, capture screenshots, and record after confirmation. The agent must never click the final Submit/Apply button.

## Copy-Paste Agent Prompt

Paste this into Claude Code, Codex CLI, or another local agent session from the repository root:

```text
You are executing the job-hunt full closed loop once.

Repository: the current `job-hunt` repository root

Read and follow:
- AGENTS.md
- docs/agent-apply.md
- docs/full-loop-execution.md

Hard rules:
- Never click the final Submit/Apply button.
- Do not record an application as Applied until the user explicitly says they manually submitted it.
- Do not invent candidate facts. Use profile/profile.yml, profile/cv.md, reports/, tracker rows, and selected PDFs.
- Pause for user confirmation on legal/work authorization, sponsorship, relocation, compensation, demographic questions, start date, or ambiguous required questions.
- Do not print secrets, Slack webhooks, OAuth tokens, cookies, or browser storage.

Execution:
1. Run preflight commands.
2. Pick or verify the target job. Do not proceed with a SKIP/low-score job unless the user explicitly says to continue.
3. Confirm the resume PDF exists and is appropriate for the role.
4. Generate the agent apply runbook with job-hunt agent-apply.
5. Run job-hunt apply --fill-only in visible browser mode.
6. Inspect the latest apply-review.md, apply-review.json, and screenshot.
7. Report to the user:
   - matched report and score/recommendation
   - fields filled
   - required fields still empty
   - warnings
   - PDF filename shown
   - whether you recommend submitting
8. If the user asks for changes, make them in the open browser when possible. If they ask to swap PDF, use job-hunt apply-replace-pdf.
9. When the user says ready, instruct the user to manually click Submit/Apply in the browser.
10. After the user says they submitted, run job-hunt apply-capture-page.
11. Inspect the newest apply-page-*.png for a confirmation message if available.
12. Record with job-hunt apply --no-browser --confirmed.
13. Verify tracker, activity log, event log, and Slack activity.
14. Summarize exactly what happened and any residual follow-up.
```

## Minimal User Input

If the user only has a job URL or application URL, start with:

```bash
.venv/bin/job-hunt loop '<job-or-application-url>'
```

Example:

```bash
.venv/bin/job-hunt loop 'https://jobs.ashbyhq.com/cohere/a5bbd015-65a9-48a1-aab1-b266bdbc9905/application'
```

`job-hunt loop` extracts the URL, infers company/role, matches tracker/report context, selects a likely PDF, and prints the next `job-hunt agent-apply` command. If inference is weak because the page blocks extraction, add an optional override:

```bash
.venv/bin/job-hunt loop '<job-or-application-url>' '<company role hint>'
```

## Preflight

Run:

```bash
cd /path/to/job-hunt
pwd
.venv/bin/job-hunt config doctor
.venv/bin/pytest tests/test_apply_assist.py tests/test_cv_template.py tests/test_web_extract.py
```

If the focused tests fail, stop and fix them before opening a real application.

Optional full suite:

```bash
.venv/bin/pytest
```

## Non-Polluting Closed-Loop Smoke

Use this when you need to verify the post-submit recording leg without touching
the real tracker:

```bash
tmpdir=$(mktemp -d)
mkdir -p "$tmpdir/config" "$tmpdir/data"
cp config/settings.yml "$tmpdir/config/settings.yml"
(
  cd "$tmpdir" &&
  /path/to/job-hunt/.venv/bin/job-hunt apply 'https://example.com/apply' \
    --company 'Smoke Co' \
    --role 'Smoke Role' \
    --no-browser \
    --confirmed
)
rm -rf "$tmpdir"
```

Expected behavior:

- prints `Recorded Applied tracker row #1`
- writes a temporary `data/applications.md` row with status `Applied`
- writes a temporary `data/email-events.jsonl` event with source `system_apply`
- writes a temporary `data/activity-log.jsonl` event with type `apply.submitted`
- does not touch the real project tracker or activity log

## Select Or Verify A Job

If the user already gave a URL/company/role/PDF, use those.

If the agent must choose a job, prefer:

- status `Evaluated`
- score >= `3.8/5`
- recommendation `APPLY` or strong `MAYBE`
- current/open posting
- role aligned with AI Engineer, LLM Engineer, AI orchestration, backend/full-stack AI, or strong data/ML fit

Avoid by default:

- recommendation `SKIP`
- score < `3.0/5`
- closed postings
- forms requiring unsupported legal claims
- jobs already marked `Applied` or `Rejected`

Useful inspection commands:

```bash
rg -n "APPLY|MAYBE|SKIP|Applied|Rejected|Cohere|Faire|AI Engineer|LLM|Agent" data/applications.md reports
ls -lt reports | head -30
```

If needed, evaluate a job first:

```bash
.venv/bin/job-hunt evaluate '<job-url-or-text>'
```

## Confirm PDF

Choose the most role-appropriate PDF under `output/`.

Check it exists:

```bash
ls -lh '<resume.pdf>'
```

If the PDF was generated from `templates/cv.html.j2`, contact links must be clickable:

- email -> `mailto:`
- portfolio -> URL link
- LinkedIn -> URL link
- GitHub -> URL link

## Login Persistence

When the user logs in, completes MFA, or advances to another ATS step in the
visible browser, prefer keeping that browser open and use:

```bash
.venv/bin/job-hunt apply-refill-current-page
```

to refill/upload on the current page. Use:

```bash
.venv/bin/job-hunt apply-close-session
```

only when you intentionally want to close the browser while saving cookies and
local storage into `storage/browser-profile/`.

This is a preference, not a hard blocker. If the active process is old, stuck, or
lacks current-page refill support, restart quickly and accept the possible
relogin tradeoff rather than waiting several minutes without progress.

## Generate Apply Runbook

Template:

```bash
.venv/bin/job-hunt agent-apply \
  '<application-url>' \
  --company '<company>' \
  --role '<role>' \
  --pdf '<resume.pdf>'
```

Read the generated runbook and follow its commands.

## Fill Only

Run the fill command in visible browser mode:

```bash
.venv/bin/job-hunt apply \
  '<application-url>' \
  --company '<company>' \
  --role '<role>' \
  --pdf '<resume.pdf>' \
  --fill-only
```

This opens a persistent Chromium profile and keeps the browser open for review. The final Submit/Apply button is not clicked by automation.

The command prints:

- final URL
- artifact directory
- matched report
- identity/report warnings
- PDF attachment status
- auto-filled fields
- required fields still empty
- visible action labels
- review screenshot path
- review summary path

## Review Artifacts

Open/read the latest session files:

```bash
ls -lt artifacts/apply/* | head
sed -n '1,240p' artifacts/apply/<session>/apply-review.md
```

The agent should report:

- matched report path, score, recommendation
- whether recommendation/score blocks submission
- filled fields
- skipped/needs-review fields
- required empty fields
- visible Submit/Apply button
- PDF filename in the screenshot

Special Workday note: if the application starts with `Create Account`, do not fill
any `Website` field whose surrounding text says it is for robots only or should not
be entered by humans. That is a honeypot field and can make `Create Account` fail
without an obvious error. Restart from a clean page after fixing the fill rule if a
review summary shows `Website` was auto-filled on a Workday account page.

The agent must ask the user to review the visible browser before continuing.

## Modify Or Replace PDF

If the user wants field edits, change them in the visible browser when the agent has browser-control tools. Otherwise tell the user the exact field and replacement value.

If the user wants a different PDF:

```bash
.venv/bin/job-hunt apply-replace-pdf '<new-resume.pdf>'
```

Wait a few seconds, then inspect the newest screenshot and confirm the PDF filename changed.

## Manual Submit

When everything looks right, tell the user:

```text
Please manually click the final Submit/Apply button in the browser. Tell me "submitted" only after the page changes or you see a confirmation.
```

The agent must not click that button.

## Capture Confirmation Evidence

After the user says they submitted:

```bash
.venv/bin/job-hunt apply-capture-page
```

Wait a few seconds. Then inspect the newest `apply-page-*.png` in the session artifact directory.

Look for confirmation language such as:

- Thank you for applying
- application received
- your application has been submitted
- confirmation

This screenshot is evidence, but user confirmation remains the source of truth.

## Record Application

Only after the user explicitly confirms manual submission:

```bash
.venv/bin/job-hunt apply \
  '<application-url>' \
  --company '<company>' \
  --role '<role>' \
  --pdf '<resume.pdf>' \
  --no-browser \
  --confirmed
```

Expected output includes:

```text
Recorded Applied tracker row #...
```

## Verify Closed Loop

Run:

```bash
.venv/bin/job-hunt activity list --since 1d
rg -n '<company>|<role>|apply.submitted|system_apply' \
  data/applications.md \
  data/activity-log.jsonl \
  data/email-events.jsonl
```

Verify:

- `data/applications.md` target row is `Applied`
- PDF flag is `✅` when a PDF was supplied
- `data/email-events.jsonl` has source `system_apply`
- `data/activity-log.jsonl` has type `apply.submitted`
- Slack received the safe summary if Slack is enabled

## Optional Gmail Feedback Loop

Later, after employer emails may arrive:

```bash
.venv/bin/job-hunt email poll --live --max-results 50
.venv/bin/job-hunt email reconcile --limit 50 --import-new --new-only --skip-review --apply
.venv/bin/job-hunt email review-candidates --limit 20
```

Do not overwrite existing statuses blindly. Review low-confidence items.

## Stop Conditions

Stop and ask the user if:

- matched report is `SKIP` or score < `3.0/5`
- company/role on page does not match the intended job
- required legal/work-authorization fields are unclear
- the PDF failed to attach
- a required field remains empty
- a site asks for credentials, payment, sensitive ID, or unusual personal data
- the page shows an error, closed job, or expired posting

## Final Report To User

At the end, summarize:

- job/company/role
- PDF used
- tracker row updated
- evidence screenshot path
- activity/event verification result
- Slack result if visible
- any follow-up, especially Gmail feedback monitoring
