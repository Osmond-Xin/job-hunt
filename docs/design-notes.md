# Design Notes & Open Questions

Rolling memo for design completeness review. Not a migration plan — captures
what is good as-is, what needs improvement, and known mismatches between the
system and the operator's actual situation.

Last updated: 2026-05-10.

## Section A — Architecture & Modularity Audit

### A.1 `cli.py` size

- **Status**: ~6800 lines. Workday review_gate / required_empty /
  application_questions / employer_config are already extracted, but
  login / my_information / my_experience / voluntary_disclosures and the
  core `_workday_advance_all_steps` orchestration still live in `cli.py`.
- **Verdict**: improve, medium priority. Pain is onboarding new contributors
  and writing tests around orchestration; functional capacity is fine.
- **Suggested trigger**: do one more extraction pass the next time a Workday
  flow change touches this region. Cut by step (login / my_info / my_exp /
  questions / disclosures / review), not by concept.
- **Related debt**: backward-compat shims (`workday_employer.py` re-exports,
  `cli.py` aliases like `_workday_review_validation_issues`). Need a stated
  sunset rule, otherwise they live forever.

### A.2 ATS coverage asymmetry

- **Status**: Workday has yaml-driven question fill + structured Review gate
  + multi-gate auto-submit. Greenhouse / Lever / Ashby are scanned for
  discovery only and stay manual on the apply side.
- **Verdict**: tradeoff, accept. Workday is the worst ATS surface and gets
  the heaviest investment, correctly. But `docs/design.md` should explicitly
  say "Workday-first apply assistant; other ATSes are generic fill +
  manual submit" so readers do not assume parity.
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

- **Status**: `apply-review.json` has no `schema_version` field.
  Downstream `jq`/dashboards can break silently if the shape evolves.
- **Verdict**: small improvement, cheap. Add `schema_version: 1` when next
  touching the writer.

### C.3 Tracker integrity

- **Status**: filelock + fuzzy match (token_sort_ratio ≥ 0.85) +
  `tracker verify` for status / score / fuzzy-dup checks.
- **Risk**: a hand edit can silently break column alignment.
  `tracker_ops` writes a 9-column TSV but `verify` does not enforce
  9 columns on the canonical markdown table.
- **Verdict**: small improvement. Add a "every row has 9 columns"
  hard check in `verify`.

## Section D — External APIs (WebSearch / LLM)

### D.1 Brave WebSearch governance

- **Status**: provider abstraction in progress
  (`build_web_search_provider() -> WebSearchProvider | None`),
  graceful degrade when key absent. Phases A–D scoped.
- **Gap 1**: no result cache; running `comp_research` for the same company
  twice burns quota.
- **Gap 2**: no rate-limit / quota counter. Brave free tier is ~2k/month;
  a single scan with 50 websearch-mode companies could blow it.
- **Verdict**: add a 24h on-disk cache + a usage counter after phases A–D
  ship. Keep deferred until then to avoid overlap.

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

- **Status**: `evaluate` runs every node end-to-end, even when an early
  node already implies SKIP.
- **Test signal**: `tests/test_low_score_gate.py` exists.
- **Verdict**: confirm whether the low-score early-exit is actually wired
  into the runtime graph or only tested at the helper level. If not wired,
  wire it — saves both time and tokens on obviously-bad JDs.

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

## Section L — Top 3 Cheap Wins

If only three improvements are picked from the above:

1. **Add `schema_version` to `apply-review.json`** (Section C.2) — 5
   minutes; prevents future silent breakage.
2. **Tighten `tracker verify` with a 9-column hard check** (Section C.3) —
   30 minutes; defends against silent markdown corruption from hand edits.
3. **Confirm low-score early-exit is wired in evaluate runtime**
   (Section F) — investigation before a fix; potentially saves token cost
   on every SKIP-bound JD.

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
likely to hold roles the operator can actually take. Suggested approach:

1. Treat LinkedIn / Indeed / Glassdoor as WebSearch-driven scan tiers,
   reusing the Brave provider once phases A–D ship.
2. Add ATS adapters in priority order driven by *which postings get
   bookmarked manually*. Don't pre-build adapters for ATSes the operator
   never sees.
3. Add a "student channels" scan tier with WaterlooWorks / TalentEgg /
   Magnet / Job Bank queries — gated by the eligibility window described
   below.

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
