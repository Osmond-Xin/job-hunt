# ADR-012: apply-review.json schema + apply-run.jsonl + heartbeat IPC

**Status:** Accepted
**Date:** 2026-05-08

## Context

`apply --fill-only` runs a long-lived Playwright session that the user
interacts with via follow-up CLI commands (`apply-replace-pdf`,
`apply-capture-page`, `apply-refill-current-page`, `apply-close-session`).
The original IPC was a single named sentinel file per command, with a
30-minute hard deadline and no liveness signal.

Three problems built up:

- **Races.** Two follow-up commands within the same second could
  overwrite each other's sentinel before the fill loop consumed it.
- **No liveness.** A user returning after lunch had no way to know if
  the session was alive without opening the browser.
- **No history.** Debugging a stuck flow meant tailing the visible
  console; nothing structured survived after the session closed.

`apply-review.json` was the only durable artifact and its shape was
implicit. Downstream `jq` / dashboard consumers could break silently
when the writer evolved.

## Decision

Three coordinated changes:

### 1. Per-command UUID sentinels + heartbeat (`services/web/apply_ipc.py`)

- `.session.json` heartbeat file refreshed every 5 seconds by the fill
  loop. Subcommands warn when the heartbeat is more than 30 seconds
  stale.
- `.cmd-<uuid>.json` per-command sentinel files; the fill loop consumes
  them in arrival order. No more overwrites.
- `apply-replace-pdf` writes both the legacy named sentinel and the new
  UUID sentinel during the transition window
  (`submit_command_with_legacy`).
- Hard 30-minute deadline replaced with a 60-minute **idle** timeout
  (`IDLE_TIMEOUT_SECONDS`). Active commands reset the clock.

Public API: `write_heartbeat`, `session_is_alive`, `submit_command`,
`consume_pending_commands`, `find_active_session_dir`, `Command`,
`COMMAND_TYPE_*`, `IDLE_TIMEOUT_SECONDS`.

### 2. Structured event log (`services/web/apply_run_log.py`)

- `artifacts/apply/<slug>/apply-run.jsonl` records every notable event:
  `session.started`, `step.entered`, `save_and_continue.clicked`,
  `step.changed` / `step.change_timeout`, `review.validation`,
  `command.replace_pdf` / `command.refill_current_page` /
  `command.capture_page`, `workday.login.skipped` /
  `workday.login.unknown_state`, `auto_submit.gated` /
  `auto_submit.fired` / `auto_submit.confirmed`, `session.idle_exit`,
  `session.ended`.
- Public API: `emit(art_dir, event, **fields)`, `read_events(art_dir)`.
- Malformed lines are skipped, not fatal — partial corruption never
  hides the rest of the history.

### 3. `apply-review.json` schema lock-in

- `validation_issues` is always a list (never `null`). Clean runs emit
  `[]`. Dirty runs emit `[{code, message, details}]` per ADR-011.
- `pdf` is the file path when attached, `null` otherwise.
- `answers` persists prior Q&A so the next run can fuzzy-match.
- Workday PDF attachment is detected via
  `_workday_resume_was_uploaded(page, pdf)` (filename-in-body match) and
  mirrored into the artifact dir with `shutil.copy2`.

Login diagnostics also become 3-state in `_maybe_workday_login`:
body unreadable → dump `login-modal-unknown.{png,html}`; body present
but no Sign In / Create Account → assume signed-in (log breadcrumb);
login submit didn't clear modal → dump.

## Consequences

- The fill-only session is now observable from outside: `jq` over
  `apply-run.jsonl` reconstructs the timeline; the heartbeat says
  whether the session is alive right now.
- Race-free per-command IPC removes a class of "command silently
  ignored" failures.
- Idle-based timeout matches user behaviour (step away to fill a
  PDF, come back) without leaving zombie sessions forever.
- `apply-review.json` is now `jq`-safe; the dashboard, the auto-submit
  gate (ADR-011), and the user-facing review artifact all read the
  same structured shape.
- The legacy named-sentinel write is transition-only debt. A future
  ADR should sunset it once no caller still relies on the old path.
- Two new files-on-disk per session (`session.json`,
  `cmd-<uuid>.json`); both live under the per-session artifact dir
  and are cleaned up on `session.ended`.
