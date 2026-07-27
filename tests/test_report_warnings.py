"""Withheld/unverified artifacts must survive past the console."""

from __future__ import annotations

import asyncio

from job_hunt.models.job import JobMeta
from job_hunt.nodes.report import write_report


def test_artifact_warnings_are_written_into_the_report() -> None:
    """Console output scrolls away during a 50-job batch; the report does not."""
    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "evaluation_blocks": {},
        "artifact_warnings": ["cover letter withheld (audit failed): invented a metric"],
        "errors": [],
    }
    result = asyncio.run(write_report(state, None))
    report = result["report_md"]
    assert "Artifacts needing review" in report
    assert "cover letter withheld" in report
    assert "Do not send these without reading them first." in report


def test_clean_run_adds_no_warning_section() -> None:
    state = {
        "run_id": "abc123",
        "jd_meta": JobMeta(company="Acme", title="Engineer"),
        "evaluation_blocks": {},
        "errors": [],
    }
    result = asyncio.run(write_report(state, None))
    assert "Artifacts needing review" not in result["report_md"]
