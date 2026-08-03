"""tailor_cv, generate_cv_html_pdf, and skip_pdf nodes."""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.nodes._prompts import render
from job_hunt.nodes._quality import generate_with_audit
from job_hunt.nodes.artifact_paths import run_output_dir

_TEMPLATES_DIR = Path("templates")
_FONTS_DIR = (_TEMPLATES_DIR / "fonts").resolve()


async def tailor_cv(state: JobHuntState, config: RunnableConfig) -> dict:
    """Rewrite the master CV into a JD-tailored resume body (pruned projects,
    no tenure self-labels), quality-audited with regeneration. Falls back to
    the master CV downstream when the LLM is unavailable or returns nothing."""
    cv = state.get("cv", "")
    if not cv:
        return {"errors": []}

    prompt = render(
        "evaluate/tailor_cv.md",
        cv=cv,
        article_digest=state.get("article_digest") or "",
        jd_meta=state.get("jd_meta"),
        jd_text=state.get("jd_text", ""),
        archetype=state.get("archetype"),
        evaluation_blocks=state.get("evaluation_blocks", {}),
        mode=state.get("mode", "full"),
    )
    audited = await generate_with_audit(
        state,
        node_name="tailor_cv",
        prompt=prompt,
        prompt_version="evaluate/tailor_cv.md:v2",
        artifact_type="tailored CV",
        temperature=0.2,
        max_tokens=2800,
        tier="premium",
    )
    if audited.status == "failed":
        # A tailored CV the auditor rejected three times is worse than no
        # tailoring: downstream renders the hand-written master CV instead,
        # which is known-good. The rejected draft is not silently shipped.
        return {
            "errors": audited.errors,
            "artifact_warnings": [f"tailored CV withheld (audit failed): {'; '.join(audited.issues)}"],
        }
    if not audited.content:
        return {"errors": audited.errors}
    warnings = (
        [f"tailored CV is UNVERIFIED (auditor unavailable)"]
        if audited.status == "unavailable"
        else []
    )
    return {
        "cv_tailored": audited.content,
        "errors": audited.errors,
        "artifact_warnings": warnings,
    }


