"""The résumé page budget and its trim ladder, and the cover letter's page budget.

Guards the property that actually matters — a generated CV never ships at three
pages — plus the ordering rule that was wrong in the first implementation, where
cheap employment bullets were shed before the fat project write-ups. The cover
letter side guards CLAUDE.md §2's other half: a pipeline-generated letter that
overflows its one-page budget must be reported, not shipped silently.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from job_hunt.models.job import ArchetypeResult
from job_hunt.nodes import _quality as quality_module
from job_hunt.nodes import artifact_paths as artifact_paths_module
from job_hunt.nodes import cover_letter as cover_letter_module
from job_hunt.nodes._cv_fit import next_trim, pdf_page_count
from job_hunt.nodes.cover_letter import generate_cover_letter
from job_hunt.services.llm.base import ChatResult

CV = """## Experience

### Engineer — Acme | Jan 2026 – Mar 2026

- recent bullet one
- recent bullet two
- recent bullet three

### Engineer — Older Co | Jan 2020 – Dec 2021

- old bullet one
- old bullet two

## Projects

### Alpha — flagship

- alpha one
- alpha two
- alpha three
- alpha four
- alpha five
- alpha six

### Beta — second

- beta one
- beta two

### Gamma — third

- gamma one

## Early Career

Some Role, Some Company / Another Role, Another Company

## Education

### Master of Data Analytics

- honour line
"""


def _apply(md: str, times: int) -> tuple[str, list[str]]:
    log: list[str] = []
    for _ in range(times):
        step = next_trim(md)
        if step is None:
            break
        md, what = step
        log.append(what)
    return md, log


def test_extra_projects_go_before_anything_else():
    _, log = _apply(CV, 1)
    assert log == ['dropped project “Gamma — third”']


def test_fat_project_bullets_go_before_employment_bullets():
    md, log = _apply(CV, 4)
    # Gamma, then Alpha's tail down to the cap — no experience bullet yet.
    assert not any("experience" in entry for entry in log)
    assert "alpha six" not in md and "alpha five" not in md
    assert "old bullet two" in md


def test_early_career_dropped_before_cutting_into_roles():
    md, log = _apply(CV, 6)
    assert "dropped the Early Career block" in log
    assert "## Early Career" not in md
    assert "old bullet one" in md


def test_every_dated_role_survives_a_full_trim():
    md, _ = _apply(CV, 60)
    assert "Engineer — Acme" in md
    assert "Engineer — Older Co" in md
    # Education is never a trim target.
    assert "Master of Data Analytics" in md


def test_trimming_terminates():
    md, _ = _apply(CV, 200)
    assert next_trim(md) is None


def test_page_count_ignores_the_pages_tree_node():
    # A two-page document: the /Pages node carries /Count 2 and each /Page does not.
    pdf = b"<< /Type /Pages /Kids [1 0 R 2 0 R] /Count 2 >> /Type /Page /Type /Page"
    assert pdf_page_count(pdf) == 2
    assert pdf_page_count(b"no page tree here") is None


# ----- generate_cover_letter node: page budget is measured, never auto-trimmed -----


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


def _fake_html_to_pdf(page_count: int):
    """Stand in for Playwright: writes a fake PDF whose /Count is fixed, so the
    node's pdf_page_count() call needs no real browser or renderer."""

    async def fake(html_path: str, pdf_path: str, *, paper_size: str = "letter") -> None:
        Path(pdf_path).write_bytes(f"<< /Type /Pages /Count {page_count} >>".encode())

    return fake


_LETTER_STATE = {
    "generate_cover_letter": True,
    "run_id": "abc123",
    "jd_meta": None,
    "profile": None,
    "cv": "",
    "jd_text": "We need a backend engineer.",
    "archetype": ArchetypeResult(),
    "evaluation_blocks": {"cv_match": "match", "personalization": "plan"},
    "mode": "full",
}


def test_cover_letter_overflow_is_reported_not_trimmed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(artifact_paths_module, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        quality_module, "call_node_llm_or_fallback", _fake_llm("Paragraph one.\n\nParagraph two.\n")
    )
    monkeypatch.setattr(cover_letter_module, "_html_to_pdf", _fake_html_to_pdf(2))

    result = asyncio.run(generate_cover_letter(dict(_LETTER_STATE), None))

    assert result["cover_letter_path"] is not None
    assert any(
        "cover letter is 2 pages" in w and "budget is 1" in w
        for w in result["artifact_warnings"]
    )
    # No trim ladder for the letter — the body handed to the report is untouched.
    assert result["evaluation_blocks"]["cover_letter"] == "Paragraph one.\n\nParagraph two."


def test_cover_letter_within_budget_has_no_warning(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(artifact_paths_module, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", _fake_llm("Paragraph one.\n"))
    monkeypatch.setattr(cover_letter_module, "_html_to_pdf", _fake_html_to_pdf(1))

    result = asyncio.run(generate_cover_letter(dict(_LETTER_STATE), None))

    assert result["cover_letter_path"] is not None
    assert result["artifact_warnings"] == []
