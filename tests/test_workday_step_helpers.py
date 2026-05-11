"""Pure-Python tests for the extracted Workday step helpers.

Covers:

- ``services.workday.my_information.required_blocks_my_information_continue``
- ``services.workday.my_experience.experience_dates_match``
- ``services.workday.my_experience.write_debug_field_dump``

The fillers themselves still live in cli.py for now (heavy Workday helper
dependencies). These are the pure / self-contained pieces that have been
moved to the new modules.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from job_hunt.services.workday.my_experience import (
    experience_dates_match,
    write_debug_field_dump,
)
from job_hunt.services.workday.my_information import (
    required_blocks_my_information_continue,
)


# ----- my_information.required_blocks_my_information_continue -----


def test_password_label_blocks_continue() -> None:
    assert required_blocks_my_information_continue("Password") is True
    assert required_blocks_my_information_continue("Verify New Password") is True


def test_non_password_required_labels_do_not_block() -> None:
    # The historical false-positive surface — Workday's detector flags these
    # as "required and empty" even when the operator never asked us to fill
    # them.
    assert required_blocks_my_information_continue("Alternate Phone Number") is False
    assert required_blocks_my_information_continue("Reference Contact") is False
    assert required_blocks_my_information_continue("Additional Address") is False


def test_label_normalization_is_case_and_whitespace_insensitive() -> None:
    assert required_blocks_my_information_continue("  PASSWORD ") is True
    assert required_blocks_my_information_continue("verify\n  new   password") is True


# ----- my_experience.experience_dates_match -----


def test_experience_dates_match_returns_true_when_all_tokens_present() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value=["Engineer", "Acme Corp", "1", "2024", "12", "2025", "Toronto"]
    )

    result = asyncio.run(
        experience_dates_match(
            page,
            {
                "title": "Engineer",
                "start_month": "1",
                "start_year": "2024",
                "end_month": "12",
                "end_year": "2025",
            },
        )
    )
    assert result is True


def test_experience_dates_match_returns_false_when_year_missing() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value=["Engineer", "Acme Corp", "1", "12", "2025"]  # 2024 missing
    )

    result = asyncio.run(
        experience_dates_match(
            page,
            {
                "title": "Engineer",
                "start_month": "1",
                "start_year": "2024",
                "end_month": "12",
                "end_year": "2025",
            },
        )
    )
    assert result is False


def test_experience_dates_match_swallows_evaluate_exceptions() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("boom"))

    result = asyncio.run(
        experience_dates_match(
            page,
            {
                "title": "T",
                "start_month": "1",
                "start_year": "2024",
                "end_month": "12",
                "end_year": "2025",
            },
        )
    )
    assert result is False


# ----- my_experience.write_debug_field_dump -----


def test_write_debug_field_dump_writes_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value={"url": "https://x", "title": "Apply", "fields": []}
    )

    asyncio.run(write_debug_field_dump(page))

    dump_path = tmp_path / "artifacts" / "apply" / "workday-my-experience-fields.json"
    assert dump_path.exists()
    payload = json.loads(dump_path.read_text(encoding="utf-8"))
    assert payload["url"] == "https://x"
    assert payload["fields"] == []


def test_write_debug_field_dump_is_silent_on_failure(monkeypatch, tmp_path: Path) -> None:
    """Diagnostic helper — should never raise even when evaluate blows up."""
    monkeypatch.chdir(tmp_path)
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("boom"))

    # Should not raise.
    asyncio.run(write_debug_field_dump(page))
    dump_path = tmp_path / "artifacts" / "apply" / "workday-my-experience-fields.json"
    assert not dump_path.exists()


# ----- cli.py wrappers still work -----


def test_cli_wrappers_delegate_to_extracted_modules(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    from job_hunt.cli import (
        _workday_experience_dates_match,
        _workday_required_blocks_my_information_continue,
        _write_workday_my_experience_debug,
    )

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=["Engineer", "1", "2024", "12", "2025"])

    assert _workday_required_blocks_my_information_continue("Password") is True
    assert (
        asyncio.run(
            _workday_experience_dates_match(
                page,
                {
                    "title": "Engineer",
                    "start_month": "1",
                    "start_year": "2024",
                    "end_month": "12",
                    "end_year": "2025",
                },
            )
        )
        is True
    )
    # The debug dump path is also covered.
    page2 = MagicMock()
    page2.evaluate = AsyncMock(return_value={"url": "u", "title": "t", "fields": []})
    asyncio.run(_write_workday_my_experience_debug(page2))
    assert (tmp_path / "artifacts" / "apply" / "workday-my-experience-fields.json").exists()
