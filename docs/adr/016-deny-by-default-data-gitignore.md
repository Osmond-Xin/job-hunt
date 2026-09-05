# ADR-016: Deny-by-default `data/` in `.gitignore`

**Status:** Accepted
**Date:** 2026-09-03

## Context

`data/applications.md.bak2.123434` — 172KB holding every company, role,
score, and note in the tracker — was sitting untracked and un-ignored in
this public repo, one `git add -A` away from being published. Nothing had
actually leaked (no `.bak` file appears anywhere in the repo's git history),
but the near miss exposed how the rule was built.

`.gitignore` protected `data/` by extension: `data/*.md`, `data/*.tsv`, and
so on. A backup's filename ends in a timestamp, not in the extension the
list was written against, so `applications.md.bak2.123434` matched none of
the patterns. This was not the first time: an earlier fix
(`cf85078`, "Ignore timestamped .bak files") had already added `*.bak.*`
and `*.bak` after a first near miss. `.bak2.` walked around both of those
in turn. Each fix targeted the specific suffix shape that had just failed,
which is exactly the property that guarantees the next shape gets through.

## Decision

Stop enumerating suffixes. Deny the whole `data/` directory
(`data/*`) and allow back the one file in it that belongs in the repo
(`!data/.gitkeep`) — the same shape `profile/*` / `!profile/.gitkeep`
already used two lines above it in the same file. `data/` is private state
end to end (the tracker, the pipeline inbox, the event logs, scan history);
there is nothing else in it to allow back.

## Consequences

- No suffix, timestamp format, or backup-naming convention can walk around
  this rule the way three successive ones walked around the extension
  list — a new file under `data/` is ignored by construction, not by
  whichever pattern someone thought to add.
- Verified before landing: `.bak2`/`.backup`/`.orig`/`.save`/dated/`.tmp`/
  `.old` shapes are all ignored, `data/.gitkeep` still is not, and no
  previously-tracked file became ignored.
- Any future file that must be tracked under `data/` needs an explicit
  `!data/<name>` line, the same cost `profile/.gitkeep` already pays. This
  is the intended friction: a new exception is a deliberate line in a
  diff, not a pattern someone has to remember to keep updating.
- `profile/*`, `reports/*`, `output/*`, and `jds/*` already used this same
  `dir/*` + `!dir/.gitkeep` shape before this change — `data/` was the one
  directory still on the older extension list, and the one that actually
  leaked a file as a result. The fix brings `data/` in line with a pattern
  the rest of `.gitignore` had already converged on, rather than
  introducing a new one.
