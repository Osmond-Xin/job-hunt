"""Tests for P1-4 sub-phase 4b: scan_via_websearch (3rd-tier fallback)."""

from __future__ import annotations

from pathlib import Path

import yaml

from job_hunt.services import scan as scan_module
from job_hunt.services.scan import ScannedJob, scan_portals, scan_via_websearch
from job_hunt.services.web_search import SearchHit


class _StubProvider:
    """Synchronous in-memory WebSearchProvider for tests."""

    def __init__(self, hits: list[SearchHit], queries_log: list[str] | None = None) -> None:
        self._hits = hits
        self._log = queries_log if queries_log is not None else []

    def search(self, query: str, *, count=None, freshness=None) -> list[SearchHit]:
        self._log.append(query)
        return list(self._hits)


def test_scan_via_websearch_returns_jobs_from_hits() -> None:
    company = {
        "name": "Acme",
        "scan_method": "websearch",
        "scan_query": '"acme.com" "Data Analyst" OR "Data Scientist"',
    }
    provider = _StubProvider([
        SearchHit(
            title="Senior Data Analyst @ Acme",
            url="https://acme.com/careers/data-analyst-1",
            description="Toronto, remote.",
        ),
        SearchHit(
            title="Data Scientist | Acme",
            url="https://acme.com/careers/ds-2",
            description="",
        ),
    ])

    jobs = scan_via_websearch(company, provider)

    assert len(jobs) == 2
    assert jobs[0].title == "Senior Data Analyst"
    assert jobs[0].company == "Acme"  # parsed-then-matched-then-resolved
    assert jobs[0].portal == "websearch"
    assert jobs[1].title == "Data Scientist"


def test_scan_via_websearch_keeps_configured_company_when_parsed_mismatch() -> None:
    """Aggregator hits where the parsed company doesn't match must keep the
    configured name to avoid bleeding cross-company results in."""
    company = {
        "name": "Acme",
        "scan_method": "websearch",
        "scan_query": "site:acme.com",
    }
    provider = _StubProvider([
        SearchHit(
            title="Senior Engineer @ DifferentCorp",
            url="https://aggregator.example.com/job/123",
            description="",
        ),
    ])
    jobs = scan_via_websearch(company, provider)
    assert len(jobs) == 1
    assert jobs[0].company == "Acme"  # NOT "DifferentCorp"
    assert jobs[0].title == "Senior Engineer"


def test_scan_via_websearch_dedups_within_call() -> None:
    company = {
        "name": "Acme",
        "scan_method": "websearch",
        "scan_query": "x",
    }
    provider = _StubProvider([
        SearchHit(title="Eng", url="https://acme.com/a", description=""),
        SearchHit(title="Eng (mirror)", url="https://acme.com/a", description=""),
    ])
    jobs = scan_via_websearch(company, provider)
    assert len(jobs) == 1


def test_scan_via_websearch_skips_when_no_query() -> None:
    company = {"name": "Acme", "scan_method": "websearch"}  # no scan_query
    provider = _StubProvider([
        SearchHit(title="Job", url="https://x.com/1", description=""),
    ])
    assert scan_via_websearch(company, provider) == []


def test_scan_via_websearch_skips_blank_titles() -> None:
    company = {"name": "Acme", "scan_method": "websearch", "scan_query": "x"}
    provider = _StubProvider([
        SearchHit(title="", url="https://acme.com/1", description=""),
    ])
    assert scan_via_websearch(company, provider) == []


def test_scan_portals_includes_websearch_companies_when_provider_present(
    tmp_path: Path, monkeypatch
) -> None:
    """Smoke test: scan_portals routes a websearch-method company through Brave."""
    portals = tmp_path / "portals.yml"
    portals.write_text(
        yaml.safe_dump(
            {
                "title_filter": {"positive": ["data analyst"], "negative": []},
                "tracked_companies": [
                    {
                        "name": "Acme",
                        "scan_method": "websearch",
                        "scan_query": "site:acme.com data analyst",
                        "enabled": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # Stub _known_urls/_known_company_roles/_append_pipeline so we don't write
    # to real data/ paths during the test
    monkeypatch.setattr(scan_module, "_known_urls", lambda: set())
    monkeypatch.setattr(scan_module, "_known_company_roles", lambda: set())
    monkeypatch.setattr(scan_module, "_write_web_stat", lambda *a, **kw: None)
    monkeypatch.setattr(scan_module, "_append_pipeline", lambda jobs: None)
    monkeypatch.setattr(scan_module, "_append_scan_history", lambda job: None)

    queries: list[str] = []
    provider = _StubProvider(
        [
            SearchHit(
                title="Senior Data Analyst @ Acme",
                url="https://acme.com/careers/da-1",
                description="Toronto",
            ),
        ],
        queries_log=queries,
    )

    result = scan_portals(
        config_path=portals,
        include_non_canada=True,
        apply=False,
        web_search_provider=provider,
        mode="full",  # exercise the no-augmentation path
    )
    assert result.scanned_companies == 1
    assert result.fetched_jobs == 1
    assert result.matched_jobs == 1
    assert queries == ["site:acme.com data analyst"]


def test_scan_portals_skips_websearch_companies_when_provider_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """Without a provider, scan_method=websearch companies stay invisible
    (the historical pre-Brave behavior)."""
    portals = tmp_path / "portals.yml"
    portals.write_text(
        yaml.safe_dump(
            {
                "title_filter": {"positive": ["data analyst"], "negative": []},
                "tracked_companies": [
                    {"name": "Acme", "scan_method": "websearch", "scan_query": "x"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scan_module, "_known_urls", lambda: set())
    monkeypatch.setattr(scan_module, "_known_company_roles", lambda: set())
    result = scan_portals(config_path=portals, web_search_provider=None)
    assert result.scanned_companies == 0
