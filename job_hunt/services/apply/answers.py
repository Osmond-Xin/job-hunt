"""Finding the answer to an application question.

Split out of ``cli/apply.py`` (see docs/apply-seam-plan.md, Phase 1). Two
sources, in order: answers the operator saved for this run, then answers the
evaluation report drafted. Pure text work -- no browser, no I/O beyond reading
the saved-answers file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def _normalize_question(question: str) -> str:
    text = re.sub(r"\s+", " ", question or "").strip().lower()
    text = re.sub(r"\s*\*\s*", " ", text)
    text = re.sub(r"\bthis field is required\b", " ", text)
    text = re.sub(r"\b\d+\s*-\s*\d+\s*paragraphs?\b", " ", text)
    text = re.sub(r"\b\d+\s*paragraphs?\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _section_blocks_matching(section: str, keywords: list[str]) -> list[str]:
    blocks = re.split(r"\n(?=###?\s+|\*\*[^*\n]+:\*\*)", section)
    matches = []
    for block in blocks:
        lower = block.lower()
        if any(keyword in lower for keyword in keywords):
            matches.append(block)
    return matches


def _clean_report_answer(block: str) -> str:
    lines = []
    for line in block.splitlines():
        cleaned = re.sub(r"^#{2,4}\s*", "", line).strip()
        cleaned = re.sub(r"^\*\*([^*]+)\*\*:?\s*", "", cleaned).strip()
        cleaned = cleaned.lstrip("> ").strip()
        if cleaned and not cleaned.lower().startswith("section g"):
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _find_report_answer(question: str, report_context: dict | None) -> str:
    if not report_context:
        return ""
    section = report_context.get("application_section") or ""
    if not section:
        return ""
    q = question.lower()
    candidates: list[str] = []
    if "why" in q:
        candidates = _section_blocks_matching(section, ["why", "role", "company"])
    elif "additional" in q or "anything else" in q or "other information" in q:
        candidates = _section_blocks_matching(section, ["additional", "talking points", "application"])
    elif "fit" in q or "great" in q:
        candidates = _section_blocks_matching(section, ["fit", "good fit", "great fit"])
    elif "achievement" in q or "experience" in q or "background" in q or "relevant" in q:
        candidates = _section_blocks_matching(section, ["achievement", "experience", "relevant"])
    if not candidates:
        return ""
    answer = _clean_report_answer(candidates[0])
    return answer[:1800]


def _load_saved_apply_answers(artifact_dir: Path) -> list[dict[str, str]]:
    path = artifact_dir / "apply-review.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    answers = payload.get("answers") or []
    if not isinstance(answers, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in answers:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if question and answer:
            cleaned.append({"question": question, "answer": answer})
    return cleaned


def _find_saved_apply_answer(question: str, report_context: dict | None) -> str:
    if not report_context:
        return ""
    saved = report_context.get("saved_answers") or []
    if not saved:
        return ""
    from rapidfuzz import fuzz

    question_norm = _normalize_question(question)
    if not question_norm:
        return ""
    best_answer = ""
    best_score = 0.0
    for item in saved:
        candidate_q = _normalize_question(str(item.get("question") or ""))
        answer = str(item.get("answer") or "").strip()
        if not candidate_q or not answer:
            continue
        score = fuzz.token_set_ratio(question_norm, candidate_q) / 100
        if score > best_score:
            best_score = score
            best_answer = answer
    return best_answer if best_score >= 0.82 else ""


def _answer_for_application_question(
    question: str,
    *,
    company: str | None,
    role: str | None,
    report_context: dict | None = None,
) -> str:
    """Return an answer for an application question.

    Sources, in order:
      1. Saved answer from a prior apply session (``saved_answers`` in report_context).
      2. Section G draft answers from the evaluation report (``application_section``).

    Returns "" when no source is available so the form is left blank for the user
    to fill manually. The function does not synthesise candidate facts.
    """
    q = question.lower()
    if "reference" in q:
        return ""
    saved_answer = _find_saved_apply_answer(question, report_context)
    if saved_answer:
        return saved_answer
    report_answer = _find_report_answer(question, report_context)
    if report_answer:
        return report_answer
    return ""


def _radio_choice_for_question(question: str) -> str:
    q = question.lower()
    if "emea" in q or "apac" in q:
        return "No"
    if "north america" in q or "located in" in q:
        return "Yes"
    if "legally" in q and "work" in q:
        return "Yes"
    if "sponsor" in q or "sponsorship" in q:
        return "No"
    return ""
