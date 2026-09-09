"""Characterisation tests for the three keys that authorise an auto-submit.

Written before the apply.py seam refactor moves any of this, and deliberately
pinning *behaviour* rather than structure: what these assert is that
``_open_apply_page`` is reached with ``auto_submit=False`` unless all three keys
are on. Where that AND is computed, and which module computes it after the
refactor, is exactly what is allowed to change.

The gate is three keys because the thing on the other side is irreversible: a
real application, under a real name, to a real employer. `--auto-submit` alone
is a flag in shell history; `profile.yml` alone is a file someone edited months
ago; ``mode`` alone is a setting for a different purpose. Any one of them being
enough would make an accident cheap.

See docs/apply-seam-plan.md §3.7 -- this is Phase 1's first obligation, because
the calculation lives in a command body that Phase 1 partially moves.
"""

from __future__ import annotations

import pytest

from job_hunt.cli import apply_assist


_DEFAULTS = dict(
    tracker_id=None,
    company="Acme",
    role="Engineer",
    pdf=None,
    cover_letter_pdf=None,
    no_browser=False,
    headless=True,
    auto_fill=False,
    fill_only=False,
    confirmed=False,
    low_score_override=False,
)


class _StubSettings:
    """Enough of Settings for apply_assist to reach the auto-submit decision."""

    class _Paths:
        tracker = "data/applications.md"

    paths = _Paths()


class _Captured(Exception):
    """Raised out of the patched _open_apply_page to stop before the browser."""

    def __init__(self, auto_submit: bool) -> None:
        super().__init__(f"auto_submit={auto_submit}")
        self.auto_submit = auto_submit


@pytest.fixture
def captured_auto_submit(monkeypatch, tmp_path):
    """Run apply_assist far enough to see what it decided, and no further.

    Returns a callable: (cli_flag, profile_enabled, mode) -> the auto_submit
    value apply_assist would have handed the browser session.
    """

    def run(*, cli_flag: bool, profile_enabled: bool, mode: str) -> bool:
        # Never let the real one run: it reads .env into os.environ for the whole
        # pytest process, and one of the keys there points the red team at a
        # paid CLI. A later test that expects "no reviewer available" then fires
        # a real review instead. Nothing here needs real settings.
        monkeypatch.setattr("job_hunt.cli.apply.load_settings", lambda: _StubSettings())
        monkeypatch.setattr(
            "job_hunt.services.profile_loader._apply_profile_values",
            lambda: {"apply_auto_submit_enabled": profile_enabled},
        )
        monkeypatch.setattr(
            "job_hunt.cli.apply._apply_profile_values",
            lambda: {"apply_auto_submit_enabled": profile_enabled},
        )
        monkeypatch.setattr("job_hunt.services.profile_loader.current_mode", lambda: mode)

        async def _fake_open(*args, **kwargs):
            raise _Captured(bool(kwargs.get("auto_submit")))

        monkeypatch.setattr("job_hunt.cli.apply._open_apply_page", _fake_open)
        monkeypatch.setattr(
            "job_hunt.cli.apply._apply_artifact_dir", lambda *a, **k: tmp_path / "art"
        )
        monkeypatch.setattr(
            "job_hunt.cli.apply._load_apply_report_context", lambda **k: {}
        )
        monkeypatch.setattr(
            "job_hunt.cli.apply._load_saved_apply_answers", lambda *a, **k: []
        )

        with pytest.raises(_Captured) as excinfo:
            apply_assist(
                url="https://acme.wd5.myworkdayjobs.com/job/1",
                auto_submit=cli_flag,
                **_DEFAULTS,
            )
        return excinfo.value.auto_submit

    return run


def test_all_three_keys_on_is_the_only_way_through(captured_auto_submit) -> None:
    assert captured_auto_submit(cli_flag=True, profile_enabled=True, mode="full") is True


def test_without_the_cli_flag_no_auto_submit(captured_auto_submit) -> None:
    """The two standing settings must never authorise on their own — otherwise
    every ordinary `job-hunt apply` on a configured machine could submit."""
    assert captured_auto_submit(cli_flag=False, profile_enabled=True, mode="full") is False


def test_without_the_profile_flag_no_auto_submit(captured_auto_submit) -> None:
    """A `--auto-submit` typed (or recalled from shell history) on a machine
    that never opted in must not submit."""
    assert captured_auto_submit(cli_flag=True, profile_enabled=False, mode="full") is False


def test_student_mode_refuses_even_with_both_flags(captured_auto_submit) -> None:
    """Mode is the third key and it is a veto, not a tiebreak: co-op and intern
    forms carry per-employer variance the auto-submit path does not handle.
    See docs/design-notes.md §N.3."""
    assert captured_auto_submit(cli_flag=True, profile_enabled=True, mode="student") is False


def test_no_key_at_all(captured_auto_submit) -> None:
    assert captured_auto_submit(cli_flag=False, profile_enabled=False, mode="student") is False


@pytest.mark.parametrize(
    "cli_flag,profile_enabled,mode",
    [
        (True, True, "student"),
        (True, False, "full"),
        (False, True, "full"),
        (True, False, "student"),
        (False, False, "full"),
        (False, True, "student"),
        (False, False, "student"),
    ],
)
def test_every_incomplete_combination_refuses(
    captured_auto_submit, cli_flag, profile_enabled, mode
) -> None:
    """Exhaustive over the seven ways to be short of all three keys. A refactor
    that turns the AND into a chain of ifs can pass one of these and fail
    another, so all seven are asserted rather than a representative sample."""
    assert (
        captured_auto_submit(cli_flag=cli_flag, profile_enabled=profile_enabled, mode=mode)
        is False
    )


def test_an_ignored_flag_says_why(captured_auto_submit, capsys) -> None:
    """Silently downgrading to manual submit would leave the operator believing
    an application was sent. The refusal has to be visible."""
    captured_auto_submit(cli_flag=True, profile_enabled=False, mode="full")
    out = capsys.readouterr().out
    assert "auto-submit ignored" in out.lower()
    assert "auto_submit_enabled" in out


def test_student_mode_refusal_names_the_mode(captured_auto_submit, capsys) -> None:
    captured_auto_submit(cli_flag=True, profile_enabled=True, mode="student")
    out = capsys.readouterr().out
    assert "auto-submit ignored" in out.lower()
    assert "student" in out
