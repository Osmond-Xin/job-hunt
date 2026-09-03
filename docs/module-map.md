# Module Map

Where things live, and what owns them. `docs/design.md` is organised by
feature; this document is organised by module path. Written for someone who
knows the domain (job applications, ATS, tracker rows) but has never opened
this repo.

`job_hunt/` is ~25,600 lines. `cli/` alone is 8,032 of them, split across ten
files — the largest, `apply.py`, is still 5,171 lines on its own (why, below).
The package holds zero layering inversions, checked by numbering the layers
and walking every import (below), not asserted.

## Layers

```
models/  ←  repositories/  ←  services/  ←  nodes/  ←  graphs/  ←  cli/
config/     (persistence)     (the work)    (graph     (LangGraph   (commands)
(types)                                     steps)      wiring)
```

Each arrow points from what is depended on to what depends on it. Nothing
left of the arrow imports anything right of it — see "Direction, not
acyclicity" below for what enforces that.

- **`models/`** and **`config/`** — Pydantic models, the one `TypedDict`
  graph state (`models/state.py`), and `Settings` (`config/models.py`). No
  I/O, and no imports of anything else in `job_hunt/`.
- **`repositories/`** — markdown (`tracker_repo.py`, for
  `data/applications.md`) and JSONL (`email_event_repo.py`,
  `email_decision_repo.py`, `review_repo.py`, and the shared base they now
  sit on, `jsonl_log.py`) persistence. No LLM calls, no business logic.
- **`services/`** — the actual work: scanning, scoring inputs, Workday
  automation, email reconciliation, outreach, redteam review. Where new
  logic almost always belongs.
- **`nodes/`** — thin LangGraph node functions. Each one reads `JobHuntState`,
  calls into `services/`, and returns a partial-state dict.
- **`graphs/`** — wires nodes into a `StateGraph` and compiles it.
- **`cli/`** — Typer commands. Orchestrates graphs and services; owns no
  business logic of its own, though `apply.py` still carries a large amount
  of Workday/LinkedIn browser-driving code that hasn't been given a seam to
  move behind yet (below).

### Direction, not acyclicity

A previous pass through this codebase found four modules importing across a
layer in the wrong direction, and while writing them down as "known issues"
this document quietly noted the import graph was still acyclic — as if that
were the reassurance. It isn't: a cycle check passes on every inversion
below, because each was one-directional. Acyclicity was never the property
that mattered; direction was.

All four have since been fixed, plus three more found while fixing them:

- `nodes/_prompts.py`, a private helper imported by two `services/` modules
  despite the underscore, is now `services/prompts.py`.
- `nodes/_llm.py`, holding `LLM_FAILURE_MARKER` and the tier/failure policy
  `cli/` read directly, is now `services/llm/call.py`.
- `pdf_page_count`, reached by `services/redteam.py` through a function-local
  import into `nodes/_cv_fit.py` to dodge the layering, now lives in its own
  module, `services/pdf.py`. `next_trim` — the CV-shortening logic, not a
  generic byte parser — stayed behind in `nodes/_cv_fit.py`.
- `TrackerEntry` and `normalize`, needed by `models/state.py` but defined in
  `repositories/tracker_repo.py` (the bottom layer importing the one above
  it), are now `models/tracker.py`.
- `services/batch.py` imported `cli/_shared.py` — a service reaching into
  the command layer. The three helpers there (`_resolve_source_type`,
  `_extract_loop_url_metadata`, `_apply_profile_values`) turned out to be
  service-level work, not CLI glue; they moved to `services/source_type.py`,
  `services/web_extract.py`, and `services/profile_loader.py` respectively,
  and `cli/_shared.py` is gone.
- `services/batch.py` also imported `graphs/` directly to build the
  evaluate-job graph. `run_batch()` now takes `graph_builder` as an injected
  argument — `cli/evaluation.py` passes `build_evaluate_job_graph` — which
  removes the inversion and lets a test hand it a fake instead of patching a
  module path.
