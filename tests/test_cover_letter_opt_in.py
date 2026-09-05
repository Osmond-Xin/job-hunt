"""A cover letter is opt-in — nothing on the default path may pay to produce one."""

from __future__ import annotations

import json

import pytest

from job_hunt.services.prompts import render

BLOCKS = {
    "role_summary": "",
    "cv_match": "",
    "level_strategy": "",
    "comp_research": "",
    "personalization": "",
    "archetype": "",
}


def _scoring_prompt(*, generate_cover_letter: bool, mode: str = "full") -> str:
    return render(
        "evaluate/score_and_recommend.md",
        evaluation_blocks=BLOCKS,
        mode=mode,
        cv="",
        article_digest="",
        jd_text="",
        immigration_context="",
        generate_cover_letter=generate_cover_letter,
    )


def _pdf_content(prompt: str) -> dict:
    """Parse the JSON skeleton the prompt asks the model to fill in."""
    start = prompt.rindex("```json", 0, prompt.index('"weighted_total"')) + len("```json")
    skeleton = prompt[start : prompt.index("```", start)]
    parsed = json.loads(
        skeleton.replace('"apply|maybe|skip"', '"skip"').replace('"..."', '"x"')
    )
    return parsed["pdf_content"]


@pytest.mark.parametrize("mode", ["full", "student"])
def test_scoring_prompt_omits_cover_letter_body_by_default(mode: str) -> None:
    """Generating 3–4 unread paragraphs on every run is the cost this prevents."""
    prompt = _scoring_prompt(generate_cover_letter=False, mode=mode)
    assert "cover_letter_body" not in _pdf_content(prompt)
    assert "Do **not** emit `cover_letter_body`" in prompt


@pytest.mark.parametrize("mode", ["full", "student"])
def test_scoring_prompt_requests_cover_letter_body_when_asked(mode: str) -> None:
    prompt = _scoring_prompt(generate_cover_letter=True, mode=mode)
    assert "cover_letter_body" in _pdf_content(prompt)


@pytest.mark.parametrize("generate_cover_letter", [True, False])
def test_scoring_prompt_skeleton_stays_valid_json(generate_cover_letter: bool) -> None:
    """The conditional must not leave a dangling comma in the example schema."""
    content = _pdf_content(_scoring_prompt(generate_cover_letter=generate_cover_letter))
    assert {"summary_angle", "top_bullets", "keywords"} <= set(content)


def test_cv_pdf_never_embeds_a_cover_letter() -> None:
    """Turning the standalone letter off used to staple it into the résumé instead."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "job_hunt"
        / "nodes"
        / "pdf.py"
    ).read_text(encoding="utf-8")
    assert 'embedded_cover_letter = ""' in source
