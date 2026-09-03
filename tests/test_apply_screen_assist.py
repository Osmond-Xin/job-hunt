"""Tests for apply-answers prompt rendering and form-text loading helpers."""

from __future__ import annotations

from pathlib import Path

from job_hunt.services.prompts import render
from job_hunt.nodes.apply_screen_assist import load_form_text


def test_screen_to_answers_prompt_renders_all_inputs() -> None:
    rendered = render(
        "apply_assist/screen_to_answers.md",
        company="Anthropic",
        role="AI Engineer",
        url="https://anthropic.com/jobs/ai",
        form_text="1. Why this role?\n2. Why Anthropic?\n",
        report_section_g="## G) Draft Application Answers\n### 1. Why this role?\n> Existing draft.",
        report_full="(unused when section_g is provided)",
        cv_md="# CV\n- Built X.\n",
    )

    assert "Anthropic" in rendered
    assert "AI Engineer" in rendered
    assert "https://anthropic.com/jobs/ai" in rendered
    assert "Why this role?" in rendered
    assert "Existing draft." in rendered
    assert "Built X." in rendered
    # Tone framework rules are carried by the shared prompt contract.
    assert "I'm choosing you" in rendered
    assert "Reuse Section G first" in rendered


def test_load_form_text_prefers_explicit_string(tmp_path: Path) -> None:
    file_path = tmp_path / "form.txt"
    file_path.write_text("from file\n", encoding="utf-8")

    assert load_form_text("from string", file_path) == "from string"


def test_load_form_text_falls_back_to_file(tmp_path: Path) -> None:
    file_path = tmp_path / "form.txt"
    file_path.write_text("  q1\n  q2  \n", encoding="utf-8")

    assert load_form_text(None, file_path) == "q1\n  q2"


def test_load_form_text_returns_empty_when_neither_supplied() -> None:
    assert load_form_text(None, None) == ""