- `TrackerRepository.find_match` delegated up to `services/employer_match.py`
  to resolve a circularity that no longer exists once `TrackerEntry` and
  `normalize` live in `models/`. `find_match` is gone; every caller
  (`cli/apply.py`, `cli/evaluation.py`, `nodes/tracker.py`,
  `services/checkup.py`, and the three `services/email/` modules that match
  employers) constructs `EmployerMatcher` directly.

The check that actually holds: number the layers `models`/`config` 0,
`repositories` 1, `services` 2, `nodes` 3, `graphs` 4, `cli` 5, and assert no
module imports from a layer above its own — counting function-local imports,
which is exactly where the `pdf_page_count` inversion was hiding. It
currently passes over **276 internal edges with zero inversions**, and
`models/` imports nothing from `job_hunt` outside itself.

### Highest fan-in (most-imported modules)

| Module | Imported by |
|---|---|
| `models/state.py` | 18 modules |
| `config/models.py` | 18 modules |
| `repositories/tracker_repo.py` | 15 modules |
| `services/prompts.py` | 11 modules |

`config/models.py` and `repositories/tracker_repo.py` both grew (10→18,
12→15) — mostly because the `cli.py` split turned one importer into several:
each command module (`cli/evaluation.py`, `cli/apply.py`, `cli/setup.py`, ...)
now imports `Settings` and `TrackerRepository` on its own rather than sharing
one module-level import. `services/prompts.py` carries the same fan-in (11)
it had as `nodes/_prompts.py` — the move didn't change who needs it, only
where it lives.

Anything touching these files' public shape (state keys, `TrackerEntry`
fields, `render()`'s call signature, `Settings`) should be grepped for
callers before the signature changes, not after.

## `job_hunt/` package layout

