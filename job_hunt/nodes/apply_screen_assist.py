"""apply_screen_assist — generate per-question answers for a live application form.

This is a one-shot node, not part of the evaluate graph. It takes a pasted form-question
text plus an existing report (matched via company + role) and produces ready-to-paste
markdown answers grounded in Section G / the wider report / the CV.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from job_hunt.services.llm.call import call_node_llm_or_fallback
from job_hunt.services.prompts import render
from job_hunt.services.llm.base import ChatResult


@dataclass
class ApplyAnswersResult:
    content: str
    errors: list[str]
    raw: ChatResult | None = None


async def generate_apply_answers(
    *,
    company: str,
    role: str,
    url: str,
    form_text: str,
    report_section_g: str,
    report_full: str,
    cv_md: str,
) -> ApplyAnswersResult:
    """Render the screen_to_answers prompt on the premium tier (outward-facing
    apply answers), falling back to the cheap tier when premium is unavailable."""
    prompt = render(
        "apply_assist/screen_to_answers.md",
        company=company,
        role=role,
        url=url or "",
        form_text=form_text,
        report_section_g=report_section_g or "(none — fall back to wider report)",
        report_full=report_full or "(no report available)",
        cv_md=cv_md or "",
    )
    fallback_content = (
        "Unable to generate apply answers because the LLM provider failed. "
        "Open the matched report in `reports/` and adapt Section G manually."
    )

    result, errors = await call_node_llm_or_fallback(
        state={},
        node_name="apply_screen_assist",
        prompt=prompt,
        prompt_version="apply_assist/screen_to_answers.md:v1",
        fallback_content=fallback_content,
        temperature=0.3,
        max_tokens=1800,
        tier="premium",
    )
    if errors:
        cheap_result, cheap_errors = await call_node_llm_or_fallback(
            state={},
            node_name="apply_screen_assist",
            prompt=prompt,
            prompt_version="apply_assist/screen_to_answers.md:v1",
            fallback_content=fallback_content,
            temperature=0.3,
            max_tokens=1800,
            tier="cheap",
        )
        if not cheap_errors:
            result = cheap_result
        errors += cheap_errors

    return ApplyAnswersResult(content=result.content.strip(), errors=errors, raw=result)


def load_form_text(form_text: str | None, form_text_file: Path | None) -> str:
    """Read the form-question text either from a literal string or a file."""
    if form_text:
        return form_text.strip()
    if form_text_file:
        return form_text_file.read_text(encoding="utf-8").strip()
    return ""
