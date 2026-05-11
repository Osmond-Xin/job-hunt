"""Tests for `job-hunt config set-mode` CLI (docs/design-notes.md §N.4 step 9)."""

from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from job_hunt.cli import app

runner = CliRunner()


def _setup_profile(tmp_path: Path, body: str) -> Path:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    profile = profile_dir / "profile.yml"
    profile.write_text(body, encoding="utf-8")
    return profile


def test_set_mode_writes_student(tmp_path: Path) -> None:
    profile = _setup_profile(tmp_path, 'mode: "full"\ncandidate:\n  full_name: A\n')
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["config", "set-mode", "student"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    text = profile.read_text(encoding="utf-8")
    assert 'mode: "student"' in text
    assert "full_name: A" in text  # rest of file preserved


def test_set_mode_inserts_when_missing(tmp_path: Path) -> None:
    profile = _setup_profile(tmp_path, "candidate:\n  full_name: A\n")
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["config", "set-mode", "full"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    text = profile.read_text(encoding="utf-8")
    assert text.startswith("# Top-level mode switch")
    assert 'mode: "full"' in text


def test_set_mode_no_op_without_force(tmp_path: Path) -> None:
    profile = _setup_profile(tmp_path, 'mode: "student"\n')
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["config", "set-mode", "student"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0
    assert "No change" in result.output
    assert profile.read_text(encoding="utf-8") == 'mode: "student"\n'


def test_set_mode_force_rewrites_same_value(tmp_path: Path) -> None:
    profile = _setup_profile(tmp_path, 'mode: student\n')
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["config", "set-mode", "student", "--force"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0
    # Force-rewrite should normalise the quoting.
    assert 'mode: "student"' in profile.read_text(encoding="utf-8")


def test_set_mode_rejects_invalid_value(tmp_path: Path) -> None:
    _setup_profile(tmp_path, 'mode: "full"\n')
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["config", "set-mode", "contractor"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 2
    assert "Invalid mode" in result.output


def test_set_mode_errors_when_profile_missing(tmp_path: Path) -> None:
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["config", "set-mode", "full"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 2
    assert "profile/profile.yml not found" in result.output
