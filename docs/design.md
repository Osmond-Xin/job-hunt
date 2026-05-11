# Job Hunt Design

This document describes the current system as it exists now. It is not a
migration plan.

## Operating Model

Job Hunt is a local, human-in-the-loop job-search operations system. It helps
discover jobs, evaluate fit, generate application artifacts, assist with form
filling, record activity, reconcile employer responses, and manage outreach.

The final application submit button is always a human action.

## Operator Mode

A single top-level field in `profile/profile.yml` decides whether the system
behaves as an intern / co-op hunting tool or a full-time hunting tool:

```yaml
mode: "student"   # or "full"
```

The field is the only signal. The system intentionally has no awareness of
study permits, graduation dates, work authorization windows, or any other
external state — see `docs/design-notes.md` §N for the rationale.

Reading the value:

- All subsystems read through `services.profile_loader.current_mode()`.
- Default is `"full"` when missing or malformed; never auto-flips on the
  calendar.
- Surface in `job-hunt config doctor`.
- Flip atomically via `job-hunt config set-mode <student|full>`. The command
  refuses to no-op without `--force` and prints which subsystems will pick up
  the change.

What the mode controls:

| Subsystem | mode = student | mode = full |
|---|---|---|
| Scan title filter | `title_filter.student` (positives include `intern`, `co-op`, `internship`, `student`, `new grad`; negatives include `senior`, `staff`, `principal`, `manager`) | `title_filter.full` (current senior list; intern/co-op are negatives) |
| Tracked-company eligibility | `eligibility_tags` containing `intern` / `coop` required when set; missing tags = both modes | `eligibility_tags` containing `full` / `full_time` required when set; missing tags = both modes |
| JD eligibility gate | intern / co-op JD passes through to scoring; FT JD forced to SKIP; ambiguous passes through | FT JD passes through; intern / co-op forced to SKIP; ambiguous passes through |
| Scoring weights and thresholds | Tech 20% / Level 10% / Domain 20% / Growth 25% / Co 25%; apply ≥ 3.5, maybe 3.0–3.5 | Tech 30 / Level 20 / Domain 15 / Growth 15 / Co 20; apply ≥ 4.0, maybe 3.5–4.0 |
| Active archetypes | only entries tagged `eligibility: student` | only entries tagged `eligibility: full` |
| Narrative / cover-letter framing | `narrative.student` block; emphasises learning velocity and co-op fit | top-level `narrative.*` (the "20-year veteran" framing) |
| Compensation expectation | `compensation.student_minimum` / `student_range` (typically hourly) | `compensation.minimum` / `target_range` |
| Auto-submit | force-disabled regardless of CLI flag and profile flag | follows existing two-key gate |
| Activity events | emitted with `mode: "student"` for funnel slicing | emitted with `mode: "full"` |

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
- `profile/profile.yml`: top-level `mode` switch, candidate identity, target
  roles (each tagged `eligibility: student | full`), narrative variants
  (`narrative.*` for full mode plus `narrative.student` block), location,
  Workday preferences, transcript paths, compensation ranges per mode,
  and apply defaults.
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
WebSearch. Scans branch on the operator mode at runtime.

Direct ATS support:

- Greenhouse
- Lever
- Ashby

WebSearch support (Brave):

- Enable with `web_search.provider: brave` in `config/settings.yml`.
- Set the API key env var configured by `web_search.api_key_env`
  (`BRAVE_API_KEY` by default).
- Smoke test with `job-hunt search-test "<query>"`.

Companies marked `scan_method: websearch` are included only when a provider is
available. Missing provider or API key skips this tier without failing the scan.

Mode-aware filtering:

- Title filter is split into `title_filter.student` and `title_filter.full`
  groups in `config/portals.yml`. The active mode selects which group runs.
  Legacy top-level `positive`/`negative` keys remain a fallback for older
  configs that have not been migrated.
- Each tracked company may declare optional `eligibility_tags` like
  `[intern, coop]` or `[full]`. Missing tags = scanned in both modes (the
  default; covers ATS boards that mix intern + FT). Explicit tags restrict
  the company to a specific mode.

### Tier-3 Cross-Employer Discovery Channels

`config/portals.yml::discovery_channels` declares cross-employer search
channels — LinkedIn / Indeed / Glassdoor / YC Work-at-a-Startup /
Wellfound for full mode, and WaterlooWorks / TalentEgg / Magnet / Job
Bank for student mode.

Each channel carries:

- `id` — short slug used as `ScannedJob.portal`.
- `enabled` — defaults to `false`; explicit opt-in to avoid quota burn.
- `modes` — list like `[full]` / `[student]`.
- `query_template` — `{role}` / `{location}` placeholders interpolated
  against `profile.yml::candidate.target_roles` ×
  `target_locations`. Empty `target_roles` skips the channel.

