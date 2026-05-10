"""Tests for tracker_ops merge/dedup/normalize/verify."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunt.repositories.tracker_repo import TRACKER_HEADER, TrackerEntry
from job_hunt.services import tracker_ops


def _write_apps(path: Path, rows: list[str]) -> None:
    body = TRACKER_HEADER + "\n".join(rows) + "\n"
    path.write_text(body, encoding="utf-8")


def test_merge_appends_new_entry_and_archives_tsv(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    additions = tmp_path / "additions"
    _write_apps(apps, [])

    entry = TrackerEntry(
        number=1,
        date="2026-05-09",
        company="Anthropic",
        role="AI Engineer",
        score="4.6/5",
        status="Evaluated",
        pdf="✅",
        report="reports/0001-anthropic.md",
        notes="Score 4.6 — strong match.",
    )
    tsv_path = tracker_ops.stage_addition(entry, additions_dir=additions)
    assert tsv_path.exists()

    result = tracker_ops.merge(additions_dir=additions, applications_md=apps)

    assert result.added == 1
    assert result.updated == 0
    assert result.skipped == 0
    assert any("Anthropic" in line for line in apps.read_text(encoding="utf-8").splitlines())
    # processed file moved out of additions root into additions/merged/<date>/
    assert not tsv_path.exists()
    assert result.moved
    assert (additions / "merged") in result.moved[0].parents


def test_merge_updates_existing_entry_when_score_higher(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    additions = tmp_path / "additions"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Anthropic | Senior Software Engineer | 3.8/5 | Evaluated | ❌ | [1](reports/0001.md) | initial pass |",
        ],
    )
    new_entry = TrackerEntry(
        number=99,
        date="2026-05-09",
        company="Anthropic",
        role="Senior Software Engineering Platform",
        score="4.5/5",
        status="Evaluated",
        pdf="✅",
        report="[1](reports/0001.md)",  # same report number as existing → triggers update path
        notes="Re-evaluation",
    )
    tracker_ops.stage_addition(new_entry, additions_dir=additions)

    result = tracker_ops.merge(additions_dir=additions, applications_md=apps)

    assert result.updated == 1
    assert result.added == 0
    assert result.skipped == 0
    line = next(l for l in apps.read_text(encoding="utf-8").splitlines() if l.startswith("| 1 "))
    assert "4.5/5" in line
    assert "Re-eval 2026-05-09" in line


def test_merge_skips_lower_score_duplicate(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    additions = tmp_path / "additions"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | OpenAI | Senior Research Engineer | 4.7/5 | Applied | ✅ | [1](reports/0001.md) | strong |",
        ],
    )
    weaker = TrackerEntry(
        number=2,
        date="2026-05-09",
        company="OpenAI",
        role="Senior Research Engineer Compute",
        score="4.0/5",
        status="Evaluated",
        pdf="❌",
        report="[2](reports/0002.md)",
        notes="follow-up",
    )
    tracker_ops.stage_addition(weaker, additions_dir=additions)

    result = tracker_ops.merge(additions_dir=additions, applications_md=apps)

    assert result.added == 0
    assert result.updated == 0
    assert result.skipped == 1


def test_merge_dry_run_does_not_move_files(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    additions = tmp_path / "additions"
    _write_apps(apps, [])
    entry = TrackerEntry(
        number=1,
        date="2026-05-09",
        company="Acme",
        role="Engineer",
        score="4.0/5",
        status="Evaluated",
        pdf="❌",
        report="reports/0001.md",
        notes="",
    )
    tsv_path = tracker_ops.stage_addition(entry, additions_dir=additions)

    result = tracker_ops.merge(additions_dir=additions, applications_md=apps, dry_run=True)

    assert result.added == 1
    assert tsv_path.exists()
    # apps file untouched
    assert "Acme" not in apps.read_text(encoding="utf-8")


def test_dedup_keeps_highest_score_and_promotes_status(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Stripe | Senior Software Engineer | 4.2/5 | Interview | ✅ | reports/0001.md | first eval |",
            "| 2 | 2026-04-15 | Stripe | Senior Software Engineering Platform | 4.5/5 | Evaluated | ❌ | reports/0002.md | re-eval |",
        ],
    )

    result = tracker_ops.dedup(applications_md=apps)

    assert result.removed == 1
    # the higher-score row (#2) is kept and gets promoted to Interview from #1
    assert result.promoted and result.promoted[0][2] == "Interview"
    remaining = [
        line for line in apps.read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and "---" not in line and "Stripe" in line
    ]
    assert len(remaining) == 1
    assert "Interview" in remaining[0]
    assert "4.5/5" in remaining[0]


def test_dedup_dry_run_does_not_modify_file(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    rows = [
        "| 1 | 2026-04-01 | Foo | Senior Software Engineer | 3.5/5 | Evaluated | ❌ | reports/0001.md |  |",
        "| 2 | 2026-04-02 | Foo | Senior Software Engineering | 4.0/5 | Evaluated | ❌ | reports/0002.md |  |",
    ]
    _write_apps(apps, rows)
    before = apps.read_text(encoding="utf-8")
    tracker_ops.dedup(applications_md=apps, dry_run=True)
    assert apps.read_text(encoding="utf-8") == before


def test_normalize_rewrites_aliases_and_strips_bold(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Acme | Engineer | **4.0/5** | aplicado | ❌ | reports/0001.md |  |",
            "| 2 | 2026-04-02 | Beta | Manager | 3.5/5 | Cerrada | ❌ | reports/0002.md |  |",
            "| 3 | 2026-04-03 | Gamma | Lead | 4.5/5 | Applied | ✅ | reports/0003.md |  |",
        ],
    )

    result = tracker_ops.normalize_statuses(applications_md=apps)

    assert result.changes == 2
    text = apps.read_text(encoding="utf-8")
    assert "| Applied |" in text
    assert "| Discarded |" in text
    # bold stripped from score column
    assert "**4.0/5**" not in text
    # already-canonical row untouched
    assert "Gamma" in text and "Applied" in text


def test_normalize_flags_unknown_status(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Acme | Engineer | 4.0/5 | TotallyMadeUp | ❌ | reports/0001.md |  |",
        ],
    )
    result = tracker_ops.normalize_statuses(applications_md=apps)
    assert result.unknowns and result.unknowns[0][1] == "TotallyMadeUp"
    assert result.changes == 0


def test_verify_reports_pending_tsvs_and_invalid_score(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    additions = tmp_path / "additions"
    additions.mkdir()
    (additions / "00001-foo.tsv").write_text(
        "1\t2026-05-09\tFoo\tEngineer\tEvaluated\t4.0/5\t❌\treports/0001.md\t\n",
        encoding="utf-8",
    )
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Foo | Engineer | wrong-score | Evaluated | ❌ | reports/0001.md |  |",
            "| 2 | 2026-04-02 | Foo | Engineer | 4.0/5 | bogus-status | ❌ | reports/0002.md |  |",
        ],
    )

    result = tracker_ops.verify_pipeline(applications_md=apps, additions_dir=additions)

    assert result.entries == 2
    assert any("invalid score" in e for e in result.errors)
    assert any("non-canonical status" in e for e in result.errors)
    assert any("pending TSVs" in w for w in result.warnings)
    assert any("possible duplicates" in w for w in result.warnings)


def test_verify_clean_tracker_reports_no_errors(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Foo | Engineer | 4.0/5 | Evaluated | ❌ | reports/0001.md |  |",
        ],
    )
    result = tracker_ops.verify_pipeline(applications_md=apps)
    assert result.errors == []
    assert result.entries == 1


def test_merge_does_not_dedup_by_number_alone_across_companies(tmp_path: Path) -> None:
    """Regression: a TSV with num=5 / Acme must NOT collide with #5 / Beta."""
    apps = tmp_path / "applications.md"
    additions = tmp_path / "additions"
    _write_apps(
        apps,
        [
            "| 5 | 2026-04-01 | Beta | Manager | 4.0/5 | Applied | ✅ | reports/0005-beta.md |  |",
        ],
    )
    new_entry = TrackerEntry(
        number=5,  # same number, different company
        date="2026-05-09",
        company="Acme",
        role="Engineer",
        score="3.8/5",
        status="Evaluated",
        pdf="❌",
        report="reports/9999-acme.md",
        notes="",
    )
    tracker_ops.stage_addition(new_entry, additions_dir=additions)

    result = tracker_ops.merge(additions_dir=additions, applications_md=apps)

    # Must be added, not skipped/updated. Acme gets a fresh next-number.
    assert result.added == 1, result.actions
    assert result.updated == 0
    assert result.skipped == 0
    text = apps.read_text(encoding="utf-8")
    assert "Beta" in text and "Manager" in text
    assert "Acme" in text and "Engineer" in text


def test_dedup_does_not_write_bak_file(tmp_path: Path) -> None:
    """Regression: dedup used to write applications.md.bak; now it doesn't."""
    apps = tmp_path / "applications.md"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Stripe | Senior Software Engineer | 4.2/5 | Evaluated | ❌ | reports/0001.md |  |",
            "| 2 | 2026-04-15 | Stripe | Senior Software Engineering | 4.5/5 | Evaluated | ❌ | reports/0002.md |  |",
        ],
    )
    tracker_ops.dedup(applications_md=apps)
    assert not apps.with_suffix(".md.bak").exists()


def test_parse_tsv_addition_handles_swapped_status_score(tmp_path: Path) -> None:
    additions = tmp_path
    swapped = additions / "00001-foo.tsv"
    swapped.write_text(
        # status/score swapped (4.0/5 first, then Applied)
        "1\t2026-05-09\tFoo\tEngineer\t4.0/5\tApplied\t❌\treports/0001.md\t\n",
        encoding="utf-8",
    )
    parsed = tracker_ops.parse_tsv_addition(swapped)
    assert parsed is not None
    assert parsed.status == "Applied"
    assert parsed.score == "4.0/5"
