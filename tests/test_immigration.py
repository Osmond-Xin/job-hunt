from pathlib import Path

from job_hunt.services.immigration import (
    immigration_context,
    matched_places,
    place_tokens,
    priority_config,
)
from job_hunt.services.scan import _location_matches_canada


def _write_profile(tmp_path: Path, enabled: bool = True) -> Path:
    path = tmp_path / "profile.yml"
    path.write_text(
        f"""
immigration_priority:
  enabled: {str(enabled).lower()}
  provinces:
    - "New Brunswick"
    - "Manitoba"
  communities:
    - "Thunder Bay"
    - "Moncton"
  score_note: "Offers here advance PR."
""",
        encoding="utf-8",
    )
    return path


def test_priority_config_disabled_returns_empty(tmp_path):
    path = _write_profile(tmp_path, enabled=False)
    assert priority_config(path) == {}
    assert place_tokens(path) == []
    assert immigration_context("Moncton, NB", path) == ""


def test_priority_config_missing_file(tmp_path):
    assert priority_config(tmp_path / "nope.yml") == {}


def test_place_tokens_lowercased_deduped(tmp_path):
    path = _write_profile(tmp_path)
    assert place_tokens(path) == ["new brunswick", "manitoba", "thunder bay", "moncton"]


def test_matched_places(tmp_path):
    path = _write_profile(tmp_path)
    assert matched_places("Thunder Bay, ON (Hybrid)", path) == ["thunder bay"]
    assert matched_places("Toronto, ON", path) == []
    assert matched_places("", path) == []


def test_immigration_context_includes_note(tmp_path):
    path = _write_profile(tmp_path)
    ctx = immigration_context("Moncton, New Brunswick", path)
    assert "new brunswick" in ctx and "moncton" in ctx
    assert "Offers here advance PR." in ctx


def test_location_filter_covers_all_provinces():
    for loc in [
        "Moncton, New Brunswick",
        "Charlottetown, Prince Edward Island",
        "St. John's, Newfoundland",
        "Winnipeg, Manitoba",
        "Saskatoon, Saskatchewan",
        "Whitehorse, Yukon",
    ]:
        assert _location_matches_canada(loc), loc


def test_location_filter_still_blocks_non_canada():
    assert not _location_matches_canada("Austin, Texas, United States")
    assert not _location_matches_canada("London, United Kingdom")


def test_passes_canada_filter_keeps_unknown_location():
    """Empty location (WebSearch/discovery hits) must not be dropped as non-Canadian."""
    from job_hunt.services.scan import _passes_canada_filter

    assert _passes_canada_filter("") is True          # unknown → keep
    assert _passes_canada_filter("Moncton, NB") is True
    assert _passes_canada_filter("Austin, Texas, USA") is False
