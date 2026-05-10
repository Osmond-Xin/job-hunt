"""Tests for P1-4 pipeline.md URL inbox."""

from __future__ import annotations

from pathlib import Path

from job_hunt.services import pipeline_inbox
from job_hunt.services.pipeline_inbox import EntryStatus, InboxEntry


def test_ensure_exists_creates_skeleton(tmp_path: Path) -> None:
    p = tmp_path / "pipeline.md"
    pipeline_inbox.ensure_exists(p)
    text = p.read_text(encoding="utf-8")
    assert "## Pending" in text
    assert "## Processed" in text


def test_add_appends_pending_entry(tmp_path: Path) -> None:
    p = tmp_path / "pipeline.md"
    assert pipeline_inbox.add(
        "https://job-boards.greenhouse.io/anthropic/jobs/123",
        company="Anthropic",
        role="AI Engineer",
        path=p,
    )
    entries = pipeline_inbox.parse(p)
    assert len(entries) == 1
    e = entries[0]
    assert e.url.endswith("/123") and e.company == "Anthropic"
    assert e.status is EntryStatus.PENDING


def test_add_dedups_same_url(tmp_path: Path) -> None:
    p = tmp_path / "pipeline.md"
    assert pipeline_inbox.add("https://example.com/a", path=p)
    assert not pipeline_inbox.add("https://example.com/a", path=p)
    assert len(pipeline_inbox.parse(p)) == 1


def test_mark_processed_moves_entry(tmp_path: Path) -> None:
    p = tmp_path / "pipeline.md"
    pipeline_inbox.add("https://example.com/a", company="Acme", role="Eng", path=p)
    moved = pipeline_inbox.mark_processed(
        "https://example.com/a",
        tracker_id=42,
        score="4.2/5",
        pdf_check="✅",
        company="Acme",
        role="Eng",
        path=p,
    )
    assert moved
    entries = pipeline_inbox.parse(p)
    assert len(entries) == 1
    e = entries[0]
    assert e.status is EntryStatus.PROCESSED
    assert e.tracker_id == 42
    assert e.score == "4.2/5"


def test_mark_processed_returns_false_when_not_pending(tmp_path: Path) -> None:
    p = tmp_path / "pipeline.md"
    pipeline_inbox.ensure_exists(p)
    assert not pipeline_inbox.mark_processed(
        "https://example.com/missing", tracker_id=1, score="4/5", path=p
    )


def test_mark_error_keeps_entry_in_pending_section(tmp_path: Path) -> None:
    p = tmp_path / "pipeline.md"
    pipeline_inbox.add("https://example.com/x", path=p)
    assert pipeline_inbox.mark_error(
        "https://example.com/x", note="login required", path=p
    )
    entries = pipeline_inbox.parse(p)
    assert len(entries) == 1
    assert entries[0].status is EntryStatus.ERROR
    assert entries[0].note == "login required"


def test_list_entries_filters_by_status(tmp_path: Path) -> None:
    p = tmp_path / "pipeline.md"
    pipeline_inbox.add("https://example.com/a", path=p)
    pipeline_inbox.add("https://example.com/b", path=p)
    pipeline_inbox.mark_processed("https://example.com/a", tracker_id=1, score="4/5", path=p)
    pending = pipeline_inbox.list_entries(status=EntryStatus.PENDING, path=p)
    processed = pipeline_inbox.list_entries(status=EntryStatus.PROCESSED, path=p)
    assert len(pending) == 1 and pending[0].url.endswith("/b")
    assert len(processed) == 1 and processed[0].url.endswith("/a")


def test_render_round_trip_for_each_status() -> None:
    pending = InboxEntry(url="https://example.com", company="A", role="B")
    processed = InboxEntry(
        url="https://example.com", company="A", role="B",
        status=EntryStatus.PROCESSED, tracker_id=7, score="4.5/5", pdf_check="✅",
    )
    error = InboxEntry(url="https://example.com", status=EntryStatus.ERROR, note="403")

    for original in (pending, processed, error):
        parsed = pipeline_inbox.parse_entry(original.render())
        assert parsed is not None
        assert parsed.status is original.status
        assert parsed.url == original.url
        if original.status is EntryStatus.PROCESSED:
            assert parsed.tracker_id == original.tracker_id


def test_parse_entry_returns_none_for_non_list_lines() -> None:
    assert pipeline_inbox.parse_entry("# Heading") is None
    assert pipeline_inbox.parse_entry("regular text") is None
    assert pipeline_inbox.parse_entry("## Pending") is None
