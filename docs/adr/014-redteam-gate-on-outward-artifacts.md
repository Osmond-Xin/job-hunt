# ADR-014: Red-team review gate on every outward-facing artifact

**Status:** Accepted
**Date:** 2026-08-13

## Context

Nothing checked the factual claims in a generated résumé or cover letter
before it reached an employer. Two defects reached submitted PDFs before
this existed: a lapsed certification written in the present tense, and a
"province-scale data platforms" claim nothing in the candidate's actual
history supported. The model that wrote the draft has no standing to catch
either — it produced the claim in the first place, and a model asked to
critique its own output tends to defend it.

`CLAUDE.md` §1 names résumés, cover letters, outreach emails, and
application-form answers in the same sentence as artifacts that must clear
this gate. Only two of the four ever did: `apply-answers` printed premium-
tier output straight to the console, and `outreach draft` wrote a file to
`data/outreach-drafts/`, both unreviewed. That gap sat for three weeks
before `f40add8` closed it.

## Decision

`job_hunt/services/redteam.py` runs a shared review through the `mmx` CLI —
`MiniMax-M3`, a different model family from whatever wrote the artifact —
in three passes, in a fixed order:

1. **Facts**, checked against `profile/redteam-facts.md` (the ground-truth
   ledger of what the candidate can actually claim).
2. **Targeting**, checked against the job description.
3. **HR read** — a cold pass with no ground truth, reacting to the page as
   a 40-second recruiter screen would.

Ending in one line: `VERDICT: BLOCK / REVISE / SEND — <reason>`. The
reviewer reads text extracted from the PDF (`pdftotext -layout`, plus page
count) and is told which template produced the artifact
(`ORIGINS["pipeline"]` vs `ORIGINS["hand-written"]`), since the two carry
different formatting invariants and conflating them produced five false
BLOCKs in one day before that distinction existed.

Two call sites, both landing on the same `run_review()`:

- `nodes/redteam.py::redteam_review` — the graph node, run after
  `generate_cv_html_pdf` / `generate_cover_letter` and before `write_report`
  in `evaluate_job.py`. The verdict is written to `redteam.md` in the run
  directory and surfaced at the top of the report.
- `cli.py::_gate_outward_artifact` — added 2026-09-03 (`f40add8`) to close
  the `apply-answers` / `outreach draft` gap. Writes the review beside its
  artifact as `<stem>.redteam.md` rather than the pipeline's plain
  `redteam.md`, because a CLI output directory can already hold an
  unrelated one (an apply-answers file lands in the same `output/<run>/`
  directory as a CV that has its own review).
- `scripts/redteam.py` runs the same three passes over hand-written
  artifacts (government-competition submissions, mostly), which is most of
  what actually gets sent for those channels.

`UNREVIEWED` — `mmx` unreachable, a timeout, no verdict line in the reply —
is a distinct state from a pass at every one of these call sites, and is
printed as "not a pass," never silently treated as clean.

BLOCK is loud, not destructive: the reviewer has no veto over the artifact.
It does not delete the draft, does not withhold the PDF, does not block the
CLI command from finishing. The operator adjudicates every BLOCK against
the source before acting on it, because the reviewer reads extracted PDF
text and produces false positives (the origin-tagging above exists because
of one such class of them).

## Consequences

- Every outward-facing artifact this repo can produce — pipeline-generated
  or hand-written, résumé/letter or answers/outreach — runs through one
  rubric and one ground-truth file, so the standard cannot drift between
  code paths.
- `UNREVIEWED` being a real, printed state means "the reviewer was down"
  can never be read back later as "this passed." A missing `mmx` binary
  fails loud, not quiet.
- The reviewer has no authority to stop a send; a human always adjudicates
  BLOCK. This is a deliberate trade — false positives are cheap to overrule,
  but an artifact silently withheld because of a misread PDF would be worse
  than the defect it was trying to catch. (Compare ADR-013's amendment,
  which chooses the opposite trade for the *quality* audit: that gate does
  withhold, because its failure mode is regeneration cost, not a human
  losing visibility into a false block.)
- Cost: every artifact-producing run and every `apply-answers` /
  `outreach draft` invocation now makes an extra reasoning-model call
  (`DEFAULT_MAX_TOKENS = 12000`, ~600s timeout budget). Accepted per the
  same "trade tokens for quality" mandate as ADR-013.