```
job_hunt/
├── cli/                     8,032 lines across 10 files — every `job-hunt <command>`
│   ├── __init__.py           292   Typer app + 10 sub-apps + pipeline_app; re-exports
│   │                               every command/helper so `job_hunt.cli.<name>` still
│   │                               resolves the way it did as one file
│   ├── apply.py             5,171   apply/apply-do/apply-answers + all Workday and
│   │                               LinkedIn browser-driving helpers (why it's still
│   │                               one file: below)
│   ├── setup.py               461   init, config validate/doctor/set-mode, resume import
│   ├── outreach.py            535   contacts, outreach draft/log, research/project/
│   │                               training-eval, _gate_outward_artifact
│   ├── discovery.py           258   scan, triage, shortlist, search
│   ├── evaluation.py          341   evaluate, evaluate-batch
│   ├── tracking.py            332   tracker subcommands, pipeline subcommands, checkup
│   ├── mail.py                345   email subcommands, review subcommands
│   ├── diagnostics.py         286   trace/activity/schedule/llm/search-test subcommands
│   └── _render.py              11   console + _short(), shared by every command file
├── models/                 types only, no I/O, imports nothing else in job_hunt/
│   ├── state.py             JobHuntState (the graph's TypedDict)
│   ├── job.py                JobMeta, ArchetypeResult, CandidateProfile
│   ├── evaluation.py          EvaluationScores, PdfContent
│   ├── review.py              review-queue item shapes
│   ├── events.py              ActivityEvent and friends
│   ├── tracker.py             TrackerEntry, normalize() — moved out of
│   │                           repositories/tracker_repo.py (below)
│   └── posting.py             JobPosting, from_row(), SourceHealth, SourceResult —
│                               the single normalisation point for board/API rows
│                               (below)
├── repositories/
│   ├── tracker_repo.py       data/applications.md read/write/lock
│   ├── jsonl_log.py           generic append-only JSONL log the other three now
│   │                           share (below)
│   ├── email_event_repo.py   data/*.jsonl for Gmail-derived events
│   ├── email_decision_repo.py approve/ignore decisions on review items
│   └── review_repo.py        the review queue itself
├── config/
│   └── models.py            Settings (config/settings.yml) — LlmConfig,
│                             WebSearchConfig, AdzunaConfig, etc.
├── services/                 34 top-level modules + 5 subpackages
│   ├── scan.py               the 7-tier scan orchestrator (below)
│   ├── gov_boards.py, regional_boards.py, jobbank.py, adzuna.py,
│   │   workday_boards.py     tier 4–7 board adapters (docs/design.md
│   │                         §Discovery has the per-source detail)
│   ├── posted_date.py         one body of "N days/hours ago" → ISO date
│   │                           arithmetic, shared by gov_boards.py and scan.py's
│   │                           Workday/Adzuna/Job Bank date handling (below)
│   ├── shortlist.py           build_shortlist() — triage's composition, pulled out
│   │                           of the `triage` command body (below)
│   ├── batch.py                run_batch() — evaluate-batch's concurrent runner,
│   │                           pulled out of the `evaluate-batch` command body (below)
│   ├── employer_match.py      EmployerMatcher — the one place two employer/role
│   │                           names get decided to mean the same tracker row (below)
│   ├── usage_ledger.py        the JSONL spend ledger evaluate-batch --max-cost reads
│   ├── source_type.py         _resolve_source_type() — url/jd_text/local_file
│   │                           inference, moved down from the former cli/_shared.py
│   ├── prompts.py             render() — Jinja2 loader over prompts/, fences
│   │                           untrusted JD/form text (moved from nodes/_prompts.py)
│   ├── pdf.py                 pdf_page_count() — generic PDF byte parsing (moved
│   │                           out of nodes/_cv_fit.py)
│   ├── redteam.py            shared red-team review (mmx CLI wrapper)
│   ├── tracker_ops.py        stats/verify/merge/dedup/normalize over the tracker
│   ├── outreach.py           contacts + outreach event lifecycle
│   ├── triage.py, screen.py, link_check.py  the leaves shortlist.py composes
│   ├── workday/               employer_config, application_questions,
│   │                          review_gate, required_empty, login, ...
│   ├── linkedin/               Easy Apply automation
│   ├── web/                    apply_ipc.py, apply_ops.py, apply_run_log.py,
│   │                           page_summary.py — the fill-only session's IPC
│   ├── email/                  gmail_client, poller, reconcile, summarize, gaps
│   └── llm/                    factory, minimax, local_command, traced, content,
│                                call.py (moved from nodes/_llm.py)
├── nodes/                     one file per (or a few) graph node(s)
│   ├── _cv_fit.py, _quality.py   private helpers (next_trim stayed in _cv_fit.py
│   │                              when pdf_page_count moved out — below)
│   ├── context.py, extract.py, eligibility_gate.py, classify.py
│   ├── evaluate.py             cv_match / role_summary / level_strategy /
│   │                            score_and_recommend
│   ├── research.py             company_comp_research
│   ├── personalize.py          personalization_plan / interview_prep /
│   │                            draft_application_answers / update_story_bank
│   ├── pdf.py                   tailor_cv / generate_cv_html_pdf / skip_pdf
│   ├── cover_letter.py          generate_cover_letter
│   ├── redteam.py               redteam_review node (calls services/redteam.py)
│   ├── report.py, tracker.py    write_report / write_tracker_addition /
│   │                            merge_or_update_tracker
│   ├── artifact_paths.py        run_output_dir() and friends
│   ├── apply_screen_assist.py   one-shot node for live-form answers, not part
│   │                            of the evaluate graph
│   └── human_review.py          apparently dead — see below
└── graphs/
    ├── evaluate_job.py         the one real graph (below)
    ├── scan_portals.py         7-line NotImplementedError placeholder
    └── email_ingest.py         7-line NotImplementedError placeholder
```

