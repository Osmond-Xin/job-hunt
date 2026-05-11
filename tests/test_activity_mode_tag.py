"""Tests for ActivityEvent mode tagging (docs/design-notes.md §N.5)."""

from __future__ import annotations

import json
from pathlib import Path

from job_hunt.config.models import (
    ActivityConfig,
    ActivitySinksConfig,
    LocalLogSinkConfig,
    SlackSinkConfig,
)
from job_hunt.services.activity import ActivityEvent, ActivityLogger


def _logger(tmp_path: Path) -> tuple[ActivityLogger, Path]:
    log_path = tmp_path / "activity-log.jsonl"
    config = ActivityConfig(
        sinks=ActivitySinksConfig(
            local_log=LocalLogSinkConfig(enabled=True, path=log_path),
            slack=SlackSinkConfig(enabled=False, webhook_secret_ref="env:UNUSED"),
        ),
    )
    return ActivityLogger(config), log_path


def test_activity_event_defaults_mode_to_none() -> None:
    event = ActivityEvent(type="apply.submitted", summary="x")
    assert event.mode is None


def test_activity_event_accepts_student_mode() -> None:
    event = ActivityEvent(type="apply.submitted", summary="x", mode="student")
    assert event.mode == "student"


def test_activity_event_accepts_full_mode() -> None:
    event = ActivityEvent(type="apply.submitted", summary="x", mode="full")
    assert event.mode == "full"


def test_activity_event_serialises_mode_to_jsonl(tmp_path: Path) -> None:
    logger, log_path = _logger(tmp_path)
    logger.emit(ActivityEvent(type="apply.submitted", summary="x", mode="student"))
    line = log_path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["mode"] == "student"
    assert parsed["type"] == "apply.submitted"


def test_activity_event_omits_mode_field_value_when_none(tmp_path: Path) -> None:
    """Backward-compat: legacy emitters that don't pass mode produce a
    ``"mode": null`` line, which existing readers ignore harmlessly."""
    logger, log_path = _logger(tmp_path)
    logger.emit(ActivityEvent(type="email.poll_completed", summary="x"))
    line = log_path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["mode"] is None


def test_activity_event_rejects_invalid_mode() -> None:
    import pydantic

    try:
        ActivityEvent(type="apply.submitted", summary="x", mode="contractor")  # type: ignore[arg-type]
    except pydantic.ValidationError:
        return
    raise AssertionError("Expected ValidationError for invalid mode value.")
