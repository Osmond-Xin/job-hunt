"""generate_cover_letter node — produces an independent one-page cover letter PDF."""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.services.prompts import render
from job_hunt.nodes._quality import generate_with_audit
from job_hunt.nodes.artifact_paths import artifact_filename, run_output_dir
from job_hunt.nodes.pdf import (
    MAX_COVER_LETTER_PAGES,
    _html_to_pdf,
    artifact_template_env,
    detect_paper_size,
)
from job_hunt.services.pdf import pdf_page_count

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
    availability = ""
    if profile is not None:
        exit_narrative = getattr(profile, "exit_narrative", "") or ""
        availability = getattr(profile, "availability", "") or ""

    prompt = render(
        "evaluate/cover_letter.md",
        cv=state.get("cv", ""),
        article_digest=state.get("article_digest") or "",
        jd_meta=jd_meta,
        jd_text=state.get("jd_text", ""),
        archetype=state.get("archetype"),
        evaluation_blocks=state.get("evaluation_blocks", {}),
        exit_narrative=exit_narrative,
        availability=availability,
        mode=state.get("mode", "full"),
    )

    audited = await generate_with_audit(
        state,
        node_name="generate_cover_letter",
        prompt=prompt,
        prompt_version="evaluate/cover_letter.md:v3",
        artifact_type="cover letter",
        temperature=0.3,
        max_tokens=900,
        tier="premium",
    )
    errors = audited.errors
    if audited.status == "failed":
        # No PDF at all beats a PDF the auditor rejected. Unlike the CV there
        # is no known-good fallback to fall back to, and the cover letter is
        # optional — so withhold it and say so.
        return {
            "errors": errors,
            "cover_letter_path": None,
            "artifact_warnings": [
                f"cover letter withheld (audit failed): {'; '.join(audited.issues)}"
            ],
        }
    body_md = audited.content
    if not body_md:
        return {"errors": errors}
    unverified = (
        ["cover letter is UNVERIFIED (auditor unavailable)"]
        if audited.status == "unavailable"
        else []
    )

    paragraphs = _split_paragraphs(body_md)

    try:
        if not (_TEMPLATES_DIR / _TEMPLATE_NAME).exists():
            return {
                "errors": errors + [f"{_TEMPLATE_NAME} template not found; skipping cover letter."],
            }

        out_dir = run_output_dir(state)
        out_dir.mkdir(parents=True, exist_ok=True)

        template = artifact_template_env().get_template(_TEMPLATE_NAME)
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

        stem = artifact_filename(state, kind="Cover_Letter", suffix="")
        html_path = out_dir / f"{stem}.html"
        html_path.write_text(html, encoding="utf-8")

        pdf_path = out_dir / f"{stem}.pdf"
        await _html_to_pdf(str(html_path), str(pdf_path), paper_size=paper_size)

        # CLAUDE.md §2: "Cover letter: 1 page. Enforced, not aspirational." The
        # résumé has a trim order (`_cv_fit.next_trim`) that decides which block
        # to drop; a cover letter's paragraphs have no such order, so dropping
        # one silently would change the argument. Measure and report instead —
        # same warnings channel the audit-failed and UNVERIFIED cases above use.
        overflow_warnings: list[str] = []
        pages = pdf_page_count(pdf_path.read_bytes())
        if pages is not None and pages > MAX_COVER_LETTER_PAGES:
            overflow_warnings.append(
                f"cover letter is {pages} pages — budget is {MAX_COVER_LETTER_PAGES}. "
                "Shorten it by hand; the letter is not auto-trimmed."
            )

        return {
            "cover_letter_path": str(pdf_path),
            "evaluation_blocks": {"cover_letter": body_md},
            "errors": errors,
            "artifact_warnings": unverified + overflow_warnings,
        }

    except Exception as exc:
        return {
            "cover_letter_path": None,
            "errors": errors + [f"cover letter generation failed: {exc}"],
        }


def _split_paragraphs(markdown: str) -> list[str]:
    """Split LLM markdown into clean paragraph strings, dropping empty lines and stray markers.

    The letter template renders paragraphs as plain text, so inline markdown
    (`code`, **bold**, *em*) would show its literal markers in the PDF — strip it.
    """
    cleaned = re.sub(r"^#+\s+.*$", "", markdown, flags=re.MULTILINE)
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"\*{1,2}([^*\n]+)\*{1,2}", r"\1", cleaned)
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", cleaned) if chunk.strip()]
    return [re.sub(r"\s+", " ", chunk) for chunk in chunks]
