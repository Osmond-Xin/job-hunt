"""Tests for the extracted Workday login flow.

These exercise the orchestration glue — file-based password handoff,
no-op short-circuits, and the diagnostic-dump path — without booting
Playwright. The page object is mocked to record what the login flow
attempted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_hunt.services.workday import login as login_module
from job_hunt.services.workday.login import (
    _read_and_consume_password,
    maybe_login,
)


def _mock_page(url: str, body_text: str | None = "") -> MagicMock:
    page = MagicMock()
    page.url = url
    body_locator = MagicMock()
    if body_text is None:
        body_locator.inner_text = AsyncMock(side_effect=RuntimeError("unreadable"))
    else:
        body_locator.inner_text = AsyncMock(return_value=body_text)
    page.locator.return_value = body_locator
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock(return_value=False)
    page.screenshot = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    return page


# ----- password file handling -----


def test_password_file_deleted_after_read_unless_keep_sentinel(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    secret = tmp_path / "storage" / "private" / "workday-login-password.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("hunter2\n", encoding="utf-8")

    password = _read_and_consume_password()

    assert password == "hunter2"
    assert not secret.exists(), "password file must be wiped after read"


def test_password_file_preserved_when_keep_sentinel_present(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    private = tmp_path / "storage" / "private"
    private.mkdir(parents=True)
    secret = private / "workday-login-password.txt"
    secret.write_text("hunter2", encoding="utf-8")
    (private / "keep-workday-login").write_text("")

    _read_and_consume_password()
    assert secret.exists(), "keep sentinel must preserve the password file"


def test_missing_password_file_returns_empty_string(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _read_and_consume_password() == ""


# ----- maybe_login short-circuits -----


def test_login_no_op_on_non_workday_url(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    page = _mock_page("https://greenhouse.io/foo", body_text="anything")

    asyncio.run(maybe_login(page, email="x@example.com"))

    page.locator.assert_not_called()
    page.goto.assert_not_called()


def test_login_no_op_when_no_password_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    page = _mock_page("https://acme.myworkdayjobs.com/job", body_text="Sign In")

    asyncio.run(maybe_login(page, email="x@example.com"))

    page.evaluate.assert_not_called()
    page.goto.assert_not_called()


def test_login_signed_in_emits_breadcrumb(tmp_path, monkeypatch) -> None:
    """Modal not visible → assume signed in; emit `workday.login.skipped`."""
    monkeypatch.chdir(tmp_path)
    private = tmp_path / "storage" / "private"
    private.mkdir(parents=True)
    (private / "workday-login-password.txt").write_text("hunter2")

    art = tmp_path / "art"
    art.mkdir()

    page = _mock_page(
        "https://acme.myworkdayjobs.com/job",
        body_text="Welcome back, here are your applications",
    )

    asyncio.run(maybe_login(page, email="x@example.com", artifact_dir=art))

    events_path = art / "apply-run.jsonl"
    assert events_path.exists()
    contents = events_path.read_text(encoding="utf-8")
    assert "workday.login.skipped" in contents
    assert "no_modal_detected_assume_signed_in" in contents


def test_login_unknown_state_dumps_screenshot_and_html(tmp_path, monkeypatch) -> None:
    """Body unreadable → dump screenshot + html and emit unknown_state event."""
    monkeypatch.chdir(tmp_path)
    private = tmp_path / "storage" / "private"
    private.mkdir(parents=True)
    (private / "workday-login-password.txt").write_text("hunter2")

    art = tmp_path / "art"
    art.mkdir()

    page = _mock_page("https://acme.myworkdayjobs.com/job", body_text=None)
    warnings: list[str] = []

    asyncio.run(
        maybe_login(
            page,
            email="x@example.com",
            artifact_dir=art,
            warn=warnings.append,
        )
    )

    assert (art / "login-modal-unknown.html").exists()
    page.screenshot.assert_called()
    contents = (art / "apply-run.jsonl").read_text(encoding="utf-8")
    assert "workday.login.unknown_state" in contents
    assert any("unknown state" in msg.lower() for msg in warnings)