`scan_portals.py` and `email_ingest.py` are still stubs — each is a single
`def build_*_graph(): raise NotImplementedError(...)`, unchanged since the
last pass through this document. Scan and email ingestion both run as plain
service calls from `cli/discovery.py` / `cli/mail.py`
(`services/scan.py::scan_portals`, `services/email/poller.py` +
`reconcile.py`), not as LangGraph graphs.

### `nodes/human_review.py` — still apparently dead

`stop_before_submit()` is a LangGraph `interrupt()` node whose docstring
still says it's "used by apply_assistant graph." No such graph exists, and
grepping `job_hunt/`, `scripts/`, and `tests/` for `human_review` still finds
no importer anywhere, in `evaluate_job.py` or otherwise. Unchanged from the
last time this was checked — flagged here rather than deleted, since this
document only describes what is.

## `cli/`

One `typer.Typer()` root app plus ten sub-apps mounted with `add_typer`:
`config`, `trace`, `activity`, `tracker`, `schedule`, `email`, `llm`,
`review`, `contacts`, `outreach`, plus a `pipeline` app for `data/pipeline.md`
URL-inbox commands — all still wired in `cli/__init__.py`. Top-level commands
(`scan`, `evaluate`, `evaluate-batch`, `apply`, `apply-answers`,
`outreach draft`/`linkedin`, `research`, `project-eval`, `training-eval`,
`checkup`, ...) hang directly off the root app, spread across the nine
command files.

`apply.py` is still 5,171 lines and was deliberately left out of this split.
Everything else in `cli/` is a Typer command body calling into `services/`;
`apply.py` is mostly Playwright — a live browser page being driven through
Workday's and LinkedIn's multi-step forms, one function per form section
(`_fill_workday_my_experience`, `_workday_advance_all_steps`,
`_maybe_linkedin_easy_apply`, and around a hundred more). That code needs a
page-driving seam — something a test can stand in for the live DOM — before
it can be pulled apart the way the Workday *config* logic already was
(`services/workday/`, ADR-010). No such seam exists yet, and building one
that's actually trustworthy means verifying it against a live ATS session,
not just a mock. So it stayed in `cli/apply.py`, as one file, rather than
being split against an untested boundary.

## The one real graph: `evaluate_job.py`

`job-hunt evaluate` runs `graphs/evaluate_job.py::build_evaluate_job_graph()`,
a sequential `StateGraph` (checkpointed with `MemorySaver`) of 24 nodes:

```
load_context → extract_jd → verify_active ─(inactive)→ mark_unavailable → END
                                 └─(active)→ eligibility_gate
                                     ├─(blocked)→ mark_ineligible → END
                                     └─(eligible|unknown)→ classify_archetype
                                          → cv_match → role_summary → level_strategy
                                          → company_comp_research → personalization_plan
                                          → interview_prep → score_and_recommend
                                          → draft_application_answers → update_story_bank
                                          ├─(generate_pdf)→ tailor_cv → generate_cv_html_pdf ┐
                                          └─(skip)────────→ skip_pdf ──────────────────────┤
                                                                                             ↓
                                                                            generate_cover_letter
                                                                                    → redteam_review
                                                                                    → write_report
                                                                          → write_tracker_addition
                                                                          → merge_or_update_tracker
                                                                                    → END
```

Deliberately sequential (not fan-out), so a local/cheap-tier model is never
hit with concurrent calls — see `docs/design.md` §Evaluation Graph.

## `services/shortlist.py` and `services/batch.py`

`triage`'s composition and `evaluate-batch`'s orchestration used to live in
the bodies of those two commands. Both are now services, and both are worth
knowing exist, because each holds ordering logic that came from a real
incident and would silently break if reordered:

**`shortlist.py::build_shortlist()`** composes `triage.py`'s leaves (parse,
exclude, rank) with an optional LLM screen, optional link verification, and
an immigration lane, in that order. Five invariants, each with its own test
in `tests/test_shortlist.py`:

