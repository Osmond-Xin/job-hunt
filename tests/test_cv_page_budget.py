"""The résumé page budget and its trim ladder.

Guards the property that actually matters — a generated CV never ships at three
pages — plus the ordering rule that was wrong in the first implementation, where
cheap employment bullets were shed before the fat project write-ups.
"""

from __future__ import annotations

from job_hunt.nodes._cv_fit import next_trim, pdf_page_count

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
