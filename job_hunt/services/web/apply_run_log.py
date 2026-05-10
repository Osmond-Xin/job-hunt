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
