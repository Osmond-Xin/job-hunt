"""What `evaluate-batch --skip-evaluated` is allowed to skip.

The skip decision spends or saves real money in one direction and silently
loses a job in the other, so it may only act on the identity the posting page
itself states.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunt import cli

_TRACKER = """| # | Date | Company | Role | Score | Status | PDF | Report | Notes |
| 689 | 2026-05-11 | Cohere | Software Engineer, Search Applications | 3.6/5 | Evaluated | ✅ | r.md | apply |
| 732 | 2026-08-15 | CSC Generation | AI Solutions Engineer | 3.3/5 | Evaluated | ❌ | r.md | skip |
"""


@pytest.fixture
def tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "applications.md").write_text(_TRACKER, encoding="utf-8")
    return tmp_path


def _pages(monkeypatch: pytest.MonkeyPatch, pages: dict[str, dict[str, str]]) -> None:
    # _partition_already_evaluated (cli.evaluation) calls _extract_loop_url_metadata
    # as a bare name resolved from its own module's import of services.web_extract,
    # so the patch has to land on cli.evaluation's copy, not job_hunt.cli's re-export.
    monkeypatch.setattr(cli.evaluation, "_extract_loop_url_metadata", lambda url: pages.get(url, {}))


def test_a_page_naming_no_company_is_run_not_guessed_at(
    tracker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2026-08-17 case, which cost a real job out of that day's batch.

    SIGA's Applications Systems Analyst came through an aggregator that sets no
    company. The old code fuzzy-matched the JD text against the whole tracker,
    hit Cohere's "Software Engineer, Search Applications" at 1.0, and dropped
    the target as already evaluated.
    """
    url = "https://www.adzuna.ca/details/5841507731"
    _pages(monkeypatch, {url: {"company": "", "title": "Applications Systems Analyst - adzuna.ca"}})

    runnable, skipped = cli._partition_already_evaluated([url])

    assert runnable == [url]
    assert skipped == []


def test_a_board_suffix_does_not_hide_a_duplicate(
    tracker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"AI Solutions Engineer - adzuna.ca" is tracker #732 wearing a brand."""
    url = "https://www.adzuna.ca/details/5833584853"
    _pages(
        monkeypatch,
        {url: {"company": "CSC Generation", "title": "AI Solutions Engineer - adzuna.ca"}},
    )

    runnable, skipped = cli._partition_already_evaluated([url])

    assert runnable == []
    assert [entry.number for _target, entry in skipped] == [732]


def test_an_unrelated_posting_at_the_same_employer_still_runs(
    tracker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://boards.example.invalid/cohere/platform-engineer"
    _pages(monkeypatch, {url: {"company": "Cohere", "title": "Platform Engineer"}})

    runnable, _skipped = cli._partition_already_evaluated([url])

    assert runnable == [url]
