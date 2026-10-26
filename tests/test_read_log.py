"""Hand-read verdicts keep triage from offering the same posting twice."""

from __future__ import annotations

from datetime import date

import pytest

from job_hunt.services import read_log


def test_collapse_is_settled_a_later_fit_reopens_it_and_second_look_cuts_count(tmp_path) -> None:
    log = tmp_path / "postings-read.tsv"
    second = tmp_path / "second-look-log.tsv"
    second.write_text(
        "date\turl\tcompany\trole\twhy_cut\tverdict\trescue\tkill\n"
        "2026-10-20\thttps://x/cut\tA\tB\tlarge employer\tcut confirmed\t\t5+ years\n"
        "2026-10-20\thttps://x/rescued\tA\tB\tlarge employer\trescued\tLLM\t\n",
        encoding="utf-8",
    )
    read_log.record("https://x/gone", "COLLAPSE", "5+ yrs", path=log)
    read_log.record("https://x/fit", "FIT", "agentic", path=log)
    read_log.record("https://x/flip", "COLLAPSE", "misread", path=log)
    read_log.record("https://x/flip", "FIT", "re-read", path=log)

    assert read_log.settled_urls(log, second) == {"https://x/gone", "https://x/cut"}


def test_an_unread_page_cannot_be_recorded(tmp_path) -> None:
    with pytest.raises(ValueError):
        read_log.record("https://x", "UNREAD", "503", path=tmp_path / "log.tsv")


def test_a_hand_fit_beats_a_second_look_cut_and_a_backfill_cannot_override_a_newer_fit(tmp_path) -> None:
    """Spec: FIT stays visible. Code review + OpenCode red team 2026-10-24."""
    log = tmp_path / "postings-read.tsv"
    second = tmp_path / "second-look-log.tsv"
    second.write_text(
        "date\turl\tcompany\trole\twhy_cut\tverdict\trescue\tkill\n"
        "2026-10-20\thttps://x/a\tA\tB\tlarge employer\tcut confirmed\t\t5+ years\n",
        encoding="utf-8",
    )
    read_log.record("https://x/a", "FIT", "read by hand", path=log, today=date(2026, 10, 20))
    read_log.record("https://x/b", "FIT", "read", path=log, today=date(2026, 10, 23))
    read_log.record("https://x/b", "COLLAPSE", "back-filled", path=log, today=date(2026, 10, 22))

    assert read_log.settled_urls(log, second) == set()



def test_an_empty_log_file_still_gets_its_header(tmp_path) -> None:
    log = tmp_path / "postings-read.tsv"
    log.write_text("", encoding="utf-8")
    read_log.record("https://x/a", "COLLAPSE", "5+ yrs", path=log)
    assert read_log.settled_urls(log, tmp_path / "none.tsv") == {"https://x/a"}
