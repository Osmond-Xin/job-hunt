# Module Map

Where things live, and what owns them. `docs/design.md` is organised by
feature; this document is organised by module path. Written for someone who
knows the domain (job applications, ATS, tracker rows) but has never opened
this repo.

`job_hunt/` is ~24,600 lines. `cli.py` alone is 7,999 of them — nearly a
third of the package. The package has zero circular imports (checked by
walking the import graph, not asserted).

## Layers

```
models/  ←  repositories/  ←  services/  ←  nodes/  ←  graphs/  ←  cli.py
(types)     (persistence)     (the work)    (graph     (LangGraph   (commands)
                                             steps)      wiring)
```

Each arrow points from what is depended on to what depends on it. In
principle nothing left of the arrow should import anything right of it.

- **`models/`** — Pydantic models and the one `TypedDict` graph state
  (`state.py`). No I/O.
- **`repositories/`** — markdown (`tracker_repo.py`, for
  `data/applications.md`) and JSONL (`email_event_repo.py`,
  `email_decision_repo.py`, `review_repo.py`) persistence. No LLM calls, no
  business logic.
- **`services/`** — the actual work: scanning, scoring inputs, Workday
  automation, email reconciliation, outreach, redteam review. Where new
  logic almost always belongs.
- **`nodes/`** — thin LangGraph node functions. Each one reads `JobHuntState`,
  calls into `services/`, and returns a partial-state dict.
- **`graphs/`** — wires nodes into a `StateGraph` and compiles it.
- **`cli.py`** — Typer commands. Orchestrates graphs and services; owns no
  business logic of its own (though it accumulates plenty — see below).

### Layering leaks

The layering above is the intended shape, not a fully accurate description.
Three places invert it, and they should be read as known issues, not as
evidence the diagram is wrong:

- **`nodes/_prompts.py`** is a private helper (`_`-prefixed) of `nodes/`, but
  it is imported by 11 modules, two of them in `services/`:
  `services/compare_offers.py` and `services/email/summarize.py`. Both need
  is only prompt rendering — pure infrastructure — so `_prompts.py` belongs
  in `services/llm/` or a new `job_hunt/prompts.py`, not inside `nodes/`.
- **`cli.py`** imports `LLM_FAILURE_MARKER` from the private `nodes/_llm.py`
  (`from job_hunt.nodes._llm import LLM_FAILURE_MARKER`, used to detect
  degraded rows in `tracker dashboard`-style output). Same shape: a constant
  that has nothing to do with graph nodes, sitting in the wrong package.
- **`services/redteam.py`** does a local import of `pdf_page_count` from
  `nodes/_cv_fit.py` (inside `artifact_text()`, only for PDF artifacts) —
  a third instance of `services/` reaching into `nodes/`'s private helpers.
- **`models/state.py`** imports `TrackerEntry` from
  `repositories/tracker_repo.py` — the reverse of the stated
  `models ← repositories` direction. `JobHuntState.tracker_entry` needs the
  type, and nothing has split it out of the repository module, so the
  lowest layer currently depends on the persistence layer above it.

None of these are urgent; none are cycles (the import graph is still
acyclic — `_llm.py`, `_prompts.py` and `_cv_fit.py` don't import back into
`services/`, and `tracker_repo.py` doesn't import `models/state.py`). They
are just packages holding code that belongs one level over.

### Highest fan-in (most-imported modules)

| Module | Imported by |
|---|---|
| `models/state.py` | 18 modules |
| `repositories/tracker_repo.py` | 12 modules |
| `nodes/_prompts.py` | 11 modules |
| `config/models.py` | 10 modules |

Anything touching these files' public shape (state keys, `TrackerEntry`
fields, `render()`'s call signature, `Settings`) should be grepped for
callers before the signature changes, not after.

## `job_hunt/` package layout

```
job_hunt/
├── cli.py                  7,999 lines — every `job-hunt <command>`
├── models/                 types only
│   ├── state.py            JobHuntState (the graph's TypedDict)
│   ├── job.py               JobMeta, ArchetypeResult, CandidateProfile
│   ├── evaluation.py         EvaluationScores, PdfContent
│   ├── review.py             review-queue item shapes
│   └── events.py             ActivityEvent and friends
├── repositories/
│   ├── tracker_repo.py      data/applications.md read/write/lock
│   ├── email_event_repo.py   data/*.jsonl for Gmail-derived events
│   ├── email_decision_repo.py approve/ignore decisions on review items
│   └── review_repo.py        the review queue itself
├── config/
│   └── models.py            Settings (config/settings.yml) — LlmConfig,
│                             WebSearchConfig, AdzunaConfig, etc.
├── services/                 ~30 top-level modules + 5 subpackages
│   ├── scan.py               the 7-tier scan orchestrator (below)
│   ├── gov_boards.py, regional_boards.py, jobbank.py, adzuna.py,
│   │   workday_boards.py     tier 4–7 board adapters (docs/design.md
│   │                         §Discovery has the per-source detail)
│   ├── redteam.py            shared red-team review (mmx CLI wrapper)
│   ├── tracker_ops.py        stats/verify/merge/dedup/normalize over the tracker
│   ├── outreach.py           contacts + outreach event lifecycle
│   ├── triage.py, screen.py, link_check.py  pre-evaluation filtering
│   ├── workday/               employer_config, application_questions,
│   │                          review_gate, required_empty, login, ...
│   ├── linkedin/               Easy Apply automation
│   ├── web/                    apply_ipc.py, apply_ops.py, apply_run_log.py,
│   │                           page_summary.py — the fill-only session's IPC
│   ├── email/                  gmail_client, poller, reconcile, summarize, gaps
│   └── llm/                    factory, minimax, local_command, traced, content
├── nodes/                     one file per (or a few) graph node(s)
│   ├── _prompts.py, _llm.py, _quality.py, _cv_fit.py   private helpers
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
│   └── human_review.py          apparently dead — see below
└── graphs/
    ├── evaluate_job.py         the one real graph (below)
    ├── scan_portals.py         7-line NotImplementedError placeholder
    └── email_ingest.py         7-line NotImplementedError placeholder
```

`scan_portals.py` and `email_ingest.py` are not partial implementations —
each is a single `def build_*_graph(): raise NotImplementedError(...)`. Scan
and email ingestion both run as plain service calls from `cli.py`
(`services/scan.py::scan_portals`, `services/email/poller.py` +
`reconcile.py`), not as LangGraph graphs. Two of the three files in
`graphs/` are stubs; only `evaluate_job.py` is real.

### `nodes/human_review.py` — apparently dead

`stop_before_submit()` is a LangGraph `interrupt()` node whose docstring
says it's "used by apply_assistant graph." No such graph exists (`graphs/`
has only the three files above), and grepping `job_hunt/`, `scripts/`, and
`tests/` for `human_review` finds no importer anywhere. It is not wired into
`evaluate_job.py` either. Treat it as dead code from an abandoned direction,
not as live infrastructure — flagged here rather than deleted, since this
document only describes what is.

