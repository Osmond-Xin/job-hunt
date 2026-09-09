# Giving `cli/apply.py` a seam

**Status:** accepted. Five reviews: agy (RETHINK on v1), codex
(REVIEW_NEEDED on v2, then on v3), a `codebase-design` vocabulary pass, and
opencode (RETHINK on v2, then "no blockers, structurally sound" on v3).
**Date:** 2026-09-09

§8 records what was rejected and why. Numbers below were re-counted, not carried
over: **42** `_*workday*` functions (not 43), **268** ruff findings of which
**239** are in `cli/__init__.py` leaving **29** real (not 227/21), **43**
`console.print` inside `_open_apply_page` (113 file-wide), **35** functions with
no Playwright coupling totalling **701** lines.

## 1. The problem

**(a)** The layer check passes while the layering is wrong. `cli/` is the top
layer so it may import anything; an AST walk of 119 modules finds zero
inversions. Business logic *lives* in `cli/` rather than being *called* from it:
4,820 of 5,197 lines are function bodies, only 530 are Typer commands.

**(b)** The Workday step machine cannot be tested without a browser. ADR-010
carved four Workday concerns into `services/workday/` (1,432 lines, 55 tests, no
Playwright) and stopped short: 42 functions, 1,813 lines, stayed in `cli/`.

**(c)** There is a driver seam and Workday is on the wrong side of it.
`services/linkedin/easy_apply.py` returns a structured result; `detect.py` splits
pure URL probes from Playwright-aware ones. Workday has neither, so
`_open_apply_page` hand-rolls the flow inline.

**(d)** ATS detection is a string literal repeated **13 times**
(`"myworkdayjobs.com" in page.url`). There is no `services/workday/detect.py`.

## 2. What success means

**Behavioural:** adding a third ATS requires a new module and one registry entry,
and no edit to `cli/apply.py` or to `apply_session.py`'s body. Dispatch is by
URL/page, so no new Typer subcommand is involved.

**Honest accounting of the payoff (deletion test):** `AtsDriver` has exactly one
caller today. The return is **locality** — Workday knowledge in one place, 13
literals collapsed to one — not **leverage** across many callers. The
third-ATS benefit is future, and is claimed as such.

## 3. The design

### 3.1 Contracts, drivers, registry — three modules, one direction

A single `ats_driver.py` that both defines the protocol and imports the concrete
drivers to build `DRIVERS` is an import cycle. Split it:

```
services/web/ats_contract.py     protocol + AtsResult + ApplyContext.  Imports nothing local.
services/workday/driver.py       imports ats_contract
services/linkedin/driver.py      imports ats_contract
services/web/ats_registry.py     imports both drivers + ats_contract. Imported only by the session.
```

### 3.2 Fill and submit are separate calls

v2 gave the driver one `run()` that could return `OUTCOME_SUBMITTED` while also
promising a single shared gate. Both cannot hold. **Policy in the session, DOM
knowledge in the driver:**

```python
class AtsDriver(Protocol):
    name: str
    def matches_url(self, url: str) -> bool: ...       # pure, pre-navigation
    async def matches_page(self, page) -> bool: ...     # post-navigation, authoritative
    async def fill(self, page, ctx: ApplyContext) -> AtsResult: ...   # never clicks final submit
    async def submit(self, page, ctx: ApplyContext) -> SubmitOutcome: ...
```

`SubmitOutcome` is not a `bool`. An ATS can accept the click while the
confirmation navigation times out, and `False` would conflate "never clicked",
"validation rejected" and "possibly submitted" — the last of which must never be
retried blindly against a real employer:

```python
class SubmitOutcome(BaseModel):
    state: Literal["confirmed", "rejected", "unknown"]
    evidence: str          # confirmation URL/text, or why it is unknown
```

