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


def test_command_id_of_matches_sentinel(tmp_path: Path) -> None:
    sentinel = apply_ipc.submit_command(tmp_path, apply_ipc.COMMAND_TYPE_STATUS, {})
    cmd_id = apply_ipc.command_id_of(sentinel)
    pending = apply_ipc.consume_pending_commands(tmp_path)
    assert [c.id for c in pending] == [cmd_id]


def test_response_round_trip(tmp_path: Path) -> None:
    apply_ipc.write_response(tmp_path, "abc123", {"ok": True, "url": "https://x"})
    data = apply_ipc.wait_for_response(tmp_path, "abc123", timeout=2)
    assert data == {"ok": True, "url": "https://x"}
    # Response file is consumed after reading.
    assert not (tmp_path / ".res-abc123.json").exists()


def test_wait_for_response_times_out(tmp_path: Path) -> None:
    assert apply_ipc.wait_for_response(tmp_path, "missing", timeout=0.6) is None


def test_wait_for_response_rejects_malformed(tmp_path: Path) -> None:
    (tmp_path / ".res-bad1.json").write_text("::not json::", encoding="utf-8")
    assert apply_ipc.wait_for_response(tmp_path, "bad1", timeout=1) is None
    assert not (tmp_path / ".res-bad1.json").exists()


def test_clear_stale_responses(tmp_path: Path) -> None:
    apply_ipc.write_response(tmp_path, "0dd1beef", {"ok": True})
    (tmp_path / ".res-old2.json.tmp").write_text("{}", encoding="utf-8")
    apply_ipc.clear_stale_responses(tmp_path)
    assert list(tmp_path.glob(".res-*")) == []


def test_write_response_rejects_hostile_id(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        apply_ipc.write_response(tmp_path, "../escape", {"ok": True})
    with pytest.raises(ValueError):
        apply_ipc.write_response(tmp_path, "UPPER-not-hex", {"ok": True})


def test_session_token_round_trip(tmp_path: Path) -> None:
    apply_ipc.write_heartbeat(tmp_path, started_at=1.0, session_token="feedbeef")
    assert apply_ipc.read_session_token(tmp_path) == "feedbeef"
    sentinel = apply_ipc.submit_command(tmp_path, apply_ipc.COMMAND_TYPE_STATUS, {})
    assert sentinel.exists()
    pending = apply_ipc.consume_pending_commands(tmp_path)
    assert pending[0].token == "feedbeef"


def test_submit_command_without_heartbeat_has_empty_token(tmp_path: Path) -> None:
    apply_ipc.submit_command(tmp_path, apply_ipc.COMMAND_TYPE_STATUS, {})
    pending = apply_ipc.consume_pending_commands(tmp_path)
    assert pending[0].token == ""


def test_consume_derives_id_from_filename_not_body(tmp_path: Path) -> None:
    # A forged body id must not reach the response path.
    (tmp_path / ".cmd-abcd1234.json").write_text(
        '{"id": "../../evil", "kind": "status", "payload": {}, "sent_at": 0}',
        encoding="utf-8",
    )
    pending = apply_ipc.consume_pending_commands(tmp_path)
    assert [c.id for c in pending] == ["abcd1234"]


def test_consume_drops_sentinel_with_hostile_filename_id(tmp_path: Path) -> None:
    bad = tmp_path / ".cmd-NOT_HEX!.json"
    bad.write_text('{"kind": "status", "payload": {}}', encoding="utf-8")
    assert apply_ipc.consume_pending_commands(tmp_path) == []
    assert not bad.exists()


def test_find_alive_session_dirs_orders_and_filters(tmp_path: Path) -> None:
    import time as _time

    root = tmp_path / "apply"
    live = root / "one"
    dead = root / "two"
    live.mkdir(parents=True)
    dead.mkdir(parents=True)
    apply_ipc.write_heartbeat(live, started_at=_time.time())
    (dead / apply_ipc.HEARTBEAT_FILE).write_text(
        '{"pid": 1, "started_at": 0, "last_heartbeat": 0.0}', encoding="utf-8"
    )
    assert apply_ipc.find_alive_session_dirs(root) == [live]
