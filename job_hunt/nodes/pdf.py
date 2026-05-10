"""generate_cv_html_pdf and skip_pdf nodes."""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState

_OUTPUT_DIR = Path("output")
_TEMPLATES_DIR = Path("templates")
_FONTS_DIR = (_TEMPLATES_DIR / "fonts").resolve()


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

        env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=False)
        template = env.get_template("cv.html.j2")

        paper_size = detect_paper_size(jd_meta)
        html = template.render(
            profile=profile,
            cv_raw=cv,
            company=jd_meta.company if jd_meta else "",
            role=jd_meta.title if jd_meta else "",
            summary_angle=pdf_content.summary_angle if pdf_content else "",
            top_bullets=pdf_content.top_bullets if pdf_content else [],
            keywords=pdf_content.keywords if pdf_content else [],
            cover_letter_body=pdf_content.cover_letter_body if pdf_content else "",
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
