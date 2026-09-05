"""Tests for P2-8 LinkedIn outreach + deep research prompt templates."""

from __future__ import annotations

from job_hunt.services.prompts import render


def test_linkedin_outreach_renders_with_company_and_role() -> None:
    out = render(
        "linkedin_outreach.md",
        company="Anthropic",
        role="AI Engineer",
        jd_text="",
        cv_excerpt="",
    )
    assert "Anthropic" in out and "AI Engineer" in out
    # 3-sentence framework is present
    assert "Hook" in out and "Proof" in out and "Proposal" in out
    # 300-char limit reminder
    assert "300 character" in out
    # shared.md framing rules included
    assert "Ethical Use" in out


def test_linkedin_outreach_handles_optional_jd_and_cv() -> None:
    """Empty jd_text/cv_excerpt should not cause undefined-variable errors."""
    out = render(
        "linkedin_outreach.md",
        company="OpenAI",
        role="Researcher",
        jd_text="",
        cv_excerpt="",
    )
    assert "OpenAI" in out
    # Optional sections collapse when their content is empty
    assert "JD excerpt" not in out
    assert "Candidate CV excerpt" not in out


def test_linkedin_outreach_includes_jd_and_cv_when_provided() -> None:
    out = render(
        "linkedin_outreach.md",
        company="Stripe",
        role="ML Engineer",
        jd_text="We're looking for someone to ship LLM eval pipelines...",
        cv_excerpt="Built closed-loop eval system at FraudShield.",
    )
    assert "JD excerpt" in out
    assert "LLM eval pipelines" in out
    assert "Candidate CV excerpt" in out
    assert "FraudShield" in out


def test_deep_research_renders_six_axes() -> None:
    out = render(
        "deep_research.md",
        company="Cohere",
        role="LLM Engineer",
        jd_text="",
        cv_excerpt="",
    )
    assert "Cohere" in out and "LLM Engineer" in out
    # 6 numbered axes
    for axis in ("AI / product strategy", "Recent moves", "Engineering culture",
                  "Likely challenges", "Competitors and differentiation",
                  "Candidate's angle"):
        assert axis in out
    assert "Ethical Use" in out  # shared.md included


def test_project_eval_prompt_renders_scorecard() -> None:
    out = render(
        "project_eval.md",
        project_idea="Build a public LLM eval dashboard for job application agents.",
        role_context="AI platform roles",
        cv_excerpt="Built agentic pipelines with Playwright.",
    )
    assert "Portfolio Project Evaluation" in out
    assert "Target-role signal" in out
    assert "BUILD" in out and "PIVOT TO" in out
    assert "LLM eval dashboard" in out
    assert "Ethical Use" in out


def test_training_eval_prompt_renders_verdicts() -> None:
    out = render(
        "training_eval.md",
        training_option="An LLM observability certification",
        role_context="AI engineer roles",
        cv_excerpt="Built data dashboards.",
    )
    assert "Training / Certification Evaluation" in out
    assert "DO WITH TIMEBOX" in out
    assert "Opportunity cost" in out
    assert "LLM observability certification" in out
    assert "Ethical Use" in out
