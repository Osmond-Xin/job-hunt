from __future__ import annotations

from job_hunt.repositories.tracker_repo import TRACKER_HEADER, parse_tracker_line
from job_hunt.services import tracker_ops

ROW = (
    "| 730 | 2026-08-14 | Hiveway | Forward Deployed Solutions Engineer | 4.25/5 "
    "| Interview | ✅ | reports/hiveway.md | {notes} |"
)


def test_a_note_containing_a_triple_dash_does_not_hide_the_row():
    # The separator test used to match "---" anywhere in the line, so a row
    # whose notes mentioned a markdown rule vanished from every read.
    line = ROW.format(notes="the header sits above a `---` rule and is never sent")
    entry = parse_tracker_line(line)
    assert entry is not None
    assert entry.number == 730
    assert entry.status == "Interview"


def test_the_markdown_separator_row_is_still_skipped():
    assert parse_tracker_line("|---|------|---------|------|-------|") is None
    assert parse_tracker_line("| :--- | ---: | :---: |") is None


def test_the_header_row_is_still_skipped():
    assert parse_tracker_line("| # | Date | Company | Role |") is None


def _verify(tmp_path, *rows):
    path = tmp_path / "applications.md"
    path.write_text(TRACKER_HEADER + "".join(row + "\n" for row in rows), encoding="utf-8")
    return tracker_ops.verify_pipeline(applications_md=path, additions_dir=tmp_path / "none")


def test_a_repeated_row_number_is_an_error(tmp_path):
    rows = [
        ROW.format(notes="first"),
        ROW.format(notes="second"),
    ]
    result = _verify(tmp_path, *rows)
    assert any("row number used 2 times" in error for error in result.errors)


def test_a_row_the_parser_cannot_read_is_an_error(tmp_path):
    broken = "| 731 | 2026-08-14 | Acme |"  # too few columns to parse
    result = _verify(tmp_path, ROW.format(notes="fine"), broken)
    assert any("does not parse" in error for error in result.errors)


def test_a_clean_tracker_still_passes(tmp_path):
    result = _verify(tmp_path, ROW.format(notes="fine"))
    assert result.errors == []
    assert result.entries == 1