`unknown` blocks both recording and a second attempt. A returned value cannot
enforce that on its own — the process exits and the next `job-hunt apply` knows
nothing — so the attempt is persisted: `submit.attempted` goes into the run
log **before** the click and `submit.resolved` after it, both carrying the
application identity already used for the artifact directory. A run that finds
an `attempted` with no `resolved` for the same identity refuses to click and
says which artifact directory to check. Only a human confirming the outcome —
or an inbound acknowledgement email reconciled against the row — clears it.
Reconciliation is a step the operator takes, not one the code guesses at,
because the failure it guards against is a second real application to the same
employer.

**URL matching is a hint; page matching is authoritative** and is re-evaluated
after navigation, because vanity domains redirect into Workday tenants. This is
what collapses the 13 literals into `WorkdayDriver.matches_page()`.

### 3.3 `AtsResult` carries only what both ATSs have

```python
class AtsResult(BaseModel):
    outcome: str                                       # FILLED | LOGIN_REQUIRED | BLOCKED
    filled: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    answers: list[dict[str, str]] = Field(default_factory=list)
    required_empty: list[str] = Field(default_factory=list)
    uploads: list[str] = Field(default_factory=list)   # replaces attached/cover_letter_attached
    blockers: list[Blocker] = Field(default_factory=list)
```

v2 put `validation_issues: list[ReviewIssue]` here. `ReviewIssue` is defined in
`services/workday/review_gate.py` and used by nothing but Workday; LinkedIn's
`EasyApplyResult` has no such field. Putting it in the shared contract forces
`LinkedInDriver` to import a Workday type it never populates. `Blocker` is the
ATS-neutral shape (`code`, `message`, `details`); Workday's review gate maps
`ReviewIssue` into it.

### 3.4 The session accepts its browser

`_open_apply_page` calls `async_playwright()` inside itself, so it is untestable
by construction. `apply_session.py` takes a context factory and a `reporter`.
The repo already does this: `easy_apply.py` takes its ten page helpers as
injected callables.

The reporter is a protocol, not a bare callable — the 43 calls it replaces carry
severity, and some iterate lists:

```python
class Reporter(Protocol):
    def info(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def bullets(self, title: str, items: list[str], limit: int | None = None) -> None: ...
```

`cli/` passes a Rich-backed one; tests pass a recording one.

**Which of the 113 `console.print` calls this covers**, counted per function so
no group is left unassigned:

