"""Tests for the Phase 3.4 apply-run.jsonl event log."""

from __future__ import annotations

import json
from pathlib import Path

from job_hunt.services.web import apply_run_log


def test_emit_appends_one_line_per_event(tmp_path: Path) -> None:
    apply_run_log.emit(tmp_path, "step.entered", step="My Information")
    apply_run_log.emit(tmp_path, "save_and_continue.clicked", step="My Information")
    apply_run_log.emit(
        tmp_path,
        "step.changed",
        **{"from": "My Information", "to": "My Experience"},
    )

    log_path = tmp_path / apply_run_log.RUN_LOG_FILENAME
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [item["event"] for item in parsed] == [
        "step.entered",
        "save_and_continue.clicked",
        "step.changed",
    ]
    assert parsed[2]["from"] == "My Information"
    assert parsed[2]["to"] == "My Experience"
    # Every event has a timestamp.
    assert all(item.get("ts") for item in parsed)


def test_emit_drops_none_fields(tmp_path: Path) -> None:
    apply_run_log.emit(tmp_path, "command.replace_pdf", pdf="/tmp/x.pdf", screenshot=None)
    parsed = apply_run_log.read_events(tmp_path)
    assert parsed[0]["pdf"] == "/tmp/x.pdf"
    assert "screenshot" not in parsed[0]


def test_read_events_skips_malformed_lines(tmp_path: Path) -> None:
    log_path = tmp_path / apply_run_log.RUN_LOG_FILENAME
    log_path.write_text(
        '{"event": "ok", "ts": "2026-05-08T00:00:00Z"}\n'
        "::not json::\n"
        '{"event": "ok2", "ts": "2026-05-08T00:00:01Z"}\n',
        encoding="utf-8",
    )
    events = apply_run_log.read_events(tmp_path)
    assert [e["event"] for e in events] == ["ok", "ok2"]


def test_emit_creates_directory_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "newly" / "created"
    apply_run_log.emit(nested, "session.started")
    assert (nested / apply_run_log.RUN_LOG_FILENAME).exists()


def test_emit_is_silent_on_falsy_dir() -> None:
    # Should not raise when artifact_dir is None (defensive guard).
    apply_run_log.emit(None, "session.started")  # type: ignore[arg-type]
