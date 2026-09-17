"""scripts/daily_scan.sh — the launchd-run daily scan wrapper.

Run against a throwaway copy with a fake `.venv/bin/job-hunt`, so the real
scan never runs. Pins the three things Codex found wrong on 2026-09-16: the
exit status, overlapping runs, and a lock left behind by a killed run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_scan.sh"


@pytest.fixture
def checkout(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "daily_scan.sh")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    return tmp_path


def _fake_cli(root: Path, scan_status: int, triage_status: int) -> None:
    cli = root / ".venv" / "bin" / "job-hunt"
    cli.write_text(f'#!/bin/bash\n[ "$1" = scan ] && exit {scan_status}\nexit {triage_status}\n')
    cli.chmod(0o755)


def _run(root: Path) -> int:
    return subprocess.run(["bash", str(root / "scripts" / "daily_scan.sh")], capture_output=True).returncode


@pytest.mark.parametrize("scan, triage, expected", [(0, 0, 0), (3, 0, 3), (0, 4, 4), (3, 4, 3)])
def test_a_failed_step_is_the_scripts_exit_status(checkout, scan, triage, expected):
    _fake_cli(checkout, scan, triage)
    assert _run(checkout) == expected


def test_a_run_whose_lock_holder_is_alive_is_skipped(checkout):
    _fake_cli(checkout, 0, 0)
    lock = checkout / "data" / "locks" / "daily-scan.lock"
    lock.mkdir(parents=True)
    # A live process whose command line is this script.
    holder = subprocess.Popen(["bash", "-c", "sleep 30; true", "daily_scan.sh"])
    try:
        (lock / "pid").write_text(str(holder.pid))
        assert _run(checkout) == 75
        assert lock.exists()  # the live holder's lock is left alone
    finally:
        holder.kill()


def test_a_recycled_pid_owned_by_another_process_does_not_block_the_run(checkout):
    """agy review 2026-09-16: `kill -0` alone read a recycled PID as a live holder."""
    _fake_cli(checkout, 0, 0)
    lock = checkout / "data" / "locks" / "daily-scan.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(os.getpid()))  # pytest, not daily_scan.sh
    assert _run(checkout) == 0


def test_a_lock_with_no_pid_yet_is_a_run_starting_not_a_stale_one(checkout):
    """agy review 2026-09-16: between another run's mkdir and its PID write, an
    empty PID used to be read as stale and the live lock deleted."""
    _fake_cli(checkout, 0, 0)
    lock = checkout / "data" / "locks" / "daily-scan.lock"
    lock.mkdir(parents=True)
    assert _run(checkout) == 75
    assert lock.exists()


def test_a_pid_less_lock_older_than_ten_minutes_is_taken_over(checkout):
    _fake_cli(checkout, 0, 0)
    lock = checkout / "data" / "locks" / "daily-scan.lock"
    lock.mkdir(parents=True)
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert _run(checkout) == 0


def test_a_lock_left_by_a_killed_run_does_not_disable_later_runs(checkout):
    _fake_cli(checkout, 0, 0)
    lock = checkout / "data" / "locks" / "daily-scan.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999999")  # no such process
    assert _run(checkout) == 0
    assert not lock.exists()
    assert "stale lock" in (checkout / "logs" / "daily-scan.log").read_text()
