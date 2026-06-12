"""generate_cover_letter node — produces an independent one-page cover letter PDF."""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.nodes._llm import call_node_llm_or_fallback
from job_hunt.nodes._prompts import render
from job_hunt.nodes.pdf import detect_paper_size

_OUTPUT_DIR = Path("output")
_TEMPLATES_DIR = Path("templates")
_FONTS_DIR = (_TEMPLATES_DIR / "fonts").resolve()
_TEMPLATE_NAME = "cover-letter.html.j2"


async def generate_cover_letter(state: JobHuntState, config: RunnableConfig) -> dict:
    """Render an independent cover letter PDF when ``generate_cover_letter`` is set.

    Score gate: even with the flag on, skip when the scorer decided this JD
    doesn't warrant a CV PDF (``scores.generate_pdf == False``). The flag
    signals "operator wants a cover letter for jobs we're actually applying
    to" — if we're not applying, the cover letter is pure token waste. This
    keeps the cover letter consistent with the CV-PDF route and the
    ``draft_application_answers`` no-op below 4.5.
    """
    if not state.get("generate_cover_letter"):
        return {"errors": []}
    scores = state.get("scores")
    if scores is not None and not getattr(scores, "generate_pdf", True):
        return {"errors": [], "cover_letter_path": None}

    jd_meta = state.get("jd_meta")
    profile = state.get("profile")
    company = jd_meta.company if jd_meta else "Unknown"
    role = jd_meta.title if jd_meta else ""

    exit_narrative = ""
    if profile is not None:
        exit_narrative = getattr(profile, "exit_narrative", "") or ""

    prompt = render(
        "evaluate/cover_letter.md",
        cv=state.get("cv", ""),
        jd_meta=jd_meta,
        jd_text=state.get("jd_text", ""),
        archetype=state.get("archetype"),
        evaluation_blocks=state.get("evaluation_blocks", {}),
        exit_narrative=exit_narrative,
        mode=state.get("mode", "full"),
    )

    result, errors = await call_node_llm_or_fallback(
        state,
        node_name="generate_cover_letter",
        prompt=prompt,
        prompt_version="evaluate/cover_letter.md:v2",
        fallback_content="",
        temperature=0.3,
        max_tokens=900,
    )

    body_md = result.content.strip()
    if not body_md:
        return {"errors": errors}

    paragraphs = _split_paragraphs(body_md)

    try:
        from jinja2 import Environment, FileSystemLoader

        if not (_TEMPLATES_DIR / _TEMPLATE_NAME).exists():
            return {
                "errors": errors + [f"{_TEMPLATE_NAME} template not found; skipping cover letter."],
            }

        run_id = state.get("run_id", "unknown")
        out_dir = _OUTPUT_DIR / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
        template = env.get_template(_TEMPLATE_NAME)
        paper_size = detect_paper_size(jd_meta)
        html = template.render(
            profile=profile,
            company=company,
            role=role,
            date=datetime.date.today().strftime("%B %d, %Y"),
            paragraphs=paragraphs,
            greeting="Dear Hiring Team,",
            closing="Sincerely,",
            fonts_dir=str(_FONTS_DIR),
            paper_size=paper_size,
        )

        html_path = out_dir / "cover-letter.html"
        html_path.write_text(html, encoding="utf-8")

        pdf_path = out_dir / "cover-letter.pdf"
        await _html_to_pdf(str(html_path), str(pdf_path), paper_size=paper_size)

        return {
            "cover_letter_path": str(pdf_path),
            "evaluation_blocks": {"cover_letter": body_md},
            "errors": errors,
        }

    except Exception as exc:
        return {
            "cover_letter_path": None,
            "errors": errors + [f"cover letter generation failed: {exc}"],
        }


def _split_paragraphs(markdown: str) -> list[str]:
    """Split LLM markdown into clean paragraph strings, dropping empty lines and stray markers."""
    cleaned = re.sub(r"^#+\s+.*$", "", markdown, flags=re.MULTILINE)
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", cleaned) if chunk.strip()]
    return [re.sub(r"\s+", " ", chunk) for chunk in chunks]


async def _html_to_pdf(html_path: str, pdf_path: str, *, paper_size: str = "letter") -> None:
    from playwright.async_api import async_playwright

    fmt = "Letter" if paper_size.lower() == "letter" else "A4"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(Path(html_path).resolve().as_uri())
        await page.pdf(path=pdf_path, format=fmt, print_background=True)
        await browser.close()