1. **Pool widening before ranking cuts.** With `--screen` or `--verify`, the
   pool ranked is wider than the limit asked for (`options.pool` for screen;
   `max(limit*4, 40)` for verify) — otherwise verification alone can drop 6
   of 10 candidates and hand back 4.
2. **Screen verdicts are keyed by 1-based position** in the ranked list, not
   by row identity — `screen()`'s return shape, and losing that pairing
   silently attaches the wrong verdict to the wrong row.
3. **The sort is `(-fit, -score)`** — model fit first, deterministic
   priority score only as the tie-break — never either key alone.
4. **The lane pool resets to verification's survivors.** The immigration
   lane must never resurrect a posting verification just killed.
5. **`SKIPPED` never folds into "nothing dead."** A host the link checker
   declines to fetch comes back `SKIPPED`, not clean — reporting it as clean
   is how a dead posting reached the shortlist once (2026-08-15).

**`batch.py::run_batch()`** is the semaphore-bounded concurrent runner behind
`evaluate-batch`: it gathers the evaluate graph over a target list, tracks
spend against a JSONL ledger (`usage_ledger.py`), and detects the case where
premium spend is happening but isn't being recorded (`unmeasurable` —
different from "$0 spent," and treating the two the same means the cap trips
late or never). It takes `graph_builder` as an injected callable rather than
importing `graphs/` itself — the layering fix noted above.

## `EmployerMatcher` and the `intent` parameter

`services/employer_match.py::EmployerMatcher` is the one place this system
decides two employer/role names mean the same tracker row. Before it existed
that decision was implemented six times at five different thresholds. It
takes an `intent`:

- **`intent="mutate"`** — "which row do I write to?" A false positive here
  corrupts a real application's record. This is the strict gate: a human has
  already identified the row (typed the company/role, confirmed a
  submission), and the match only has to confirm that identification.
- **`intent="report"`** — "does this employer appear anywhere?" A false
  negative here hides a sent application — the failure this project cares
  most about. This is the loose gate, folding in the alias/decoration
  fallbacks that used to live only in `email/gaps.py`.

Both gates sit at the same `MATCH_THRESHOLD = 0.70`. `services/email/reconcile.py`
layers its own stricter floor on top, via `is_reliable_match`
— an explicit opt-in for that one caller, not something `mutate` imposes on
everyone — because reconcile acts on inbound mail with nobody in the loop to
catch a bad match before it writes. This distinction was flattened once
during the work that built this module, and caught in review: worth keeping
in mind if `intent` ever looks like it could be collapsed to one threshold.

## To add a new job board, touch these files

This is the highest-frequency structural change in the codebase, and until
this document there was nowhere to look it up. Two different changes go by
that name — know which one you're making before you start:

**A. Add an employer to an existing tier** (most common — a new Greenhouse
company, a new Workday tenant, a new tracked company for tier 1–3):
edit `config/portals.yml` only. For tier 6 (Workday), paste any posting URL
from the employer's board into a `workday_boards.employers[].url` entry —
`resolve_workday_target()` in `services/workday_boards.py` extracts the
tenant/host/site triple; no code changes. No file list needed beyond the
config file itself, which is why it isn't covered further here.

**B. Add a new board *source*** (a genuinely new adapter — a new provincial
government site, a new aggregator API, a new ATS not yet supported): trace
the existing tier-4–7 adapters as the template. There are two shapes to
copy from, and which one depends on where the migration below stands.

Two of the five tiers — Workday and Adzuna — return a
`models.posting.SourceResult` (postings + `SourceHealth`) from a
`scan_<source>_source()` function; that is the shape a new adapter should
target. The other three —
Job Bank, gov boards, regional boards — still take a `stats: dict | None`
keyword and report through `services.scan._board_coverage_warnings`, because
converting them needs a prior refactor each: `gov_boards.py`'s board
registry is a dict literal closing over six locals 190 lines into
`scan_gov_boards`, and `regional_boards.py`'s `BOARDS` vtable dispatches
fetch through the string `"curl"` rather than a real seam. `jobbank.py` has
neither obstacle and is the next one worth converting. Until that happens,
`models.posting.from_row()` is still the single normalisation point all five
go through — every mapper, converted or not, builds its rows with it.

