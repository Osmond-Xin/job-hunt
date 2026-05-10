"""Tests for P2-6 multi-offer comparison service."""

from __future__ import annotations

from pathlib import Path

from job_hunt.repositories.tracker_repo import TRACKER_HEADER
from job_hunt.services import compare_offers


def _write_apps(path: Path, rows: list[str]) -> None:
    path.write_text(TRACKER_HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def test_load_offers_resolves_known_ids(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "0001.md").write_text("# Anthropic report\n\nSection A: …", encoding="utf-8")

    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Anthropic | AI Engineer | 4.6/5 | Evaluated | ✅ | [1](reports/0001.md) | strong |",
            "| 2 | 2026-04-05 | OpenAI | Researcher | 4.0/5 | Applied | ✅ | reports/0002.md |  |",
        ],
    )

    offers, missing = compare_offers.load_offers(
        [1, 2], apps_path=apps, reports_dir=reports
    )

    assert missing == []
    assert len(offers) == 2
    assert offers[0].company == "Anthropic"
    assert "Anthropic report" in offers[0].report
    # offer #2's report.md is referenced but file doesn't exist — empty body
    assert offers[1].report == ""


def test_load_offers_reports_missing_ids(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | A | r | 4.0/5 | Applied | ✅ | reports/0001.md |  |",
        ],
    )
    offers, missing = compare_offers.load_offers([1, 99, 100], apps_path=apps)
    assert len(offers) == 1
    assert missing == ["99", "100"]


def test_render_prompt_includes_each_offer(tmp_path: Path) -> None:
    offers = [
        compare_offers.OfferContext(
            tracker_id=1, company="Anthropic", role="AI Engineer",
            tracker_score="4.6/5", status="Applied", date="2026-04-01",
            report="Acme report content",
        ),
        compare_offers.OfferContext(
            tracker_id=2, company="OpenAI", role="Researcher",
            tracker_score="4.0/5", status="Evaluated", date="2026-04-05",
            report="",
        ),
    ]
    rendered = compare_offers.render_prompt(offers)
    assert "Anthropic" in rendered and "OpenAI" in rendered
    assert "Multi-Offer Comparison" in rendered
    # the 10-dim weighted matrix table is in the rendered prompt
    assert "Alignment with North Star" in rendered
    assert "25%" in rendered
    # shared.md framing rules should be included via the {% include %} tag
    assert "Ethical Use" in rendered


def test_resolve_report_handles_bracket_notation(tmp_path: Path) -> None:
    """report column may be '[N](reports/N-slug.md)' or just 'reports/N.md'."""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "0042.md").write_text("body", encoding="utf-8")

    apps = tmp_path / "applications.md"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | A | r | 4.0/5 | Applied | ✅ | [42](reports/0042.md) |  |",
        ],
    )
    offers, _ = compare_offers.load_offers([1], apps_path=apps, reports_dir=reports)
    assert offers[0].report == "body"
