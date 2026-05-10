"""Tests for the HTML dashboard generator."""

from __future__ import annotations

import json
import re
from pathlib import Path

from job_hunt.repositories.tracker_repo import TRACKER_HEADER
from job_hunt.services import dashboard_html


def _write_apps(path: Path, rows: list[str]) -> None:
    path.write_text(TRACKER_HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def test_generate_writes_html_with_kpis_and_table(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    out = tmp_path / "dashboard.html"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Anthropic | AI Engineer | 4.5/5 | Applied | ✅ | reports/0001.md | strong |",
            "| 2 | 2026-04-02 | OpenAI | Researcher | 4.0/5 | Evaluated | ❌ | reports/0002.md |  |",
            "| 3 | 2026-04-03 | Acme | PM | 2.5/5 | Discarded | ❌ | reports/0003.md |  |",
        ],
    )

    count = dashboard_html.generate(apps_path=apps, output_path=out)

    assert count == 3
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    # Skeleton
    assert "<title>Job Hunt — Dashboard</title>" in html
    assert "Updated " in html
    # KPIs / chart canvases
    for canvas_id in ("statusChart", "scoreChart", "timelineChart"):
        assert f'id="{canvas_id}"' in html
    # Each entry's company appears in the embedded JSON payload
    for company in ("Anthropic", "OpenAI", "Acme"):
        assert company in html


def test_payload_score_parses_to_float(tmp_path: Path) -> None:
    """Regression: scores like '4.5/5' must serialize as numeric 4.5 in the JS payload."""
    apps = tmp_path / "applications.md"
    out = tmp_path / "dashboard.html"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | Foo | Engineer | 4.5/5 | Applied | ✅ | reports/0001.md |  |",
        ],
    )
    dashboard_html.generate(apps_path=apps, output_path=out)
    html = out.read_text(encoding="utf-8")
    match = re.search(r"const raw = (\[.*?\]);", html, re.DOTALL)
    assert match is not None
    payload = json.loads(match.group(1))
    assert payload[0]["score"] == 4.5
    assert payload[0]["pdf"] is True


def test_generate_with_empty_tracker_creates_file(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    out = tmp_path / "dashboard.html"
    _write_apps(apps, [])
    count = dashboard_html.generate(apps_path=apps, output_path=out)
    assert count == 0
    assert out.exists()
    # No tracker rows → no JS Chart() calls fire
    assert "<title>Job Hunt — Dashboard</title>" in out.read_text(encoding="utf-8")


def test_payload_sorts_descending_by_num(tmp_path: Path) -> None:
    apps = tmp_path / "applications.md"
    out = tmp_path / "dashboard.html"
    _write_apps(
        apps,
        [
            "| 1 | 2026-04-01 | A | r | 4.0/5 | Applied | ✅ | reports/0001.md |  |",
            "| 5 | 2026-04-05 | B | r | 4.0/5 | Applied | ✅ | reports/0005.md |  |",
            "| 3 | 2026-04-03 | C | r | 4.0/5 | Applied | ✅ | reports/0003.md |  |",
        ],
    )
    dashboard_html.generate(apps_path=apps, output_path=out)
    html = out.read_text(encoding="utf-8")
    match = re.search(r"const raw = (\[.*?\]);", html, re.DOTALL)
    payload = json.loads(match.group(1))
    nums = [r["num"] for r in payload]
    assert nums == sorted(nums, reverse=True)