Channels run only when a WebSearch provider is configured. Results flow
through the same title / location / dedup pipeline as tier-1 and tier-2.
Restrict a single run to one channel via `job-hunt scan --channel <id>`.

### WebSearch Cache And Quota

Brave responses go through a transparent caching wrapper
(`services.web_search.CachingProvider`):

- **Cache**: `cache/web_search/brave/entries/<sha256>.json`, keyed by
  `(query, count, freshness)`. TTL defaults to 24h; configurable via
  `web_search.cache_ttl_seconds`. Disable with `web_search.cache_enabled:
  false`.
- **Counter**: `cache/web_search/brave/usage.json`, bucketed by UTC
  `YYYY-MM` with `api_calls` / `cache_hits` / `errors`. Brave's free tier
  is ~2k queries/month, so the counter is the first thing to consult
  before a wide scan.
- **Inspect**: `job-hunt search-usage` prints the current month; pass
  `--month YYYY-MM` for a historical bucket.
- Empty results are not cached and are counted as `errors`, so a
  transient outage isn't pinned for 24h.

### WebSearch Grounding (`--with-search`)

Two CLI commands accept an optional `--with-search` flag that runs live
Brave queries and injects a snippet block into the prompt context:

```bash
job-hunt research <company> "<role>" --with-search
job-hunt linkedin <company> "<role>" --with-search
```

- `research` favours strategy / recent moves / engineering culture queries.
- `linkedin` favours news and product-announcement queries that can become
  the message hook.
- The flag is a no-op when `web_search.provider` is unset or
  `BRAVE_API_KEY` is missing — the prompts render without the snippet block.
- Snippet formatting and URL deduping live in
  `services.web_search.format_search_hits()`; reused by any future
  `--with-search`-style flag.

Compensation research (`nodes/research.py::company_comp_research`) injects
Brave hits into the comp prompt automatically when a provider is configured;
no flag required.

## Evaluation Graph

`job-hunt evaluate` runs a sequential LangGraph workflow:

```text
extract JD
  -> verify active     -- inactive  -> mark unavailable -> END
  -> eligibility gate  -- mismatch  -> mark ineligible  -> END
  -> classify archetype
  -> CV match
  -> role summary
  -> level strategy
  -> company/comp research   (optional Brave injection)
  -> personalization plan
  -> interview prep
  -> score and recommend     (mode-branched weights + thresholds)
  -> draft application answers
  -> update story bank
  -> optional cover letter   (mode-branched framing)
  -> resume PDF
  -> report
  -> tracker
```

Single-job evaluation is intentionally sequential. It keeps model load
predictable and avoids fan-in races.

The eligibility gate is a pure-heuristic node (no LLM call) that runs before
any expensive analysis:

- Classifies the JD as `student` (intern / co-op posting), `full` (full-time
  posting), or `unknown` based on title regex first, then a JD-prefix scan.
- Routes to `mark_ineligible -> END` (recommendation forced to `skip`) when
  the classification disagrees with the active mode.
- `unknown` always passes through — let the scorer handle ambiguity.

The score-and-recommend prompt branches on mode at render time: weights and
apply / maybe / skip thresholds match the active mode (see the table in the
Operator Mode section). The cover-letter prompt branches the same way and
chooses the active narrative variant.

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

Auto-submit is gated by four simultaneous conditions:

- CLI flag `--auto-submit` set, AND
- `apply.auto_submit_enabled: true` in `profile/profile.yml`, AND
- mode is `full` (auto-submit is force-disabled in student mode regardless
  of the other flags — co-op forms have higher per-employer variance), AND
- the URL host is a Workday host with the Review gate clean
  (`validation_issues == []` AND `required_empty == []`).

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

Apply events (`apply.submitted`, `apply.cancelled`) carry the operator mode
at emission time as a top-level `mode` field on `ActivityEvent`. This makes
later funnel analysis able to slice apply outcomes by student vs full
without joining against the tracker. Other event types may emit `mode: null`
when the mode is not meaningful (e.g. email poll); legacy readers ignore it
harmlessly.

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
.venv/bin/pytest tests/test_web_search_brave.py tests/test_web_search_cache.py tests/test_scan_via_websearch.py tests/test_comp_research_brave.py
.venv/bin/pytest tests/test_apply_assist.py
.venv/bin/pytest tests/test_profile_mode.py tests/test_profile_normalize_mode.py tests/test_eligibility_gate.py tests/test_scan_mode.py tests/test_config_set_mode.py tests/test_activity_mode_tag.py
.venv/bin/pytest tests/test_with_search_grounding.py
```

Full suite (339 passing as of 2026-05-11):

```bash
.venv/bin/pytest
```
