# Job Hunt Design

This document describes the current system as it exists now. It is not a
migration plan.

## Operating Model

Job Hunt is a local, human-in-the-loop job-search operations system. It helps
discover jobs, evaluate fit, generate application artifacts, assist with form
filling, record activity, reconcile employer responses, and manage outreach.

The final application submit button is always a human action.

## Closed Loop

```text
scan / pipeline add
  -> evaluate
  -> report + resume PDF + optional cover-letter PDF
  -> tracker row
  -> apply --fill-only or apply-answers
  -> user manually submits
  -> apply --confirmed records Applied
  -> activity log + optional Slack
  -> Gmail poll/reconcile updates tracker
```

The stable executable runbook is [full-loop-execution.md](full-loop-execution.md).
Apply-session safety rules live in [agent-apply.md](agent-apply.md).

## Source Files

Core local state:

- `profile/cv.md`: canonical resume source.
- `profile/profile.yml`: candidate identity, target roles, narrative, location,
  Workday preferences, transcript paths, and apply defaults.
- `config/settings.yml`: model, tracing, activity, email, and WebSearch config.
- `config/portals.yml`: job discovery queries and tracked companies.
- `profile/cv-experience.yml`: optional structured experience/education entries for form filling.
- `profile/workday-employers/`: optional employer-specific Workday question YAML.
- `data/applications.md`: canonical tracker.
- `data/pipeline.md`: URL inbox.
- `data/scan-history.tsv`: scan dedup/audit history.
- `data/contacts.jsonl`: local recruiter/referral/contact CRM.
- `data/outreach-events.jsonl`: outreach lifecycle events.
- `interview-prep/story-bank.md`: accumulated STAR+R stories.
- `reports/`, `output/`, `artifacts/`: generated evaluation and apply artifacts.

## Discovery

`job-hunt scan` discovers jobs from configured ATS APIs and optional Brave
WebSearch.

Direct ATS support:

- Greenhouse
- Lever
- Ashby

WebSearch support:

- Enable with `web_search.provider: brave` in `config/settings.yml`.
- Set the API key env var configured by `web_search.api_key_env`
  (`BRAVE_API_KEY` by default).
- Smoke test with `job-hunt search-test "<query>"`.

Companies marked `scan_method: websearch` are included only when a provider is
available. Missing provider or API key skips this tier without failing the scan.

## Evaluation Graph

`job-hunt evaluate` runs a sequential LangGraph workflow:

```text
extract JD
  -> classify archetype
  -> CV match
  -> role summary
  -> level strategy
  -> company/comp research
  -> personalization plan
  -> interview prep
  -> score and recommend
  -> draft application answers
  -> update story bank
  -> optional cover letter
  -> resume PDF
  -> report
  -> tracker
```

Single-job evaluation is intentionally sequential. It keeps model load
predictable and avoids fan-in races.

Prompt rules shared by evaluation nodes live in `prompts/shared.md`. They define
source-of-truth rules, archetype framing, compensation guidance, location policy,
ethical constraints, and output style.

## Reports And Artifacts

Reports are written to `reports/` and include:

- date, company, role, archetype, score, URL, PDF
- analysis blocks
- Section G draft application answers
- ATS keywords

Resume and cover-letter artifacts are rendered from Jinja2 HTML templates through
Playwright. Generated CV PDFs must preserve clickable links for email,
portfolio, LinkedIn, and GitHub. This contract is covered by tests.

## Tracker

`data/applications.md` is the canonical tracker. Core commands:

```bash
job-hunt tracker stats
job-hunt tracker verify
job-hunt tracker merge
job-hunt tracker dedup
job-hunt tracker normalize
job-hunt tracker check-sync
job-hunt tracker dashboard
```

Tracker writes use file locking. Synchronous flows write directly when they need
the final row number immediately. Deferred import flows may write TSV files
to `data/tracker-additions/` and merge later.

Canonical states are defined in `templates/states.yml`, including:

- `Evaluated`
- `Contacted`
- `Applied`
- `Responded`
- `Interview`
- `Offer`
- `Rejected`
- `Discarded`
- `SKIP`

## Apply Assistant

`job-hunt apply --fill-only` opens a visible browser, fills known fields, uploads
PDFs, writes review artifacts, and waits for the user. It does not submit.

Follow-up commands:

```bash
job-hunt apply-replace-pdf '<new-resume.pdf>'
job-hunt apply-refill-current-page
job-hunt apply-capture-page
job-hunt apply-close-session
job-hunt apply '<url>' --no-browser --confirmed
```

Workday-specific logic is split between `cli.py` orchestration and service
modules under `job_hunt/services/workday/`. Employer-specific question answers
belong in YAML under `profile/workday-employers/`.

Non-Workday fallback:

```bash
job-hunt apply-answers --company X --role Y --form-text-file form.txt
```

This generates copyable answers from the matched report, Section G, and CV.

## Outreach

Outreach is tracked separately from applications but can be linked to tracker
rows.

Contacts:

```bash
job-hunt contacts add --company Acme --name "Jane Doe" --relationship recruiter
job-hunt contacts search --company Acme
```

Outreach:

```bash
job-hunt linkedin Acme "AI Engineer"
job-hunt outreach draft <contact-id> --role "AI Engineer" --application 123
job-hunt outreach mark-sent <event-id> --follow-up-at 2026-05-16
job-hunt outreach mark <event-id> responded
job-hunt outreach due
```

`outreach draft` writes a local message draft under `data/outreach-drafts/` and
records a `drafted` event in `data/outreach-events.jsonl`.

## Career Strategy Helpers

Two standalone commands help decide whether to spend time outside the immediate
application loop:

```bash
job-hunt project-eval '<portfolio project idea>'
job-hunt training-eval '<course or certification>'
```

`project-eval` scores signal, uniqueness, demo-ability, metrics potential,
time-to-MVP, and interview-story value.

`training-eval` scores role alignment, recruiter signal, time cost, opportunity
cost, risk, and whether the training produces a portfolio artifact.

## Email Reconciliation

Gmail polling and reconcile convert employer emails into structured local
events, then update or create tracker rows when confidence is high enough.

Conservative path:

```bash
job-hunt email poll
job-hunt email reconcile --import-new --new-only --skip-review --apply
job-hunt review list
job-hunt email approve-event <event-prefix> --company X --role Y --status Applied
```

Low-confidence items remain in the review queue until explicitly approved or
ignored.

## Activity And Notifications

Operational events are recorded in `data/activity-log.jsonl`. Slack forwarding is
optional and goes through `ActivityLogger`; apply code must not call Slack
directly.

```bash
job-hunt activity list --since 7d
job-hunt activity tail
job-hunt activity slack-test
```

## Safety Rules

- Do not click final Submit/Apply/Send automatically.
- Do not invent candidate facts.
- Do not answer legal, sponsorship, relocation, compensation, demographic, or
  ambiguous required questions without confirmation.
- Do not print secrets, cookies, OAuth tokens, Slack webhooks, or browser storage.
- Record `Applied` only after the user explicitly confirms manual submission.
- Keep generated artifacts and private local state out of git.

## Verification

Focused tests for recent surfaces:

```bash
.venv/bin/pytest tests/test_outreach_tracking.py tests/test_outreach_research_prompts.py
.venv/bin/pytest tests/test_web_search_brave.py tests/test_scan_via_websearch.py tests/test_comp_research_brave.py
.venv/bin/pytest tests/test_apply_assist.py
```

Full suite:

```bash
.venv/bin/pytest
```
