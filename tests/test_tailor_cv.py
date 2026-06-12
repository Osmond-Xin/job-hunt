"""Tests for the tailor_cv node and the cv markdown→HTML helper."""

from __future__ import annotations

import asyncio

from job_hunt.nodes import pdf as pdf_module
from job_hunt.nodes.pdf import _TENURE_SELF_LABEL_RE, cv_markdown_to_html, tailor_cv
from job_hunt.services.llm.base import ChatResult


# ----- tenure self-label detector -----


def test_tenure_regex_flags_self_labels() -> None:
    assert _TENURE_SELF_LABEL_RE.search("20+ years of experience bridging software")
    assert _TENURE_SELF_LABEL_RE.search("with 15 years' experience in backend systems")
    assert _TENURE_SELF_LABEL_RE.search("Two decades of shipping production systems")


def test_tenure_regex_allows_role_scoped_facts() -> None:
    # Dated, role-scoped statements are fine; only advertised totals are flagged.
    assert not _TENURE_SELF_LABEL_RE.search("across the 7-year tenure — defined product requirements")
    assert not _TENURE_SELF_LABEL_RE.search("Dec 2014 – May 2021 | Beijing, China")
    assert not _TENURE_SELF_LABEL_RE.search("experienced backend engineer with a deep track record")


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


# ----- tailor_cv node -----


def _fake_llm(content: str, errors: list[str] | None = None):
    async def fake(state, **kwargs):
        return (
            ChatResult(
                content=content,
                model="fake",
                provider="local",
                tier="cheap",
                invocation="http",
            ),
            errors or [],
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
        pdf_module,
        "call_node_llm_or_fallback",
        _fake_llm("```markdown\n## Professional Summary\n\nBuilder.\n```"),
    )
    result = asyncio.run(tailor_cv(dict(_BASE_STATE), None))
    assert result["cv_tailored"].startswith("## Professional Summary")
    assert "```" not in result["cv_tailored"]
    assert result["errors"] == []


def test_tailor_cv_warns_on_tenure_self_label(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_module,
        "call_node_llm_or_fallback",
        _fake_llm("## Professional Summary\n\nEngineer with 20+ years of experience.\n"),
    )
    result = asyncio.run(tailor_cv(dict(_BASE_STATE), None))
    assert "cv_tailored" in result
    assert any("tenure self-label" in err for err in result["errors"])


def test_tailor_cv_empty_llm_output_falls_back(monkeypatch) -> None:
    # Empty content (LLM fallback path) → no cv_tailored key, so the PDF node
    # renders the master cv.md instead.
    monkeypatch.setattr(pdf_module, "call_node_llm_or_fallback", _fake_llm(""))
    result = asyncio.run(tailor_cv(dict(_BASE_STATE), None))
    assert "cv_tailored" not in result


def test_tailor_cv_noop_without_cv() -> None:
    result = asyncio.run(tailor_cv({"cv": ""}, None))
    assert result == {"errors": []}