1. **`job_hunt/services/<new_source>.py`** (or add a parser function to an
   existing module, if the new board runs the same platform as one already
   supported — e.g. another SuccessFactors tenant belongs in
   `gov_boards.py` next to `ns_gov`/`nsha`/`wrha`, not in a new file). Needs
   at minimum: a `parse_<source>(...)` function that turns raw HTML/JSON
   into flat `dict[str, str]` rows, a `fetch_<source>_page(...)` that
   returns `""`/`{}` on any transport failure (never raises past this
   layer), and a `scan_<source>(...)` orchestrator. If following the
   `SourceResult` shape: return `SourceResult(postings=[...], health=...)`,
   building each posting through `models.posting.from_row()`. If following
   the interim `stats` shape (only while converting Job Bank/gov/regional):
   accept a `stats` dict and record `collected` / `errors` / `truncated` per
   board — this is what lets a quiet failure be reported as a failure
   rather than read as "no postings this week."
2. **Config location** — decide before writing: if it needs only
   enable/disable + query shaping, add a top-level key to
   `config/portals.yml` (a plain dict, no Pydantic model — this is what
   every tier-4–6 adapter does). If it needs credentials from the
   environment (an API key/secret pair), follow tier 7's shape instead: add
   a `*Config` class to `job_hunt/config/models.py`, register it on
   `Settings`, and read the credential env-var names from that config
   rather than hardcoding them (see `AdzunaConfig.app_id_env` /
   `app_key_env`).
3. **`job_hunt/services/scan.py`** — import the new `scan_<source>`
   (or `scan_<source>_source`) function; write a
   `_<source>_scanned_jobs(config, warnings=None)` wrapper following the
   existing five (`_jobbank_scanned_jobs`, `_gov_board_scanned_jobs`,
   `_regional_board_scanned_jobs`, `_workday_scanned_jobs`,
   `_adzuna_scanned_jobs`) — it maps rows onto `ScannedJob` (defined in this
   same file), reports sweep-level exceptions into `warnings` rather than
   propagating them, and (for the `stats`-shaped tiers) forwards
   `_board_coverage_warnings(stats)`. Add the wrapper's return value to the
   `extra_tiers` list inside `scan_portals()`. Decide whether the source
   already establishes an occupation (pass it through `_accept_jobs(...,
   require_positive=False)`, as tiers 4–7 do) or needs the ordinary
   positive/negative title filter.
4. **Update the tier docstring** in `scan_portals()` (the `Tier N: ...`
   comments above `extra_tiers`) and `docs/design.md`'s Discovery section —
   both describe every tier by name today, and both go stale the same way
   this document did before its last pass.
5. **`config/portals.example.yml`** (or `config/settings.example.yml` for a
   credentials-driven source) — add the new section here too. This is the
   only copy of the config shape that is actually checked into the repo;
   the real `portals.yml`/`settings.yml` are gitignored, so skipping this
   step leaves the source undocumented for anyone without the live config
   file.
6. **Tests** — a new `tests/test_<source>.py`, or an addition to the
   existing combined file `tests/test_new_source_tiers.py` if the source is
   small (that file already covers gov boards, Workday, Adzuna, and Job
   Bank together, parametrised across all five tiers for the
   stats/coverage-warning behaviour). Cover at minimum: the parser against
   a captured HTML/JSON fixture, the failure-reporting path (`stats` or
   `SourceHealth`) counting a transport failure rather than reading it as
   zero postings, and (if paginated) the truncation flag when the page
   budget runs out mid-board.

No other layer needs touching: nothing downstream of `ScannedJob` branches
on which portal/board a job came from (`portal` and `source` are free-text
fields used only for display and dedup provenance), so scoring, triage, and
the tracker need no changes for a new source.
