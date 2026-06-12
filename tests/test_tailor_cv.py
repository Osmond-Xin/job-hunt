"""Tests for the tailor_cv node and the cv markdown→HTML helper."""

from __future__ import annotations

import asyncio

from job_hunt.nodes import _quality as quality_module
from job_hunt.nodes._quality import TENURE_SELF_LABEL_RE
from job_hunt.nodes.pdf import cv_markdown_to_html, tailor_cv
from job_hunt.services.llm.base import ChatResult


# ----- tenure self-label detector -----


def test_tenure_regex_flags_self_labels() -> None:
    assert TENURE_SELF_LABEL_RE.search("20+ years of experience bridging software")
    assert TENURE_SELF_LABEL_RE.search("with 15 years' experience in backend systems")
    assert TENURE_SELF_LABEL_RE.search("Two decades of shipping production systems")


def test_tenure_regex_allows_role_scoped_facts() -> None:
    # Dated, role-scoped statements are fine; only advertised totals are flagged.
    assert not TENURE_SELF_LABEL_RE.search("across the 7-year tenure — defined product requirements")
    assert not TENURE_SELF_LABEL_RE.search("Dec 2014 – May 2021 | Beijing, China")
    assert not TENURE_SELF_LABEL_RE.search("experienced backend engineer with a deep track record")


# ----- cv_markdown_to_html -----


def test_cv_html_strips_contact_block_before_first_rule() -> None:
    cv_md = "# Name\n\n**Phone:** 123\n\n---\n\n## Professional Summary\n\nBuilder.\n"
    html = cv_markdown_to_html(cv_md)
    assert "Professional Summary" in html
    assert "Phone" not in html
    assert "<h1>" not in html


def test_cv_html_renders_whole_body_for_tailored_cv() -> None:
    tailored = "## Professional Summary\n\nBuilder.\n\n---\n\n## Experience\n\n- Shipped X\n"
    html = cv_markdown_to_html(tailored, strip_contact_block=False)
    assert "Professional Summary" in html
    assert "Experience" in html


def test_cv_html_ignores_inline_dashes_when_stripping() -> None:
    # An inline "---" must not be mistaken for the contact-block delimiter.
    cv_md = "# Name\n\npre---post header text\n\n---\n\n## Summary\n\nBody.\n"
    html = cv_markdown_to_html(cv_md)
    assert "Summary" in html
    assert "pre" not in html


def test_cv_html_empty_input() -> None:
    assert cv_markdown_to_html("") == ""


# ----- tailor_cv node (through the generate→audit loop) -----


def _fake_llm(gen_content: str, audit_content: str = '{"verdict": "pass", "issues": []}'):
    async def fake(state, **kwargs):
        content = audit_content if kwargs["node_name"].endswith("_audit") else gen_content
        return (
            ChatResult(
                content=content,
                model="fake",
                provider="local",
                tier="cheap",
                invocation="http",
            ),
            [],
        )

    return fake


_BASE_STATE = {
    "cv": "# Name\n\n---\n\n## Experience\n\n- Shipped X\n",
    "jd_text": "We need a backend engineer.",
    "jd_meta": None,
    "archetype": None,
    "mode": "full",
    "evaluation_blocks": {"cv_match": "match", "personalization": "plan"},
}


def test_tailor_cv_returns_body_and_strips_code_fence(monkeypatch) -> None:
    monkeypatch.setattr(
        quality_module,
        "call_node_llm_or_fallback",
        _fake_llm("```markdown\n## Experience\n\n- Shipped X\n```"),
    )
    result = asyncio.run(tailor_cv(dict(_BASE_STATE), None))
    assert result["cv_tailored"].startswith("## Experience")
    assert "```" not in result["cv_tailored"]
    assert result["errors"] == []


def test_tailor_cv_tenure_self_label_fails_audit_with_warning(monkeypatch) -> None:
    # The deterministic gate rejects every attempt; the node keeps the last
    # draft but surfaces the unresolved audit failure.
    monkeypatch.setattr(
        quality_module,
        "call_node_llm_or_fallback",
        _fake_llm("## Experience\n\nEngineer with 20+ years of experience.\n"),
    )
    result = asyncio.run(tailor_cv(dict(_BASE_STATE), None))
    assert "cv_tailored" in result
    assert any("quality audit still failing" in err for err in result["errors"])
    assert any("Tenure self-label" in err for err in result["errors"])


def test_tailor_cv_empty_llm_output_falls_back(monkeypatch) -> None:
    # Empty content (LLM fallback path) → no cv_tailored key, so the PDF node
    # renders the master cv.md instead.
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", _fake_llm(""))
    result = asyncio.run(tailor_cv(dict(_BASE_STATE), None))
    assert "cv_tailored" not in result


def test_tailor_cv_noop_without_cv() -> None:
    result = asyncio.run(tailor_cv({"cv": ""}, None))
    assert result == {"errors": []}


# ----- strip_summary_section (fallback render path) -----


def test_strip_summary_section_removes_only_summary() -> None:
    from job_hunt.nodes.pdf import strip_summary_section

    md = (
        "## Professional Summary\n\nGeneric summary text.\n\n---\n\n"
        "## Experience\n\n- Shipped X\n\n## Skills\n\n- Python\n"
    )
    out = strip_summary_section(md)
    assert "Generic summary text" not in out
    assert "## Experience" in out
    assert "## Skills" in out


def test_strip_summary_section_noop_when_absent() -> None:
    from job_hunt.nodes.pdf import strip_summary_section

    md = "## Experience\n\n- Shipped X\n"
    assert strip_summary_section(md) == md
