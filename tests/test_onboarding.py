"""Tests for the conversational onboarding helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

from job_hunt.services import onboarding


def _scripted_provider(answers: dict[str, str]):
    """Return an input_provider that responds with `answers[key]` keyed by question index."""
    captured: list[tuple[str, str]] = []
    keys = [k for k, _, _ in onboarding._QUESTIONS]
    iter_keys = iter(keys)

    def provider(prompt: str, default: str) -> str:
        key = next(iter_keys)
        captured.append((prompt, default))
        return answers.get(key, "")

    return provider, captured


def test_collect_narrative_records_provided_answers() -> None:
    provider, _ = _scripted_provider(
        {
            "superpower": "20yr SWE + product compound",
            "energy": "0-to-1 prototyping",
            "best_achievement": "1st place H.E.A.D. competition",
        }
    )
    answers = onboarding.collect_narrative(provider)
    assert answers.superpower == "20yr SWE + product compound"
    assert answers.energy == "0-to-1 prototyping"
    assert answers.best_achievement == "1st place H.E.A.D. competition"
    # unset fields stay empty
    assert answers.exit_story == ""


def test_is_meaningful_requires_30_chars() -> None:
    empty = onboarding.NarrativeAnswers()
    assert not empty.is_meaningful()
    short = onboarding.NarrativeAnswers(superpower="x")
    assert not short.is_meaningful()
    real = onboarding.NarrativeAnswers(
        superpower="Built AI agent platform that ran in production for a year",
    )
    assert real.is_meaningful()


def test_merge_into_profile_writes_narrative_block(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    profile.write_text("full_name: Example Candidate\nemail: candidate@example.com\n", encoding="utf-8")
    answers = onboarding.NarrativeAnswers(
        superpower="Product × Tech compound — speaks both",
        exit_story="Built and ran a SaaS for 5 years; now applying systems thinking to AI orchestration.",
    )
    wrote = onboarding.merge_into_profile(profile, answers)
    assert wrote is True
    data = yaml.safe_load(profile.read_text(encoding="utf-8"))
    assert data["full_name"] == "Example Candidate"  # preserved
    assert data["narrative"]["superpower"].startswith("Product × Tech")
    assert "exit_story" in data["narrative"]


def test_merge_skips_empty_answers(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    profile.write_text("full_name: x\n", encoding="utf-8")
    empty = onboarding.NarrativeAnswers()
    wrote = onboarding.merge_into_profile(profile, empty)
    assert wrote is False
    # file untouched
    assert "narrative" not in profile.read_text(encoding="utf-8")


def test_has_narrative_detects_existing_block(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    profile.write_text(
        yaml.safe_dump({"full_name": "x", "narrative": {"superpower": "y"}}),
        encoding="utf-8",
    )
    assert onboarding.has_narrative(profile)


def test_has_narrative_false_for_empty_block(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    profile.write_text(
        yaml.safe_dump({"full_name": "x", "narrative": {"superpower": ""}}),
        encoding="utf-8",
    )
    assert not onboarding.has_narrative(profile)


def test_has_narrative_false_when_no_file(tmp_path: Path) -> None:
    assert not onboarding.has_narrative(tmp_path / "missing.yml")


def test_run_guided_questions_end_to_end(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yml"
    profile.write_text("full_name: Example Candidate\n", encoding="utf-8")
    provider, _ = _scripted_provider(
        {
            "superpower": "Compound product+engineering builder with shipping discipline",
            "best_achievement": "Sold the SaaS in 2025",
        }
    )
    wrote = onboarding.run_guided_questions(profile, provider)
    assert wrote is True
    assert onboarding.has_narrative(profile)


def test_final_message_lists_status() -> None:
    msg = onboarding.final_message(cv_present=True, profile_present=True, tracker_present=False)
    assert "cv.md" in msg
    assert "data/applications.md" in msg
    assert "✓" in msg and "✗" in msg
