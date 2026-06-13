"""Phase 3.3 — Apply-session IPC primitives.

Replaces the previous "one named sentinel file per command" scheme with:

1. **Heartbeat** (``.session.json``): the running ``apply --fill-only`` loop
   refreshes ``last_heartbeat`` every iteration. Sub-commands check the file
   before submitting work and refuse fast when the session is dead instead of
   silently dropping a sentinel into a stale directory.

2. **Per-command sentinels** (``.cmd-<uuid>.json``): each ``apply-replace-pdf``
   / ``apply-capture-page`` / ``apply-refill-current-page`` / ``apply-close-session``
   invocation writes a unique file. The main loop processes them in mtime order
   and deletes them after handling, so two ``apply-replace-pdf`` calls in quick
   succession can no longer race each other (the old single ``.replace_pdf``
   file would be overwritten before the loop noticed the first one).

3. **Idle-based timeout**: the loop exits after ``IDLE_TIMEOUT_SECONDS`` with no
   commands AND no auto-fill activity (heartbeat alone does not count as
   activity), instead of the old hard 30-minute deadline.

The pre-v2 named-sentinel compatibility path (``.replace_pdf``, ``.capture_page``,
``submit_command_with_compat``) was removed on 2026-06-13 — see ADR-012, which
flagged it as transition-only debt to sunset once no caller relied on it. The
fill-only session's ≤60-minute idle lifetime means no pre-upgrade loop or
sub-command can still exist; keeping both the dual write and dual read also
double-processed every current command.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

HEARTBEAT_FILE = ".session.json"
HEARTBEAT_STALE_SECONDS = 30        # sub-commands refuse if heartbeat is older than this
IDLE_TIMEOUT_SECONDS = 3600         # loop exits when this long passes without a command/refill
HEARTBEAT_REFRESH_SECONDS = 5       # how often the loop should write the heartbeat


COMMAND_TYPE_REPLACE_PDF = "replace_pdf"
COMMAND_TYPE_CAPTURE_PAGE = "capture_page"
COMMAND_TYPE_REFILL_CURRENT_PAGE = "refill_current_page"
COMMAND_TYPE_CLOSE_SESSION = "close_session"


@dataclass(frozen=True)
class Command:
    id: str
    kind: str
    payload: dict
    sent_at: float


def write_heartbeat(art_dir: Path, *, started_at: float) -> None:
    """Refresh the heartbeat file. Should be called by the loop every iteration."""
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / HEARTBEAT_FILE).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": started_at,
                "last_heartbeat": time.time(),
            }
        ),
        encoding="utf-8",
    )


def clear_heartbeat(art_dir: Path) -> None:
    (art_dir / HEARTBEAT_FILE).unlink(missing_ok=True)


def read_heartbeat(art_dir: Path) -> dict | None:
    path = art_dir / HEARTBEAT_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def session_is_alive(art_dir: Path, *, max_age_seconds: float = HEARTBEAT_STALE_SECONDS) -> bool:
    data = read_heartbeat(art_dir)
    if not data:
        return False
    try:
        last = float(data.get("last_heartbeat", 0))
    except (TypeError, ValueError):
        return False
    return (time.time() - last) <= max_age_seconds


def submit_command(
    art_dir: Path, kind: str, payload: dict | None = None
) -> Path:
    """Drop a unique ``.cmd-<id>.json`` sentinel. Returns the file path."""
    art_dir.mkdir(parents=True, exist_ok=True)
    cmd_id = uuid.uuid4().hex[:8]
    sentinel = art_dir / f".cmd-{cmd_id}.json"
    sentinel.write_text(
        json.dumps(
            {
                "id": cmd_id,
                "kind": kind,
                "payload": payload or {},
                "sent_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    return sentinel


def consume_pending_commands(art_dir: Path) -> list[Command]:
    """Read and unlink all queued ``.cmd-*.json`` files in mtime order."""
    items = sorted(art_dir.glob(".cmd-*.json"), key=lambda p: p.stat().st_mtime)
    out: list[Command] = []
    for path in items:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                Command(
                    id=str(data.get("id", path.stem)),
                    kind=str(data.get("kind", "")),
                    payload=dict(data.get("payload") or {}),
                    sent_at=float(data.get("sent_at", 0.0)),
                )
            )
        except Exception:
            # Drop the file regardless so a malformed sentinel doesn't loop forever.
            pass
        path.unlink(missing_ok=True)
    return out


def time_since(timestamp: float) -> float:
    return time.time() - timestamp


def find_active_session_dir(root: Path = Path("artifacts/apply")) -> Path | None:
    """Return the most-recently-touched artifact dir whose heartbeat is fresh."""
    candidates = sorted(
        list(root.glob("*/.session.json")) + list(root.glob("*/.cdp")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    seen: set[Path] = set()
    for path in candidates:
        art_dir = path.parent
        if art_dir in seen:
            continue
        seen.add(art_dir)
        if session_is_alive(art_dir):
            return art_dir
    # Fall through: no fresh heartbeat. Return the newest .cdp dir if any (so the
    # subcommand can decide how to surface "session probably dead").
    fallback = sorted(
        root.glob("*/.cdp"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return fallback[0].parent if fallback else None
