"""Tests for the tailor_cv node and the cv markdown→HTML helper."""

from __future__ import annotations

import asyncio

from job_hunt.nodes import _quality as quality_module
from job_hunt.nodes._quality import TENURE_SELF_LABEL_RE
from job_hunt.nodes.pdf import cv_markdown_to_html, link_project_mentions, tailor_cv
from job_hunt.services.llm.base import ChatResult


# ----- tenure self-label detector -----


def test_tenure_regex_flags_self_labels() -> None:
    assert TENURE_SELF_LABEL_RE.search("20+ years of experience bridging software")
    assert TENURE_SELF_LABEL_RE.search("with 15 years' experience in backend systems")
    assert TENURE_SELF_LABEL_RE.search("Two decades of shipping production systems")


def test_tailor_cv_prompt_forbids_relocating_a_bullet() -> None:
    """A bullet's achievement is pinned to its source employer/project/period.

    2026-08-19-class bug: the model moved a true AWS case-study bullet from
    Iqidao onto Freelance. Every fact in the sentence was true; the
    attribution was not. The prompt must say this is not allowed even when
    each individual fact checks out.
    """
    from job_hunt.services.prompts import render

    out = render(
        "evaluate/tailor_cv.md",
        cv="CV",
        jd_text="JD",
        article_digest="",
        jd_meta=None,
        archetype=None,
        evaluation_blocks={"cv_match": "M", "personalization": "P"},
        mode="full",
    )
    assert "Never relocate a bullet" in out
    assert "even when every fact inside it is true" in out


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


def test_cv_html_autolinks_bare_urls() -> None:
    # Project repos are written as bare URLs in cv.md; a recruiter must be able
    # to click straight through from the PDF.
    cv_md = "## Projects\n\nGitHub: https://github.com/Osmond-Xin/LearnArken | 2026\n"
    html = cv_markdown_to_html(cv_md, strip_contact_block=False)
    assert '<a href="https://github.com/Osmond-Xin/LearnArken">' in html


# ----- link_project_mentions -----

_LEARNARKEN_HREF = 'href="https://github.com/Osmond-Xin/LearnArken"'


def test_project_mention_becomes_a_link() -> None:
    html = "<p>Built LearnArken, a fail-closed retrieval system.</p>"
    assert link_project_mentions(html) == (
        f'<p>Built <a {_LEARNARKEN_HREF}>LearnArken</a>, a fail-closed retrieval system.</p>'
    )


def test_project_mention_links_every_occurrence() -> None:
    html = "<p>LearnArken ships.</p><li>LearnArken refuses.</li>"
    assert link_project_mentions(html).count(_LEARNARKEN_HREF) == 2


def test_project_mention_not_nested_inside_existing_anchor() -> None:
    html = '<p><a href="https://example.com">LearnArken docs</a></p>'
    assert link_project_mentions(html) == html


def test_project_mention_leaves_urls_and_attributes_alone() -> None:
    # The autolinked repo URL must not have an anchor injected into its href.
    html = f'<p><a {_LEARNARKEN_HREF}>https://github.com/Osmond-Xin/LearnArken</a></p>'
    assert link_project_mentions(html) == html


def test_project_mention_is_case_sensitive() -> None:
    # LEARNARKEN_LOCAL_ONLY is an env-var identifier, not a project reference.
    html = "<li><code>LEARNARKEN_LOCAL_ONLY=1</code> is a hard egress fence.</li>"
    assert link_project_mentions(html) == html


def test_project_mention_empty_input() -> None:
    assert link_project_mentions("") == ""


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


def test_tailor_cv_withholds_a_draft_that_never_passed_audit(monkeypatch) -> None:
    """A CV the auditor rejected every time must not reach the PDF.

    The deterministic tenure gate rejects all three attempts. Rather than
    shipping the last bad draft, the node returns no `cv_tailored` at all so
    downstream renders the hand-written master CV, and flags the artifact.
    """
    monkeypatch.setattr(
        quality_module,
        "call_node_llm_or_fallback",
        _fake_llm("## Experience\n\nEngineer with 20+ years of experience.\n"),
    )
    result = asyncio.run(tailor_cv(dict(_BASE_STATE), None))
    assert "cv_tailored" not in result
    assert any("quality audit FAILED" in err for err in result["errors"])
    assert any("Tenure self-label" in err for err in result["errors"])
    assert any("withheld" in warning for warning in result["artifact_warnings"])


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
