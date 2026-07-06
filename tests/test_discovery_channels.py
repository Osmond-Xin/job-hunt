"""Tests for tier-3 cross-employer discovery channels.

These exercise the cross-employer search tier added for Pain Point 1
(LinkedIn / Indeed / Glassdoor / WaterlooWorks / TalentEgg / ...). The tier
runs WebSearch queries from ``portals.yml::discovery_channels`` and emits
``ScannedJob`` rows that flow through the same title / location / dedup
filters as direct-ATS and per-company-websearch tiers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from job_hunt.services.scan import ScannedJob, scan_discovery_channels
from job_hunt.services.web_search import SearchHit


class _StubProvider:
    """Records queries and returns scripted hits for each."""

    def __init__(self, script: dict[str, list[SearchHit]] | None = None) -> None:
        self.script = script or {}
        self.calls: list[str] = []

    def search(
        self,
        query: str,
        *,
        count: int | None = None,
        freshness: str | None = None,
    ) -> list[SearchHit]:
        self.calls.append(query)
        return list(self.script.get(query, []))


def _profile(tmp_path: Path, *, roles: list[str], locations: list[str], mode: str = "full") -> Path:
    """Write a minimal profile.yml that ``discovery_context`` can read."""
    import yaml

    profile = tmp_path / "profile" / "profile.yml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        yaml.safe_dump(
            {
                "mode": mode,
                "candidate": {
                    "target_roles": roles,
                    "target_locations": locations,
                },
            }
        ),
        encoding="utf-8",
    )
    return profile


def test_disabled_channels_do_not_run(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=["AI Engineer"], locations=["Toronto"])

    provider = _StubProvider()
    channels = [
        {"id": "linkedin", "enabled": False, "modes": ["full"], "query_template": "{role}"},
    ]
    jobs = scan_discovery_channels(channels, provider, mode="full")

    assert jobs == []
    assert provider.calls == []


def test_channel_mode_mismatch_is_skipped(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=["AI Engineer"], locations=["Toronto"])

    provider = _StubProvider()
    channels = [
        {
            "id": "waterlooworks",
            "enabled": True,
            "modes": ["student"],
            "query_template": "{role}",
        },
    ]
    jobs = scan_discovery_channels(channels, provider, mode="full")

    assert jobs == []
    assert provider.calls == []


def test_template_expands_over_roles_and_locations(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _profile(
        tmp_path,
        roles=["AI Engineer", "ML Engineer"],
        locations=["Toronto", "Vancouver"],
    )

    provider = _StubProvider()
    channels = [
        {
            "id": "linkedin",
            "enabled": True,
            "modes": ["full"],
            "query_template": "{role} {location} site:linkedin.com/jobs",
        },
    ]
    scan_discovery_channels(channels, provider, mode="full")

    # 2 roles × 2 locations = 4 distinct queries.
    assert len(provider.calls) == 4
    assert "AI Engineer Toronto site:linkedin.com/jobs" in provider.calls
    assert "ML Engineer Vancouver site:linkedin.com/jobs" in provider.calls


def test_no_target_roles_skips_channel(monkeypatch, tmp_path) -> None:
    """A channel with no profile roles to interpolate yields no queries."""
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=[], locations=["Toronto"])

    provider = _StubProvider()
    channels = [
        {"id": "linkedin", "enabled": True, "modes": ["full"], "query_template": "{role}"},
    ]
    jobs = scan_discovery_channels(channels, provider, mode="full")

    assert jobs == []
    assert provider.calls == []


def test_missing_locations_falls_back_to_canada(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=["AI Engineer"], locations=[])

    provider = _StubProvider()
    channels = [
        {
            "id": "linkedin",
            "enabled": True,
            "modes": ["full"],
            "query_template": "{role} {location}",
        },
    ]
    scan_discovery_channels(channels, provider, mode="full")

    assert provider.calls == ["AI Engineer Canada"]


def test_returns_scanned_jobs_with_channel_portal(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=["AI Engineer"], locations=["Toronto"])

    provider = _StubProvider(
        script={
            "AI Engineer Toronto site:linkedin.com/jobs": [
                SearchHit(
                    title="Senior AI Engineer at Cohere",
                    url="https://www.linkedin.com/jobs/view/123",
                    description="Toronto",
                ),
            ],
        }
    )
    channels = [
        {
            "id": "linkedin",
            "enabled": True,
            "modes": ["full"],
            "query_template": "{role} {location} site:linkedin.com/jobs",
        },
    ]
    jobs = scan_discovery_channels(channels, provider, mode="full")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.portal == "linkedin"
    assert job.url == "https://www.linkedin.com/jobs/view/123"
    assert job.title == "Senior AI Engineer"  # parsed from "Title at Company"
    assert job.company == "Cohere"
    assert job.source == "AI Engineer Toronto site:linkedin.com/jobs"


def test_dedups_same_url_across_channels(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=["AI Engineer"], locations=["Toronto"])

    shared_hit = SearchHit(
        title="AI Engineer at Cohere",
        url="https://example.com/jobs/1",
        description="",
    )
    provider = _StubProvider(
        script={
            "AI Engineer site:a": [shared_hit],
            "AI Engineer site:b": [shared_hit],
        }
    )
    channels = [
        {"id": "a", "enabled": True, "modes": ["full"], "query_template": "{role} site:a"},
        {"id": "b", "enabled": True, "modes": ["full"], "query_template": "{role} site:b"},
    ]
    jobs = scan_discovery_channels(channels, provider, mode="full")

    assert len(jobs) == 1  # second occurrence deduped


def test_channel_id_filter_restricts_to_one_channel(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=["AI Engineer"], locations=["Toronto"])

    provider = _StubProvider()
    channels = [
        {"id": "linkedin", "enabled": True, "modes": ["full"], "query_template": "{role} linkedin"},
        {"id": "indeed", "enabled": True, "modes": ["full"], "query_template": "{role} indeed"},
    ]
    scan_discovery_channels(channels, provider, mode="full", channel_id="indeed")

    assert provider.calls == ["AI Engineer indeed"]


def test_provider_exception_is_swallowed(monkeypatch, tmp_path) -> None:
    """A provider blowup on one query should not abort the whole scan."""
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=["AI Engineer", "Data Scientist"], locations=["Toronto"])

    class _ExplodingThenStable:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, **kwargs: Any) -> list[SearchHit]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider down")
            return [
                SearchHit(
                    title="Data Scientist at Cohere",
                    url="https://example.com/2",
                    description="",
                ),
            ]

    provider = _ExplodingThenStable()
    channels = [
        {"id": "linkedin", "enabled": True, "modes": ["full"], "query_template": "{role}"},
    ]
    jobs = scan_discovery_channels(channels, provider, mode="full")

    # First query raised, second returned a hit.
    assert provider.calls == 2
    assert len(jobs) == 1
    assert jobs[0].url == "https://example.com/2"


def test_discovery_context_reads_top_level_target_roles_and_location(
    monkeypatch, tmp_path: Path
) -> None:
    """Real-world profile schema uses ``target_roles.primary/secondary`` at top
    level (not under ``candidate.``) and a single ``location.city`` block.
    Discovery context must collect roles + derive locations from both."""
    import yaml

    from job_hunt.services.profile_loader import discovery_context

    profile = tmp_path / "profile" / "profile.yml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        yaml.safe_dump(
            {
                "mode": "student",
                "candidate": {"full_name": "X"},
                "target_roles": {
                    "primary": ["AI Engineer", "Data Analyst"],
                    "secondary": ["Data Scientist", "Data Analyst"],  # duped
                },
                "location": {"city": "Niagara Falls, ON", "country": "Canada"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    ctx = discovery_context()
    assert ctx["roles"] == ["AI Engineer", "Data Analyst", "Data Scientist"]
    assert ctx["locations"] == ["Niagara Falls, ON, Canada"]


def test_discovery_context_legacy_flat_schema_still_works(
    monkeypatch, tmp_path: Path
) -> None:
    """Legacy ``candidate.target_roles: [...]`` continues to read correctly."""
    import yaml

    from job_hunt.services.profile_loader import discovery_context

    profile = tmp_path / "profile" / "profile.yml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        yaml.safe_dump(
            {
                "candidate": {
                    "target_roles": ["AI Engineer"],
                    "target_locations": ["Toronto"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    ctx = discovery_context()
    assert ctx["roles"] == ["AI Engineer"]
    assert ctx["locations"] == ["Toronto"]


def test_student_mode_channel_runs_under_student_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _profile(tmp_path, roles=["Data Analyst"], locations=["Toronto"], mode="student")

    provider = _StubProvider(
        script={
            'Data Analyst co-op site:waterlooworks.uwaterloo.ca': [
                SearchHit(
                    title="Data Analyst Co-op at Shopify",
                    url="https://waterlooworks.uwaterloo.ca/job/1",
                    description="",
                )
            ]
        }
    )
    channels = [
        {
            "id": "waterlooworks",
            "enabled": True,
            "modes": ["student"],
            "query_template": "{role} co-op site:waterlooworks.uwaterloo.ca",
        },
    ]
    jobs = scan_discovery_channels(channels, provider, mode="student")

    assert len(jobs) == 1
    assert jobs[0].portal == "waterlooworks"


def test_channel_locations_override(monkeypatch, tmp_path) -> None:
    """A channel-level `locations:` list replaces the profile-wide locations."""
    monkeypatch.chdir(tmp_path)
    profile = _profile(tmp_path, roles=["AI Engineer"], locations=["Toronto"])

    provider = _StubProvider()
    channels = [
        {
            "id": "jobbank_immigration",
            "enabled": True,
            "modes": ["full"],
            "query_template": '"{role}" "{location}" site:jobbank.gc.ca',
            "locations": ["Moncton", "Thunder Bay"],
        },
    ]
    scan_discovery_channels(channels, provider, mode="full", profile_path=profile)

    assert provider.calls == [
        '"AI Engineer" "Moncton" site:jobbank.gc.ca',
        '"AI Engineer" "Thunder Bay" site:jobbank.gc.ca',
    ]
