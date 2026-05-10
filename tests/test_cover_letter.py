"""Tests for the cover-letter template rendering and the cover_letter node split helper."""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from job_hunt.models.job import CandidateProfile
from job_hunt.nodes.cover_letter import _split_paragraphs


def test_cover_letter_template_renders_paragraphs_and_contact() -> None:
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
    template = env.get_template("cover-letter.html.j2")

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
