# ADR-013: Quality-audit regeneration loop + reasoning-model token budget

**Status:** Accepted (amended 2026-07-27 — see Amendment below)
**Date:** 2026-06-12

## Context

Two problems surfaced during live evaluation runs in June 2026, and they are
entangled because they share the same root cause: the cheap-tier model became a
**reasoning model** (MiniMax-M3) whose hidden reasoning consumes the completion
budget before any visible content is emitted.

1. **Silent quality degradation.** With node-level `max_tokens` (900–3200), M3
   spent the budget reasoning and returned empty or truncated output. The
   pipeline degraded silently — a score parse failure became a `0.0` skip, and a
   `tailor_cv` ReadTimeout (the 180s default was too short for a 5-minute M3 CV
   rewrite) fell back to the untailored master CV without surfacing why.
2. **No guard on artifact quality.** Even when generation succeeded, nothing
   checked the tailored CV / cover letter against the hard framing rules
   (no "X+ years" self-labels, honest framing on domain gaps, project pruning).
   The operator's mandate is explicit: **trade tokens for quality** — quality
   always wins over token cost.

## Decision

### 1. Reasoning-model handling (`services/llm/minimax.py`)

- `+8000` token headroom for `MiniMax-M3*` models (raised from the initial
  `+4000` after live CV-rewrite starvation — see commit `a0fbce3`).
- One doubled-budget retry on `finish_reason=length`; loud `RuntimeError` if the
  output is still truncated rather than a silent degraded fallback.
- Cheap-tier `timeout_seconds: 420` in `config/settings.yml` (M3 CV rewrites run
  5+ minutes; the 180s default caused the untailored-fallback regression).

### 2. Generate → audit → regenerate loop (`nodes/_quality.py` + `prompts/evaluate/quality_audit.md`)

- Every tailored CV and cover letter is reviewed by a second LLM pass against
  the hard framing rules.
- A deterministic tenure self-label regex gates *before* the LLM audit (cheap
  fail-fast on the most common violation).
- Failing drafts are regenerated with the auditor's issues fed back as
  guidance, up to **3 attempts**.
- Final failure keeps the last draft and surfaces a warning — it never blocks
  the run.

### 3. Article-digest grounding wiring

`context.py` had always loaded `profile/article-digest.md` into state, and
`shared.md` declared it the precedence source for metrics, but no prompt ever
received it. All six fact-grounding prompts (`cv_match`, `tailor_cv`,
`cover_letter`, `personalization`, `score_and_recommend`, `quality_audit`) now
take an optional Article Digest block.

## Consequences

- The cheap tier costs roughly 2× more tokens per CV/letter (reasoning headroom
  + the audit pass + occasional regeneration). This is the intended trade per
  the operator mandate, not a regression.
- Generation failures are now loud (`RuntimeError`, surfaced warnings) instead
  of silently producing a `0.0` score or an untailored CV.
- The audit loop is bounded (3 attempts, last-draft fallback) so a stubborn
  auditor can't stall the pipeline indefinitely.
- Future work should keep this bias: prefer extra LLM passes / retries over
  cheaper-but-worse output. See `docs/design-notes.md` §O for the broader
  artifact-quality review that produced these changes.

## Amendment (2026-07-27)

Commit `ff31a17` reversed the "Final failure" line under Decision §2 above.
That line is left as originally written for the record; this is what
replaced it.

**What changed.** `generate_with_audit` used to return `(artifact, errors)`,
so "the auditor approved this" and "the auditor never answered" collapsed
into the same value, and an unverified draft could ship silently. It now
returns an `AuditedArtifact` with an explicit `status`
(`ok` / `failed` / `unavailable`). On `status == "failed"`:

- `nodes/pdf.py::tailor_cv` withholds the tailored draft. The tailored CV is
  not written to state at all, and the run falls back to rendering the
  hand-written master CV instead — known-good, not tailored to the JD.
- `nodes/cover_letter.py`'s cover-letter path withholds the draft outright:
  `cover_letter_path` is set to `None`, so no PDF is produced for that
  artifact.

Both cases record `artifact_warnings` (`"... withheld (audit failed): ..."`)
that flow into the report the operator reads, so the withholding is visible,
not silent.

**Why.** The original decision treated a stubborn auditor as the failure
mode to guard against — hence "never blocks the run." Living with it showed
the asymmetry was backwards: a resume or cover letter that reaches an
employer without ever having passed review is worse than a run that
produces nothing for that artifact. Regenerating (or falling back to the
untailored master CV) costs tokens and, in the cover-letter case, an
otherwise-complete application; shipping an artifact three audit passes
rejected costs the interview. The audit is also asymmetric on purpose in
the same commit: a rejection is honoured from anywhere in the auditor's
reply, an approval only when the whole reply is the clean verdict object
with no issues — being wrong toward "regenerate" is the cheap direction to
be wrong in.

The bounded-retry shape from the original decision (3 attempts, auditor
issues fed back as guidance) is unchanged; only what happens after the
third failure changed.