# "## Professional Summary" section up to (not including) the next H2.
_SUMMARY_SECTION_RE = re.compile(
    r"^##\s+Professional Summary\s*\n.*?(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)


def strip_summary_section(cv_md: str) -> str:
    """Remove the master CV's Professional Summary section.

    Used on the fallback (untailored) render path when the template banner
    already carries a JD-specific summary_angle — two stacked summaries read
    as duplicated filler to a recruiter."""
    return _SUMMARY_SECTION_RE.sub("", cv_md)


def cv_markdown_to_html(cv_md: str, *, strip_contact_block: bool = True) -> str:
    """Convert CV markdown into rendered HTML for the resume template.

    With ``strip_contact_block`` (the master cv.md case), drops everything up to the
    first standalone ``---`` rule — the name + contact block — because the template's
    header already renders ``profile.name`` and contacts. Tailored CVs start at the
    summary and must be rendered whole.
    """
    if not cv_md:
        return ""
    from markdown_it import MarkdownIt

    body_md = cv_md
    if strip_contact_block:
        parts = re.split(r"^\s*-{3,}\s*$", cv_md, maxsplit=1, flags=re.MULTILINE)
        if len(parts) > 1:
            body_md = parts[1].lstrip()
    # The commonmark preset ships with the linkify core rule off, so the option
    # alone is a no-op — enable() is what makes bare URLs (project repos, the
    # portfolio site) clickable in the PDF instead of dead text.
    md = MarkdownIt(
        "commonmark", {"html": False, "linkify": True, "typographer": True}
    ).enable("linkify")
    return md.render(body_md)


# Flagship repos a reader should be able to reach from any mention of the name,
# not just from the one URL in the Projects block.
_PROJECT_LINKS = {
    "LearnArken": "https://github.com/Osmond-Xin/LearnArken",
}
_PROJECT_NAME_RE = re.compile(r"\b(" + "|".join(map(re.escape, _PROJECT_LINKS)) + r")\b")
# Existing anchors (so a name inside one is not nested) and any other tag (so
# href/attribute text is never rewritten). Everything between is a text node.
_HTML_SKIP_RE = re.compile(r"<a\b[^>]*>.*?</a>|<[^>]+>", re.DOTALL | re.IGNORECASE)


def link_project_mentions(html: str) -> str:
    """Turn every bare project-name mention in rendered HTML into a repo link.

    Operates on text nodes only: names already inside an ``<a>`` and anything
    within a tag (attribute values, URLs) are left untouched. Matching is
    case-sensitive, so identifiers like ``LEARNARKEN_LOCAL_ONLY`` do not match.
    """
    if not html:
        return html

    def wrap(match: re.Match[str]) -> str:
        name = match.group(1)
        return f'<a href="{_PROJECT_LINKS[name]}">{name}</a>'

    out: list[str] = []
    cursor = 0
    for skip in _HTML_SKIP_RE.finditer(html):
        out.append(_PROJECT_NAME_RE.sub(wrap, html[cursor : skip.start()]))
        out.append(skip.group(0))
        cursor = skip.end()
    out.append(_PROJECT_NAME_RE.sub(wrap, html[cursor:]))
    return "".join(out)


# Country signals that map to North-American letter paper. Everything else → A4.
# Matches whole tokens (case-insensitive) so "United Kingdom" doesn't trigger on "US".
_LETTER_COUNTRY_RE = re.compile(
    r"\b(US|U\.S\.|USA|United States|Canada|CA(?:N)?|United States of America)\b",
    re.IGNORECASE,
)


def detect_paper_size(jd_meta) -> str:
    """letter for US/Canada, A4 elsewhere. jd_meta may be None."""
    if jd_meta is None:
        return "letter"
    location = getattr(jd_meta, "location", "") or ""
    if not isinstance(location, str):
        return "letter"
    return "letter" if _LETTER_COUNTRY_RE.search(location) else "A4"


def artifact_template_env():
    """Jinja environment for the CV and cover-letter templates, with the filters they require.

    autoescape is on so LLM-supplied strings (summary_angle, bullets, keywords,
    letter paragraphs) cannot break the HTML; cv_html is the only trusted-markup
    slot (``| safe``). ``project_links`` escapes first and then injects anchors,
    so it must return Markup to survive autoescaping.
    """
    from jinja2 import Environment, FileSystemLoader
    from markupsafe import Markup, escape

    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
    env.filters["project_links"] = lambda value: Markup(
        link_project_mentions(str(escape(value)))
    )
    return env


async def generate_cv_html_pdf(state: JobHuntState, config: RunnableConfig) -> dict:
    """Render the shared Jinja2 CV template with per-company state content, then PDF via Playwright."""
    try:
        scores = state.get("scores")
        profile = state.get("profile")
        cv = state.get("cv", "")
        jd_meta = state.get("jd_meta")
        pdf_content = scores.pdf_content if scores else None

        out_dir = run_output_dir(state)
        out_dir.mkdir(parents=True, exist_ok=True)

        template_path = _TEMPLATES_DIR / "cv.html.j2"
        if not template_path.exists():
            return {
                "pdf_path": None,
                "errors": ["cv.html.j2 template not found; skipping PDF generation."],
            }

        template = artifact_template_env().get_template("cv.html.j2")

        paper_size = detect_paper_size(jd_meta)
        # Suppress the embedded "Cover Letter Draft" block in the CV PDF when an
        # independent cover-letter PDF is being generated downstream — avoids the
        # same content appearing twice across the two artifacts.
        standalone_cover_letter = bool(state.get("generate_cover_letter"))
        embedded_cover_letter = (
            "" if standalone_cover_letter else (pdf_content.cover_letter_body if pdf_content else "")
        )
        cv_tailored = state.get("cv_tailored", "")
        summary_angle = pdf_content.summary_angle if pdf_content else ""
        # Prefer the JD-tailored rewrite; the master cv.md is the fallback and
        # still needs its name/contact block stripped (the header renders it)
        # and its generic summary dropped when the banner carries a JD-specific
        # angle — otherwise the PDF opens with two stacked summaries.
        cv_for_render = cv_tailored or cv
        if not cv_tailored and summary_angle:
            cv_for_render = strip_summary_section(cv_for_render)
        html = template.render(
            profile=profile,
            cv_raw=cv,  # kept for backwards compat; template now prefers cv_html
            cv_html=link_project_mentions(
                cv_markdown_to_html(cv_for_render, strip_contact_block=not cv_tailored)
            ),
            company=jd_meta.company if jd_meta else "",
            role=jd_meta.title if jd_meta else "",
            summary_angle=pdf_content.summary_angle if pdf_content else "",
            top_bullets=pdf_content.top_bullets if pdf_content else [],
            keywords=pdf_content.keywords if pdf_content else [],
            cover_letter_body=embedded_cover_letter,
            fonts_dir=str(_FONTS_DIR),
            paper_size=paper_size,
        )

        html_path = out_dir / "cv.html"
        html_path.write_text(html, encoding="utf-8")

        pdf_path = out_dir / "cv.pdf"
        await _html_to_pdf(str(html_path), str(pdf_path), paper_size=paper_size)

        return {"pdf_path": str(pdf_path), "errors": []}

    except Exception as exc:
        return {"pdf_path": None, "errors": [f"PDF generation failed: {exc}"]}


async def skip_pdf(state: JobHuntState, config: RunnableConfig) -> dict:
    return {"pdf_path": None, "errors": []}


async def _html_to_pdf(html_path: str, pdf_path: str, *, paper_size: str = "letter") -> None:
    from playwright.async_api import async_playwright

    # Playwright accepts "Letter" or "A4" (case-insensitive on the format param).
    fmt = "Letter" if paper_size.lower() == "letter" else "A4"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(Path(html_path).resolve().as_uri())
        await page.pdf(path=pdf_path, format=fmt, print_background=True)
        await browser.close()