| Where | Calls | What happens to them |
|---|---:|---|
| the 10 Typer commands | 55 | stay — `cli/` is where printing belongs |
| `_open_apply_page` | 43 | become `reporter` calls |
| Playwright-free helpers | 8 | return a decision; the command prints it (§3.7 Phase 1's purity rule) |
| other session helpers | 6 | become `reporter` calls with the session |
| the 42 Workday functions | 1 | returns into `AtsResult.skipped`, like its neighbours |

The point of the count: over half of the printing is already in the right place.
Only 15 calls need a decision, and it is the same decision each time — a service
returns what happened, and `cli/` renders it.

### 3.5 Target layout

| Destination | Lines | What |
|---|---:|---|
| `services/workday/` — into the **five existing step modules**, plus `detect.py`, `driver.py` | 1,813 | the 42 `_*workday*` functions |
| `services/web/apply_session.py` | 1,090 | `_open_apply_page` + the helpers only it uses |
| `services/apply/` — `reporting.py`, `answers.py`, `linking.py` | 701 | the 35 Playwright-free functions |
| `services/web/form_fill.py` | 426 | ATS-agnostic DOM primitives |
| `services/linkedin/` | 283 | the 11 `_linkedin_*` helpers |
| **`cli/apply.py`** | **≤700** | the 10 Typer commands + imports |

v2 said one `steps.py` would take 1,813 lines — larger than the file ADR-010 was
written to break up. The five step modules
(`my_information`, `my_experience`, `application_questions`,
`voluntary_disclosures`, `review_gate`) already exist and already name the steps.

**Budget, honestly:** 530 command lines + 377 module-level lines = 907 before any
import shrinks, so ≤700 is reachable but only if `apply_assist` (194 lines of
gate/tracker/artifact logic, not a thin wrapper) sheds its logic into
`services/apply/`. That is Phase 1's job, and it is a refactor of those bodies,
not only a move.

### 3.6 Dependency closure decides the phase order

The 42 Workday functions reference **11 names that would stay behind** — this is
measured, not assumed:

- Phase 1 (pure): `_answer_for_application_question`
- Phase 2 (DOM primitives): `_force_fill_by_accessible_label`,
  `_fill_by_label_or_placeholder`, `_required_empty_fields`, `_field_contains_text`,
  `_fill_contenteditable`, `_field_context`
- **Scheduled by an earlier draft for the session phase, but required by Workday**: `_enter_application_form`,
  `_advance_application_start`, `_attach_resume`, `_finish_pending_upload_dialog`

Those last four are page-entry/upload primitives, not session orchestration.
**They move in Phase 2**, or Phase 3a would have to import them back out of
`cli/` — a layer inversion. This is the single concrete ordering bug the reviews
found, and it is fixed by re-labelling those four.

**What the 42 are, since it changes what Phase 3a is worth.** They are
*coordinators*, not duplicate implementations: `apply.py` references each of the
five step modules ADR-010 created between five and eight times, and those modules
hold the real per-step logic. So Phase 3a puts orchestration next to the
implementation it already calls — it does not discover a half-finished carve with
dead modules on one side. The payoff is locality, as claimed in §2, and no more
than that. (39 of the 42 are `async`; the 3 sync ones are pure predicates. No
sync-Playwright hazard anywhere in the move.)

### 3.7 Phases

0. **Make `ruff` a gate.** 268 findings, 239 of them deliberate re-exports in
   `cli/__init__.py`. Add `__all__`, fix the 29 real ones, then gate. Re-run at
   every later phase boundary.
1. **Pure logic out** → `services/apply/`. The 35 Playwright-free functions
   (701 lines), plus the gate/tracker bodies inside `apply_assist`.
   "Pure" here means **no Playwright and no console** — `_enforce_low_score_gate`
   prints, so it returns a decision and the command prints it.
   Characterisation tests written first, against current behaviour.
2. **DOM primitives out** (426 lines + the four page-entry helpers from §3.6)
   → `services/web/form_fill.py`. **Updates the two monkeypatches that break
   here** (§3.8).
3. **a. Workday transplant, verbatim** → the five step modules + `detect.py` +
   `driver.py`. `git diff -M` shows renames. No testability claimed.
   **b. Internal seam — gated on a spike (§3.9).** Extract step *decisions* into
   pure functions over a snapshot. These are an **internal seam**: private to the
   driver's implementation, used by its own tests. The session must never import
   them; the driver's interface stays `fill`/`submit`.
4. **LinkedIn conforms — including splitting `easy_apply.py`.** Today
   `easy_apply.py:449` sets `OUTCOME_SUBMITTED` and `submitted = True` *inside*
   its fill path: it clicks Submit itself. `LinkedInDriver.fill()` must not, so
   the submit tail moves into `LinkedInDriver.submit()`. `EasyApplyResult` stops
   being a public shape and becomes LinkedIn-internal, mapped to `AtsResult` at
   the driver's edge. This is a scoped piece of work, not a rename.

   **This is now Phase 4, ahead of the session.** Two reviewers caught
   independently that the earlier order had Phase 4's registry importing a
   `LinkedInDriver` that only became protocol-conforming in Phase 5 — the same
   dependency-closure bug §3.6 fixes for Workday, one layer up. The session may
   not be built on a driver that does not yet exist in its final shape, and a
   transitional non-conforming wrapper would be a second orchestration path
   nobody wants to review. So LinkedIn conforms first.

5. **Session out + gate consolidation** → `apply_session.py`, taking the context
   factory, the reporter and the registry. `_open_apply_page` becomes launch →
   select driver → fill → gate → maybe submit → report.

   **This is the phase that rewrites the auto-submit gate**, and the gate
   characterisation tests land before *this* commit. They pin two things, not
   one, because an event assertion alone would pass on a run that emitted the
   right event and clicked anyway:
   - **Events**: `apply_run_log.emit("auto_submit.gated", reason=…)` for
     `non_workday_host`, `review_validation_issues`, `required_empty_fields`,
     `submit_button_not_found`, `linkedin_review_not_reached`.
   - **Effects**: each authorisation key independently prevents the click;
     every blocking result prevents the click; neither driver's `fill()` ever
     clicks a final Submit whatever the context; an authorised submit happens at
     most once; `unknown` neither records an application nor retries.

   The three upstream keys (CLI flag, `profile.yml`, `mode == "full"`) are
   *preconditions*, not gate reasons — they are evaluated in `apply_assist`
   before the gate runs, and emit `auto_submit.bypassed`, so the two kinds of
   refusal stay distinguishable in the log and in the assertions.

   **The three-key calculation is protected before Phase 1**, not before this
   phase: it lives in `apply_assist` (`apply.py:129`), whose body Phase 1
   partially moves. Phase 1's "characterisation tests first" is not generic
   there; that calculation is named as its first obligation.

### 3.8 What the tests reach into, per phase

Two kinds of reach, both measured rather than guessed (AST walk of `tests/` for
patch targets; identifier search for imports).

`monkeypatch.setattr` into `job_hunt.cli.apply` — four targets:

| Target | Defined where | Breaks in |
|---|---|---|
| `load_settings` | `config/models.py`, re-imported | never — the import stays |
| `MATCH_THRESHOLD` | `services/employer_match.py`, re-imported | never |
| `_field_context` | `apply.py:4667`, takes a locator | **Phase 2** |
| `_field_contains_text` | `apply.py:4159`, takes a locator | **Phase 2** |

Direct imports of Workday helpers — **six**, not the zero a reviewer guessed:
`_workday_required_blocks_my_information_continue`,
`_write_workday_my_experience_debug`, `_workday_experience_dates_match`
(`test_workday_step_helpers.py`), `_workday_resume_was_uploaded`,
`_fill_workday_textarea_answers` (`test_apply_assist.py`),
`_fill_workday_voluntary_disclosures` (`test_workday_voluntary_disclosures.py`).
All six break in **Phase 3a** and are repointed in its commit.

No alias is created for any of them: an alias would leave a patch bound to a
name the moved code no longer calls, and the test would pass while the mock sat
inert.

### 3.9 Phase 3b is gated on a spike, not asserted

Two reviews flagged that "extract the step decisions into pure functions" is
asserted rather than shown, and that the DOM reads and the decisions are
interleaved. They are right that it is unproven. So it is proven before it is
promised:

> **Spike:** split `_fill_workday_current_step` (96 lines, the smallest real
> candidate) into a snapshot reader and a pure decision function. It passes only
> if the pure half **reproduces the current behaviour** on snapshot cases taken
> from a real page — not merely if it is short and dict-testable. A shape that
> compiles proves nothing about a step machine.
>
> A pass licenses extraction **only for functions that fit the same shape**,
> assessed one at a time. It is not blanket approval for all 42. If the spike
> fails, **Phase 3b is descoped** and the plan records which functions resist
> separation and stay browser-only — which is an acceptable outcome, not a
> failure of the refactor.

Phase 3a delivers value (locality, one detect module, ADR-010 finished) whether
or not 3b proves out. Nothing downstream depends on 3b.

### 3.10 Branch and rollback

One branch, `apply-driver-seam`; one commit per phase; one PR. Not five PRs into
`main` — a half-disassembled `apply.py` on `main` between phases is worse than
either end state.

**Rollback is the PR, not the commit.** v2 promised each phase independently
revertable; that is false once Phase 5 imports what Phases 1–4 moved. What is
promised instead: each commit leaves the suite green, so `git bisect` finds a
regression's phase, and the revert unit is the merge.

## 4. Alternatives

- **Leave it.** It passed 5,000 lines once before (ADR-010 records `cli.py` at
  5,800). Deferring reproduces the state ADR-010 escaped.
- **Split by size** (`apply_part1/2/3.py`). Fixes the number, not the seam.
- **Workday only, no protocol.** Cheaper and defensible; leaves two hand-rolled
  flows and two gates, and the third ATS still lands in `cli/`.

## 5. Risks

- **The gate rewrite (Phase 5) is the real exposure**, not the live run.
  Mitigation: characterisation tests on the structured gate events land before
  Phase 5; the live verification run is done with `profile.yml`
  `auto_submit_enabled` off, so a gate regression cannot reach a click.
- **Tenant variance.** One live Workday posting proves one tenant. Save its DOM
  snapshot as a fixture so the second tenant costs a fixture, not a browser.
- **Silent mock decoupling.** Four targets, two of which break, both in Phase 2 —
  handled in §3.8.
- **No CI, no type checker.** Phase 0 restores lint. mypy stays out: a
  type-annotation sweep inside a 4,800-line move makes the diff unreviewable.

## 6. Success criteria

1. `cli/apply.py` ≤ 700 lines, Typer commands and imports only.
2. `"myworkdayjobs.com"` appears in exactly one module.
3. Workday and LinkedIn reached through one protocol; one auto-submit gate.
4. `pytest -q --ignore=interview-prep` green at every phase boundary (1,027 now).
5. `ruff check job_hunt` clean at every phase boundary.
6. No **rewritten** function over 120 lines. Functions carried across verbatim
   in Phase 3a are exempt, and so is any browser orchestration the §3.9 spike
   says should stay whole — otherwise this criterion would quietly force the
   decomposition that the spike exists to decide against, and the fallback
   would not be a real fallback. Named exemptions at the end:
   `_workday_advance_all_steps`, plus whatever the spike records.
7. Phase 3b: either pure step-decision tests exist with no Playwright, or the
   spike's negative result is recorded and 3b is descoped.
8. A synthetic third driver registers and dispatches through the unchanged
   session — the only real test of criterion §2's behavioural claim.
9. One live `--fill-only` run completes, `auto_submit_enabled` off.
10. ADR-017 records the decision and the alias sunset rule.

## 7. Alias sunset rule (owed since ADR-010)

> A compatibility alias exists to keep a **test** importing during one refactor.
> It is deleted in the same PR that updates the last test importing it. No alias
> survives the PR that created it. An alias is never created for a name a test
> monkeypatches — patching an alias silently disarms the mock.

## 8. Rejected findings, with evidence

- **"`= []` shares state across Pydantic instances."** False for Pydantic v2,
  which deep-copies defaults. Measured on the installed `pydantic 2.13.4`:
  two instances, append to one, `a.xs is b.xs` → `False`. `default_factory`
  adopted as house style; it fixes nothing.
- **"Audit sync vs async Playwright before freezing the protocol."**
  `easy_apply.py` is async throughout (8 `async def`); every CLI entry uses
  `asyncio.run`. No sync Playwright in the package.
- **"32 test files monkeypatch into `cli.apply`."** 32 is the count of files
  *mentioning* apply. Actual `monkeypatch.setattr` targets: four (§3.8).
- **"`--fill-only` can fire a real submission."** Fill-only and auto-submit are
  separate paths; auto-submit is already gated three ways. Kept in corrected
  form: the exposure is the Phase 5 gate rewrite.
- **"The ≤600-line budget is off by ~900 lines; the file can only reach ~3,500."**
  Measured: 530 command lines + 377 module-level lines = 907 maximum before
  imports shrink with the code that leaves. Budget set to ≤700, and §3.5 states
  what has to happen to `apply_assist` for it to hold.
- **"A third ATS needs a new Typer subcommand, so `cli/apply.py` must be
  edited."** Dispatch is by URL and page, not by subcommand: `job-hunt apply
  <url>` already routes LinkedIn and Workday through one command. A third ATS
  adds no subcommand.
