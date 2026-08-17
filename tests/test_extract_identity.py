"""Where a job's company and title come from when the page will not say.

Four tracker rows were written on 2026-08-17 with an empty Company column and
a Role of "AI Solutions Engineer - adzuna.ca". Both halves break every
company+role check the project has.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from job_hunt.nodes import extract
from job_hunt.nodes.extract import _strip_board_suffix, extract_jd
from job_hunt.services.web_extract import WebExtractResult

_URL = "https://www.adzuna.ca/details/5833584853"
_JD = (
    "AI Solutions Engineer - adzuna.ca\n\nAI Solutions Engineer\n\n"
    "❮ back to last search\n\nCSC Generation\n\nHybrid- Toronto, Ontario\n\n"
    "About the Role\nYou will build and deploy AI tools across brands.\n\n"
    "Required Qualifications\n2-5 years building automations.\n\nApply for this job\n"
)


def test_strips_the_board_brand_but_not_a_real_title() -> None:
    assert _strip_board_suffix("AI Solutions Engineer - adzuna.ca") == "AI Solutions Engineer"
    assert _strip_board_suffix("Data Analyst – jobbank.gc.ca") == "Data Analyst"
    # A hyphen inside the role is not a suffix, and neither is a company name.
    assert (
        _strip_board_suffix("Systems Analyst - Information Management")
        == "Systems Analyst - Information Management"
    )
    assert _strip_board_suffix("Software Developer | Acme Inc.") == "Software Developer | Acme Inc."


def test_company_falls_back_to_what_discovery_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pipeline.md").write_text(
        "# Pipeline\n\n## Pending\n\n"
        f"- [ ] {_URL} | CSC Generation | AI Solutions Engineer | Canada | source: adzuna\n",
        encoding="utf-8",
    )

    async def _fake_extract(url: str, **kwargs: object) -> WebExtractResult:
        return WebExtractResult(
            url=url,
            text=_JD,
            adapter="http_extract",
            title="AI Solutions Engineer - adzuna.ca",
            company="",
            location="",
            ats="",
        )

    monkeypatch.setattr(extract, "extract_url_text", _fake_extract)

    result = asyncio.run(
        extract_jd({"input": _URL, "url": _URL, "source_type": "url", "errors": []}, None)
    )

    assert result["jd_meta"].company == "CSC Generation"
    assert result["jd_meta"].title == "AI Solutions Engineer"
