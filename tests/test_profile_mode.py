"""Tests for the top-level student/full mode switch (docs/design-notes.md §N)."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunt.services.profile_loader import current_mode


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_current_mode_returns_student_when_set(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    _write(profile, "mode: student\ncandidate:\n  full_name: A\n")
    assert current_mode(profile) == "student"


def test_current_mode_returns_full_when_set(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    _write(profile, "mode: full\n")
    assert current_mode(profile) == "full"


def test_current_mode_defaults_to_full_when_missing_file(tmp_path: Path) -> None:
    assert current_mode(tmp_path / "missing.yml") == "full"


def test_current_mode_defaults_to_full_when_field_absent(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    _write(profile, "candidate:\n  full_name: A\n")
    assert current_mode(profile) == "full"


def test_current_mode_defaults_to_full_when_value_invalid(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    _write(profile, "mode: contractor\n")
    assert current_mode(profile) == "full"


def test_current_mode_normalises_case_and_whitespace(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    _write(profile, 'mode: "  STUDENT  "\n')
    assert current_mode(profile) == "student"


def test_current_mode_handles_malformed_yaml(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    _write(profile, "mode: [this is not valid: yaml\n")
    assert current_mode(profile) == "full"


def test_current_mode_handles_non_dict_root(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    _write(profile, "- item1\n- item2\n")
    assert current_mode(profile) == "full"


@pytest.mark.parametrize("value", ["student", "full"])
def test_current_mode_round_trip(tmp_path: Path, value: str) -> None:
    profile = tmp_path / "profile.yml"
    _write(profile, f"mode: {value}\n")
    assert current_mode(profile) == value
