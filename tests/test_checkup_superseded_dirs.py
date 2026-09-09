"""One output directory per job — re-runs must not leave their old copy behind.

`artifact_paths.run_stem` names a run `<today>-<company>-<role>-<run id>`, so a
re-evaluation always lands in a fresh directory and nothing in the pipeline
notices the previous one. Enforcing "one directory per job" by memory failed on
2026-09-06 (an Xplore cover-letter re-run) and, once this check existed, it
turned out eight jobs had been quietly duplicated since May.
"""

from __future__ import annotations

from pathlib import Path

from job_hunt.services.checkup import superseded_run_dirs


def _mk(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir()
    (d / "Candidate_Resume.pdf").write_bytes(b"%PDF-1.4")
    return d


def test_a_rerun_on_a_later_day_is_reported(tmp_path: Path) -> None:
    _mk(tmp_path, "2026-09-05-xplore-inc-forward-deployed-ai-engineer-4f5cfadf")
    _mk(tmp_path, "2026-09-06-xplore-inc-forward-deployed-ai-engineer-b79d0e08")
    check = superseded_run_dirs(output_dir=tmp_path)
    assert not check.ok
    assert len(check.items) == 1
    assert "keep 2026-09-06-xplore-inc-forward-deployed-ai-engineer-b79d0e08" in check.items[0]
    assert "superseded 2026-09-05-xplore-inc-forward-deployed-ai-engineer-4f5cfadf" in check.items[0]


def test_one_directory_per_job_passes(tmp_path: Path) -> None:
    _mk(tmp_path, "2026-09-05-protocase-ai-engineer-06b63015")
    _mk(tmp_path, "2026-09-05-cambio-ai-engineer-d6ee9ad5")
    assert superseded_run_dirs(output_dir=tmp_path).ok


def test_a_directory_already_marked_superseded_is_not_reported_again(tmp_path: Path) -> None:
    old = _mk(tmp_path, "2026-09-05-xplore-inc-forward-deployed-ai-engineer-4f5cfadf")
    (old / "SUPERSEDED.md").write_text("replaced by the 09-06 run")
    _mk(tmp_path, "2026-09-06-xplore-inc-forward-deployed-ai-engineer-b79d0e08")
    assert superseded_run_dirs(output_dir=tmp_path).ok


def test_empty_identities_are_not_grouped_together(tmp_path: Path) -> None:
    """Two runs whose company AND role slugs were both empty are not the same job."""
    _mk(tmp_path, "2026-05-20---51193061")
    _mk(tmp_path, "2026-06-11---da95d74b")
    assert superseded_run_dirs(output_dir=tmp_path).ok


def test_hand_built_directories_are_skipped(tmp_path: Path) -> None:
    """No run-id suffix means the identity cannot be read; guessing would be worse."""
    _mk(tmp_path, "2026-08-31-cgi-handbuilt")
    _mk(tmp_path, "2026-09-01-cgi-handbuilt")
    assert superseded_run_dirs(output_dir=tmp_path).ok


def test_same_day_reruns_break_the_tie_on_mtime(tmp_path: Path) -> None:
    import os, time
    first = _mk(tmp_path, "2026-09-01-big-viking-games-senior-engineer-8e648eed")
    second = _mk(tmp_path, "2026-09-01-big-viking-games-senior-engineer-8069e8a0")
    os.utime(first, (time.time() - 3600, time.time() - 3600))
    check = superseded_run_dirs(output_dir=tmp_path)
    assert not check.ok
    assert "keep 2026-09-01-big-viking-games-senior-engineer-8069e8a0" in check.items[0]


def test_a_missing_output_dir_fails_loudly_rather_than_reporting_clean(tmp_path: Path) -> None:
    check = superseded_run_dirs(output_dir=tmp_path / "nope")
    assert not check.ok
    assert "not found" in check.detail
