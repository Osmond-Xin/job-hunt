"""tailor_cv, generate_cv_html_pdf, and skip_pdf nodes."""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.nodes._prompts import render
from job_hunt.nodes._quality import generate_with_audit

_OUTPUT_DIR = Path("output")
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
        jd_meta=state.get("jd_meta"),
        jd_text=state.get("jd_text", ""),
        archetype=state.get("archetype"),
        evaluation_blocks=state.get("evaluation_blocks", {}),
        mode=state.get("mode", "full"),
    )
    body, errors = await generate_with_audit(
        state,
        node_name="tailor_cv",
        prompt=prompt,
        prompt_version="evaluate/tailor_cv.md:v2",
        artifact_type="tailored CV",
        temperature=0.2,
        max_tokens=2800,
    )
    if not body:
        return {"errors": errors}
    return {"cv_tailored": body, "errors": errors}


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
    md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
    return md.render(body_md)


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


async def generate_cv_html_pdf(state: JobHuntState, config: RunnableConfig) -> dict:
    """Render the shared Jinja2 CV template with per-company state content, then PDF via Playwright."""
    try:
        from jinja2 import Environment, FileSystemLoader

        run_id = state.get("run_id", "unknown")
        scores = state.get("scores")
        profile = state.get("profile")
        cv = state.get("cv", "")
        jd_meta = state.get("jd_meta")
        pdf_content = scores.pdf_content if scores else None

        out_dir = _OUTPUT_DIR / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        template_path = _TEMPLATES_DIR / "cv.html.j2"
        if not template_path.exists():
            return {
                "pdf_path": None,
                "errors": ["cv.html.j2 template not found; skipping PDF generation."],
            }

        # autoescape so LLM-supplied strings (summary_angle, bullets, keywords)
        # cannot break the HTML; cv_html is the only trusted-markup slot (| safe).
        env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
        template = env.get_template("cv.html.j2")

        paper_size = detect_paper_size(jd_meta)
        # Suppress the embedded "Cover Letter Draft" block in the CV PDF when an
        # independent cover-letter PDF is being generated downstream — avoids the
        # same content appearing twice across the two artifacts.
        standalone_cover_letter = bool(state.get("generate_cover_letter"))
        embedded_cover_letter = (
            "" if standalone_cover_letter else (pdf_content.cover_letter_body if pdf_content else "")
        )
        cv_tailored = state.get("cv_tailored", "")
        html = template.render(
            profile=profile,
            cv_raw=cv,  # kept for backwards compat; template now prefers cv_html
            # Prefer the JD-tailored rewrite; the master cv.md is the fallback and
            # still needs its name/contact block stripped (the header renders it).
            cv_html=cv_markdown_to_html(
                cv_tailored or cv, strip_contact_block=not cv_tailored
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
