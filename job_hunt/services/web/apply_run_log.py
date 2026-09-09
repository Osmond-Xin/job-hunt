"""Phase 3.4 — Structured run log for ``apply --fill-only`` sessions.

Each apply session writes one line of JSON per noteworthy event to
``apply-run.jsonl`` in its artifact directory. Events are tiny on purpose:

```json
{"ts": "2026-05-08T19:34:21Z", "event": "step.entered", "step": "My Experience"}
{"ts": "2026-05-08T19:34:23Z", "event": "save_and_continue.clicked", "step": "My Experience"}
{"ts": "2026-05-08T19:34:27Z", "event": "step.changed", "from": "My Experience", "to": "Application Questions", "elapsed_ms": 4120}
{"ts": "2026-05-08T19:34:30Z", "event": "review.validation", "issue_code": "WD_REVIEW_DATE_MISMATCH", "details": {...}}
```

The run log is append-only and survives across the apply session. Future
``apply doctor``-style tooling can scan one or more sessions to answer "which
step usually stalls" or "which review issue code recurs" without rerunning the
real browser.

Failures here are silent — a flaky filesystem must not abort the apply flow.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_LOG_FILENAME = "apply-run.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(art_dir: Path, event: str, **fields: Any) -> None:
    """Append a single event line to ``apply-run.jsonl``.

    Never raises: the apply flow must keep running even if disk write fails.
    """
    if not art_dir:
        return
    payload: dict[str, Any] = {"ts": _now_iso(), "event": event}
    payload.update({k: v for k, v in fields.items() if v is not None})
    try:
        art_dir.mkdir(parents=True, exist_ok=True)
        with (art_dir / RUN_LOG_FILENAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


ATTEMPT_LOG = Path("data/submit-attempts.jsonl")


def record_submit_attempt(application: str, art_dir: Path, url: str) -> None:
    """Write down that a Submit is about to be clicked. Raises if it cannot.

    Unlike ``emit``, this one is not allowed to fail quietly. It is a safety
    record, and a safety record nobody managed to write is the case it exists
    for: without it, a click that lands and loses its confirmation leaves no
    trace, and the next run repeats it against a real employer. The caller must
    let the exception stop the click.
    """
    ATTEMPT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ATTEMPT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"ts": _now_iso(), "event": "submit.attempted",
                 "application": application, "artifact_dir": str(art_dir), "url": url},
                ensure_ascii=False,
            ) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def record_submit_resolution(application: str, state: str, evidence: str = "") -> None:
    """Write down how a Submit turned out. Also not allowed to fail quietly."""
    ATTEMPT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ATTEMPT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"ts": _now_iso(), "event": "submit.resolved",
                 "application": application, "state": state, "evidence": evidence},
                ensure_ascii=False,
            ) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def unresolved_submit_attempt(application: str) -> str | None:
    """Whether this application has a Submit that was clicked and never settled.

    A ``SubmitOutcome`` of ``unknown`` -- clicked, no confirmation -- cannot stop
    the *next* run by itself: the process exits and the next ``job-hunt apply``
    starts knowing nothing. So the attempt is written before the click and the
    resolution after it, and an attempt with no resolution blocks another click.

    Keyed on the application, not on the artifact directory. The directory name
    carries the date and a truncated role, so a retry the next day would look in
    a different place and find nothing, and two roles sharing their first four
    words would share a guard. Both found by review on 2026-09-09.

    Returns the artifact directory to point a person at, or None when the last
    attempt resolved -- including as ``rejected``, which is a definite "not
    sent" and safe to try again.
    """
    if not ATTEMPT_LOG.exists():
        return None
    state, where = None, None
    for line in ATTEMPT_LOG.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("application") != application:
            continue
        if event.get("event") == "submit.attempted":
            state, where = "attempted", event.get("artifact_dir")
        elif event.get("event") == "submit.resolved":
            state = "unresolved" if event.get("state") == "unknown" else None
    return where if state in {"attempted", "unresolved"} else None


def read_events(art_dir: Path) -> list[dict[str, Any]]:
    """Read parsed events from disk. Drops malformed lines silently."""
    path = art_dir / RUN_LOG_FILENAME
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
