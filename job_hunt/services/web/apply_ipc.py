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
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

# Command/response ids are hex uuid fragments; anything else is rejected so a
# forged sentinel cannot steer the response filename (path traversal) or feed
# oversized garbage into `.res-{id}.json`.
_CMD_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")

HEARTBEAT_FILE = ".session.json"
HEARTBEAT_STALE_SECONDS = 30        # sub-commands refuse if heartbeat is older than this
IDLE_TIMEOUT_SECONDS = 3600         # loop exits when this long passes without a command/refill
HEARTBEAT_REFRESH_SECONDS = 5       # how often the loop should write the heartbeat


COMMAND_TYPE_REPLACE_PDF = "replace_pdf"
COMMAND_TYPE_CAPTURE_PAGE = "capture_page"
COMMAND_TYPE_REFILL_CURRENT_PAGE = "refill_current_page"
COMMAND_TYPE_CLOSE_SESSION = "close_session"
COMMAND_TYPE_STATUS = "status"
COMMAND_TYPE_DO = "do"

RESPONSE_POLL_SECONDS = 0.5
RESPONSE_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Command:
    id: str
    kind: str
    payload: dict
    sent_at: float
    token: str = ""


def write_heartbeat(
    art_dir: Path, *, started_at: float, session_token: str = ""
) -> None:
    """Refresh the heartbeat file. Should be called by the loop every iteration.

    ``session_token`` is a per-session nonce: sub-commands copy it from the
    heartbeat into their sentinel, and the loop rejects sentinels whose token
    does not match — so a stale script or stray file cannot drive the live
    browser (red-team fix 2026-07-09).
    """
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / HEARTBEAT_FILE).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": started_at,
                "last_heartbeat": time.time(),
                "session_token": session_token,
            }
        ),
        encoding="utf-8",
    )


def read_session_token(art_dir: Path) -> str:
    data = read_heartbeat(art_dir)
    if not data:
        return ""
    return str(data.get("session_token") or "")


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
    """Drop a unique ``.cmd-<id>.json`` sentinel. Returns the file path.

    Written atomically (tmp + rename) so the polling loop never reads a
    half-written command, and stamped with the session token from the
    heartbeat so the loop can authenticate it.
    """
    art_dir.mkdir(parents=True, exist_ok=True)
    cmd_id = uuid.uuid4().hex[:8]
    sentinel = art_dir / f".cmd-{cmd_id}.json"
    tmp = art_dir / f".cmd-{cmd_id}.json.tmp"
    tmp.write_text(
        json.dumps(
            {
                "id": cmd_id,
                "kind": kind,
                "payload": payload or {},
                "sent_at": time.time(),
                "token": read_session_token(art_dir),
            }
        ),
        encoding="utf-8",
    )
    tmp.replace(sentinel)
    return sentinel


def command_id_of(sentinel: Path) -> str:
    """Extract the command id from a ``.cmd-<id>.json`` sentinel path."""
    return sentinel.name.removeprefix(".cmd-").removesuffix(".json")


def write_response(art_dir: Path, cmd_id: str, payload: dict) -> Path:
    """Write the loop's answer to a request/response command (``.res-<id>.json``).

    Written atomically (tmp + rename) so a polling sub-command never reads a
    half-written file. ``cmd_id`` must be a hex id (see ``_CMD_ID_RE``).
    """
    if not _CMD_ID_RE.fullmatch(cmd_id):
        raise ValueError(f"invalid command id: {cmd_id!r}")
    response = art_dir / f".res-{cmd_id}.json"
    tmp = art_dir / f".res-{cmd_id}.json.tmp"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(response)
    return response


def wait_for_response(
    art_dir: Path,
    cmd_id: str,
    *,
    timeout: float = RESPONSE_TIMEOUT_SECONDS,
) -> dict | None:
    """Poll for ``.res-<id>.json``, consume it, and return the payload.

    Returns ``None`` on timeout (session dead or loop busy). The response file
    is always deleted after a successful read.
    """
    response = art_dir / f".res-{cmd_id}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if response.exists():
            try:
                data = json.loads(response.read_text(encoding="utf-8"))
            except Exception:
                data = None
            response.unlink(missing_ok=True)
            return data if isinstance(data, dict) else None
        time.sleep(RESPONSE_POLL_SECONDS)
    return None


def clear_stale_responses(art_dir: Path) -> None:
    """Drop leftover ``.res-*.json`` files from a previous session."""
    for path in art_dir.glob(".res-*.json*"):
        path.unlink(missing_ok=True)


def consume_pending_commands(art_dir: Path) -> list[Command]:
    """Read and unlink all queued ``.cmd-*.json`` files in mtime order.

    The command id is derived from the sentinel *filename* (never from the
    JSON body) and must be hex, so a forged body cannot smuggle a hostile id
    into the response path. Commands with non-conforming filenames are
    dropped.
    """
    items = sorted(art_dir.glob(".cmd-*.json"), key=lambda p: p.stat().st_mtime)
    out: list[Command] = []
    for path in items:
        cmd_id = command_id_of(path)
        try:
            if not _CMD_ID_RE.fullmatch(cmd_id):
                raise ValueError(f"bad command id in filename: {path.name}")
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                Command(
                    id=cmd_id,
                    kind=str(data.get("kind", "")),
                    payload=dict(data.get("payload") or {}),
                    sent_at=float(data.get("sent_at", 0.0)),
                    token=str(data.get("token", "")),
                )
            )
        except Exception:
            # Drop the file regardless so a malformed sentinel doesn't loop forever.
            pass
        path.unlink(missing_ok=True)
    return out


def time_since(timestamp: float) -> float:
    return time.time() - timestamp


def find_alive_session_dirs(root: Path = Path("artifacts/apply")) -> list[Path]:
    """All artifact dirs with a fresh heartbeat, newest first."""
    candidates = sorted(
        root.glob(f"*/{HEARTBEAT_FILE}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [p.parent for p in candidates if session_is_alive(p.parent)]


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
