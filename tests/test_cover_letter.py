"""Tests for the cover-letter template rendering and the cover_letter node split helper."""

from __future__ import annotations

import asyncio

from job_hunt.models.evaluation import EvaluationScores
from job_hunt.models.job import CandidateProfile
from job_hunt.nodes.cover_letter import _split_paragraphs, generate_cover_letter
from job_hunt.nodes.pdf import artifact_template_env


def test_cover_letter_template_renders_paragraphs_and_contact() -> None:
    template = artifact_template_env().get_template("cover-letter.html.j2")

    html = template.render(
        profile=CandidateProfile(
            name="Example Candidate",
            email="candidate.com",
            linkedin="https://www.linkedin.com/in/example-candidate/",
        ),
        company="Anthropic",
        role="AI Engineer",
        date="May 09, 2026",
        paragraphs=[
            "First paragraph hooking the reader.",
            "Second paragraph on company fit.",
            "Third paragraph on compound differentiator.",
        ],
        greeting="Dear Hiring Team,",
        closing="Sincerely,",
    )

    assert "Anthropic" in html
    assert "Re: AI Engineer" in html
    assert "Dear Hiring Team," in html
    assert "First paragraph hooking the reader." in html
    assert "Second paragraph on company fit." in html
    assert "Third paragraph on compound differentiator." in html
    assert html.count("<p>") == 3
    assert 'href="mailto:candidate.com"' in html
    # gradient rule renders to keep visual continuity with cv.html.j2
    assert "linear-gradient" in html


def test_split_paragraphs_strips_headings_and_collapses_whitespace() -> None:
    body_md = (
        "# Cover Letter\n"
        "\n"
        "I built an end-to-end AI agent platform that runs in production.\n"
        "Your `Claude Code` orchestration role maps directly to that experience.\n"
        "\n"
        "## Why Anthropic\n"
        "\n"
        "I have been using Claude daily for the past year.\n"
        "\n"
        "I sit at the intersection of product and AI engineering.\n"
    )
    paragraphs = _split_paragraphs(body_md)

    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("I built an end-to-end AI agent platform")
    # newlines collapsed inside a paragraph
    assert "\n" not in paragraphs[0]
    assert paragraphs[1] == "I have been using Claude daily for the past year."
    assert paragraphs[2] == "I sit at the intersection of product and AI engineering."


def test_split_paragraphs_returns_empty_list_for_blank_input() -> None:
    assert _split_paragraphs("") == []
    assert _split_paragraphs("   \n  \n  ") == []


# ----- low-score early exit -----


def test_cover_letter_skipped_when_flag_off() -> None:
    """Without the generate_cover_letter flag, the node is a no-op even when
    a generate_pdf=True score exists."""
    state = {
        "generate_cover_letter": False,
        "scores": EvaluationScores(weighted_total=4.6, generate_pdf=True),
    }
    result = asyncio.run(generate_cover_letter(state, None))
    assert result == {"errors": []}


def test_cover_letter_skipped_when_score_says_skip_pdf() -> None:
    """The flag is on but the scorer marked generate_pdf=False (a SKIP).
    The cover letter must short-circuit before any LLM or Playwright work."""
    state = {
        "generate_cover_letter": True,
        "scores": EvaluationScores(weighted_total=3.2, generate_pdf=False),
        # Intentionally omit jd_meta / cv / templates — if the node reached
        # any LLM render path it would explode here. The short-circuit proves
        # we never get past the gate.
    }
    result = asyncio.run(generate_cover_letter(state, None))
    assert result["cover_letter_path"] is None
    assert result["errors"] == []


def test_split_paragraphs_strips_inline_markdown_markers() -> None:
    paragraphs = _split_paragraphs(
        "I built `job-hunt` — an **8-node** LangGraph agent with *structured* outputs.\n"
    )
    assert paragraphs == ["I built job-hunt — an 8-node LangGraph agent with structured outputs."]
