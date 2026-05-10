"""Tests for cv_sync_check pre-evaluate consistency check."""

from __future__ import annotations

import os
import time
from pathlib import Path

from job_hunt.services import cv_sync_check


def _write_min_profile(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "full_name: Example Candidate\nemail: candidate@example.com\nlocation: Toronto, ON\n",
        encoding="utf-8",
    )


def _write_cv(root: Path, *, length: int = 500) -> None:
    cv = root / "profile" / "cv.md"
    cv.parent.mkdir(parents=True, exist_ok=True)
    cv.write_text("# CV\n\n" + ("Lorem ipsum " * length), encoding="utf-8")


def test_run_passes_with_complete_setup(tmp_path: Path) -> None:
    _write_cv(tmp_path)
    _write_min_profile(tmp_path / "profile" / "profile.yml")

    result = cv_sync_check.run(root=tmp_path)

    assert result.errors == []
    assert result.ok


def test_run_errors_when_cv_missing(tmp_path: Path) -> None:
    _write_min_profile(tmp_path / "profile" / "profile.yml")
    result = cv_sync_check.run(root=tmp_path)
    assert any("profile/cv.md not found" in e for e in result.errors)
    assert not result.ok


def test_run_errors_when_profile_missing(tmp_path: Path) -> None:
    _write_cv(tmp_path)
    result = cv_sync_check.run(root=tmp_path)
    assert any("profile.yml not found" in e for e in result.errors)


def test_run_warns_when_profile_has_example_placeholder(tmp_path: Path) -> None:
    _write_cv(tmp_path)
    profile = tmp_path / "profile" / "profile.yml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        'full_name: "Jane Smith"\nemail: jane@example.com\nlocation: Anywhere\n',
        encoding="utf-8",
    )
    result = cv_sync_check.run(root=tmp_path)
    assert result.errors == []
    assert any("placeholder" in w for w in result.warnings)


def test_run_warns_on_stale_article_digest(tmp_path: Path) -> None:
    _write_cv(tmp_path)
    _write_min_profile(tmp_path / "profile" / "profile.yml")
    digest = tmp_path / "profile" / "article-digest.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("# Digest\n", encoding="utf-8")
    # Make digest 60 days old
    sixty_days_ago = time.time() - 60 * 86400
    os.utime(digest, (sixty_days_ago, sixty_days_ago))

    result = cv_sync_check.run(root=tmp_path)
    assert any("days old" in w for w in result.warnings)
