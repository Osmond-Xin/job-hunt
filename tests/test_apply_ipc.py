"""Tests for the Phase 3.3 apply-session IPC primitives."""

from __future__ import annotations

import time
from pathlib import Path

from job_hunt.services.web import apply_ipc


def test_heartbeat_round_trip(tmp_path: Path) -> None:
    apply_ipc.write_heartbeat(tmp_path, started_at=100.0)
    data = apply_ipc.read_heartbeat(tmp_path)
    assert data is not None
    assert data["started_at"] == 100.0
    assert data["pid"] > 0
    assert isinstance(data["last_heartbeat"], float)


def test_session_is_alive_true_for_fresh_heartbeat(tmp_path: Path) -> None:
    apply_ipc.write_heartbeat(tmp_path, started_at=time.time())
    assert apply_ipc.session_is_alive(tmp_path)


def test_session_is_alive_false_when_no_file(tmp_path: Path) -> None:
    assert not apply_ipc.session_is_alive(tmp_path)


def test_session_is_alive_false_when_stale(tmp_path: Path) -> None:
    # Hand-write a heartbeat that's older than the threshold.
    (tmp_path / apply_ipc.HEARTBEAT_FILE).write_text(
        '{"pid": 1, "started_at": 0, "last_heartbeat": 0.0}',
        encoding="utf-8",
    )
    assert not apply_ipc.session_is_alive(tmp_path, max_age_seconds=1)


def test_clear_heartbeat_removes_file(tmp_path: Path) -> None:
    apply_ipc.write_heartbeat(tmp_path, started_at=time.time())
    apply_ipc.clear_heartbeat(tmp_path)
    assert not (tmp_path / apply_ipc.HEARTBEAT_FILE).exists()


def test_submit_command_writes_unique_sentinel(tmp_path: Path) -> None:
    a = apply_ipc.submit_command(tmp_path, apply_ipc.COMMAND_TYPE_REPLACE_PDF, {"pdf": "/tmp/a"})
    b = apply_ipc.submit_command(tmp_path, apply_ipc.COMMAND_TYPE_REPLACE_PDF, {"pdf": "/tmp/b"})
    assert a != b
    assert a.exists() and b.exists()
    assert a.name.startswith(".cmd-") and a.name.endswith(".json")


def test_consume_pending_commands_returns_in_mtime_order(tmp_path: Path) -> None:
    first = apply_ipc.submit_command(tmp_path, apply_ipc.COMMAND_TYPE_REPLACE_PDF, {"pdf": "1"})
    # Make sure mtime ordering is unambiguous on coarse-resolution filesystems.
    import os
    os.utime(first, (1.0, 1.0))
    second = apply_ipc.submit_command(tmp_path, apply_ipc.COMMAND_TYPE_CAPTURE_PAGE, {})
    os.utime(second, (2.0, 2.0))
    pending = apply_ipc.consume_pending_commands(tmp_path)
    assert [c.kind for c in pending] == [
        apply_ipc.COMMAND_TYPE_REPLACE_PDF,
        apply_ipc.COMMAND_TYPE_CAPTURE_PAGE,
    ]
    # Sentinels are deleted after consumption.
    assert not first.exists() and not second.exists()


def test_consume_pending_commands_drops_malformed_files(tmp_path: Path) -> None:
    bad = tmp_path / ".cmd-bad.json"
    bad.write_text("::not json::")
    pending = apply_ipc.consume_pending_commands(tmp_path)
    assert pending == []
    assert not bad.exists()  # malformed file is still deleted to prevent loops


def test_submit_command_writes_only_uuid_sentinel(tmp_path: Path) -> None:
    # The pre-v2 named-sentinel compat path was removed (ADR-012 sunset); a
    # command must write exactly one ``.cmd-*.json`` file and no legacy named
    # file, otherwise the fill loop double-processes it.
    apply_ipc.submit_command(
        tmp_path,
        apply_ipc.COMMAND_TYPE_REPLACE_PDF,
        {"pdf": "/tmp/x"},
    )
    cmd_files = list(tmp_path.glob(".cmd-*.json"))
    assert len(cmd_files) == 1
    assert not (tmp_path / ".replace_pdf").exists()
    pending = apply_ipc.consume_pending_commands(tmp_path)
    assert [c.kind for c in pending] == [apply_ipc.COMMAND_TYPE_REPLACE_PDF]
    assert pending[0].payload == {"pdf": "/tmp/x"}


def test_find_active_session_dir_prefers_fresh_heartbeat(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    fresh = root / "alpha"
    stale = root / "beta"
    fresh.mkdir(parents=True)
    stale.mkdir(parents=True)
    apply_ipc.write_heartbeat(fresh, started_at=time.time())
    (stale / apply_ipc.HEARTBEAT_FILE).write_text(
        '{"pid": 1, "started_at": 0, "last_heartbeat": 0.0}',
        encoding="utf-8",
    )
    assert apply_ipc.find_active_session_dir(root) == fresh


def test_find_active_session_dir_falls_back_to_cdp_when_no_heartbeat(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    compat = root / "old"
    compat.mkdir(parents=True)
    (compat / ".cdp").write_text("active")
    # No heartbeat anywhere; fallback should still surface the .cdp dir.
    assert apply_ipc.find_active_session_dir(root) == compat


def test_find_active_session_dir_returns_none_when_empty(tmp_path: Path) -> None:
    assert apply_ipc.find_active_session_dir(tmp_path) is None