## `cli.py`

One `typer.Typer()` root app plus ten sub-apps mounted with `add_typer`:
`config`, `trace`, `activity`, `tracker`, `schedule`, `email`, `llm`,
`review`, `contacts`, `outreach`, plus a `pipeline` app for `data/pipeline.md`
URL-inbox commands. Top-level commands (`scan`, `evaluate`,
`evaluate-batch`, `apply`, `apply-answers`, `outreach draft`/`linkedin`,
`research`, `project-eval`, `training-eval`, `checkup`, ...) hang directly
off the root app. `cli.py` is where Workday-specific orchestration
(`_workday_*` helpers) also lives, split from the pure-Python pieces that
moved to `services/workday/` under ADR-010 — the orchestration that still
needs a live Playwright page stayed behind.

## The one real graph: `evaluate_job.py`

`job-hunt evaluate` runs `graphs/evaluate_job.py::build_evaluate_job_graph()`,
a sequential `StateGraph` (checkpointed with `MemorySaver`) of ~20 nodes:

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
the existing tier-4–7 adapters (`services/gov_boards.py`,
`services/regional_boards.py`, `services/jobbank.py`, `services/adzuna.py`,
`services/workday_boards.py`) as the template, and touch, in order:

1. **`job_hunt/services/<new_source>.py`** (or add a parser function to an
   existing module, if the new board runs the same platform as one already
   supported — e.g. another SuccessFactors tenant belongs in
   `gov_boards.py` next to `ns_gov`/`nsha`/`wrha`, not in a new file). Needs
   at minimum: a `parse_<source>(...)` function that turns raw HTML/JSON
   into flat `dict[str, str]` rows, a `fetch_<source>_page(...)` that
   returns `""`/`{}` on any transport failure (never raises past this
   layer), and a `scan_<source>(config, *, client=None, sleep=time.sleep,
   stats=None) -> list[dict[str, str]]` orchestrator that accepts a
   `stats` dict and records `collected` / `errors` / `truncated` per board —
   this is what lets a quiet failure be reported as a failure rather than
   read as "no postings this week" (see `_board_coverage_warnings` in
   `services/scan.py`).
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
   function; write a `_<source>_scanned_jobs(config, warnings=None)`
   wrapper following the existing four (`_jobbank_scanned_jobs`,
   `_gov_board_scanned_jobs`, `_regional_board_scanned_jobs`,
   `_workday_scanned_jobs`, `_adzuna_scanned_jobs`) — it maps raw rows onto
   `ScannedJob` (defined in this same file), reports sweep-level exceptions
   into `warnings` rather than propagating them, and forwards
   `_board_coverage_warnings(stats)`. Add the wrapper's return value to the
   `extra_tiers` list inside `scan_portals()`. Decide whether the source
   already establishes an occupation (pass it through `_accept_jobs(...,
   require_positive=False)`, as tiers 4–7 do) or needs the ordinary
   positive/negative title filter.
4. **Update the tier docstring** in `scan_portals()` (the `Tier N: ...`
   list at the top of the function) and `docs/design.md`'s Discovery
   section — both describe every tier by name today, and both go stale the
   same way `design.md` already had before this pass.
5. **`config/portals.example.yml`** (or `config/settings.example.yml` for a
   credentials-driven source) — add the new section here too. This is the
   only copy of the config shape that is actually checked into the repo;
   the real `portals.yml`/`settings.yml` are gitignored, so skipping this
   step leaves the source undocumented for anyone without the live config
   file.
6. **Tests** — a new `tests/test_<source>.py`, or an addition to the
   existing combined file `tests/test_new_source_tiers.py` if the source is
   small (that file already covers gov boards, Workday, and Adzuna
   together). Cover at minimum: the parser against a captured HTML/JSON
   fixture, `scan_<source>` counting a transport failure into `stats`
   rather than reading it as zero postings, and (if paginated) the
   truncation flag when the page budget runs out mid-board.

No other layer needs touching: nothing downstream of `ScannedJob` branches
on which portal/board a job came from (`portal` and `source` are free-text
fields used only for display and dedup provenance), so scoring, triage, and
the tracker need no changes for a new source.
