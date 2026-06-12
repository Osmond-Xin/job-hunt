"""Tests for P2-10: `local:` JD prefix + WebSearch title regex."""

from __future__ import annotations

import asyncio
from pathlib import Path

from job_hunt.services.scan import parse_search_result_title
from job_hunt.services.web_extract import extract_url_text


def test_parse_search_result_at_separator() -> None:
    title, company = parse_search_result_title("Senior AI PM @ EverAI")
    assert title == "Senior AI PM"
    assert company == "EverAI"


def test_parse_search_result_pipe_separator() -> None:
    title, company = parse_search_result_title("Data Scientist | Acme")
    assert title == "Data Scientist"
    assert company == "Acme"


def test_parse_search_result_at_word_separator() -> None:
    title, company = parse_search_result_title("Software Engineer at Stripe")
    assert title == "Software Engineer"
    assert company == "Stripe"


def test_parse_search_result_em_dash() -> None:
    title, company = parse_search_result_title("AI Researcher — Cohere")
    assert title == "AI Researcher"
    assert company == "Cohere"


def test_parse_search_result_no_separator_returns_none() -> None:
    title, company = parse_search_result_title("Senior AI Engineer (Remote)")
    assert title is None
    assert company is None


def test_parse_search_result_empty_string() -> None:
    assert parse_search_result_title("") == (None, None)


def test_extract_url_text_local_prefix_reads_file(tmp_path: Path, monkeypatch) -> None:
    """`local:<path>` must short-circuit to local file read, no HTTP."""
    monkeypatch.chdir(tmp_path)
    jd = tmp_path / "jd.md"
    jd.write_text(
        "# Senior AI Engineer at Acme\n\nWe're looking for someone to ship LLM systems.\n",
        encoding="utf-8",
    )

    result = asyncio.run(extract_url_text(f"local:{jd}"))
    assert result.adapter == "local_file"
    assert result.title == "Senior AI Engineer at Acme"
    assert "LLM systems" in result.text


def test_extract_url_text_local_prefix_resolves_jds_dir(tmp_path: Path, monkeypatch) -> None:
    """`local:foo.md` should resolve under jds/ when not found at the literal path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "jds").mkdir()
    (tmp_path / "jds" / "anthropic.md").write_text(
        "AI Engineer at Anthropic\nBuild safe AI.\n", encoding="utf-8"
    )

    result = asyncio.run(extract_url_text("local:anthropic.md"))
    assert result.adapter == "local_file"
    assert "Anthropic" in result.title
    assert "Build safe AI" in result.text


def test_extract_url_text_local_prefix_missing_file_returns_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = asyncio.run(extract_url_text("local:missing.md"))
    assert result.adapter == "local_file"
    assert result.text == ""


# ----- _guess_title_company_location (markdown JD metadata) -----


def test_guess_meta_from_markdown_jd_with_labeled_block() -> None:
    from job_hunt.nodes.extract import _guess_title_company_location

    jd = (
        "# Senior Backend Developer — Industrial Matrix\n\n"
        "**Company:** Industrial Matrix\n"
        "**Location:** Greater Toronto Area (North York, ON), Canada\n"
        "**Employment Type:** Full-time\n\n"
        "## About the Role\n"
    )
    title, company, location = _guess_title_company_location(jd)
    assert title == "Senior Backend Developer"
    assert company == "Industrial Matrix"
    assert location == "Greater Toronto Area (North York, ON), Canada"


def test_guess_meta_h1_dash_fallback_without_labels() -> None:
    from job_hunt.nodes.extract import _guess_title_company_location

    title, company, location = _guess_title_company_location(
        "# AI Engineer - Cohere\n\nBuild things.\n"
    )
    assert title == "AI Engineer"
    assert company == "Cohere"
    assert location == ""


def test_guess_meta_at_pattern_still_works() -> None:
    from job_hunt.nodes.extract import _guess_title_company_location

    title, company, _ = _guess_title_company_location(
        "Software Engineer at Stripe\nLocation: Remote\nDo stuff.\n"
    )
    assert title == "Software Engineer"
    assert company == "Stripe"
