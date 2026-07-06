from __future__ import annotations

from pathlib import Path

import pytest

from job_hunt.services.workday import employer_config as we


def _write_yaml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def employer_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "workday-employers"
    target.mkdir()
    monkeypatch.setattr(we, "_EMPLOYER_DIR", target)
    return target


def test_select_employer_config_picks_url_match(employer_dir: Path) -> None:
    _write_yaml(
        employer_dir / "acme.yml",
        """
detect:
  url_contains: "acme.wd5.myworkdayjobs.com"
ops:
  - kind: dropdown
    summary: "test"
    strategies:
      - {type: by_label, label: "Test?"}
    choices: ["Yes"]
""",
    )
    name, cfg = we.select_employer_config("https://acme.wd5.myworkdayjobs.com/job/123")
    assert name == "acme.yml"
    assert cfg["ops"][0]["summary"] == "test"


def test_select_employer_config_falls_back_to_default(employer_dir: Path) -> None:
    _write_yaml(
        employer_dir / "_default.yml",
        """
ops:
  - kind: dropdown
    summary: "default"
    strategies:
      - {type: by_label, label: "Default?"}
    choices: ["Yes"]
""",
    )
    _write_yaml(
        employer_dir / "acme.yml",
        """
detect:
  url_contains: "acme.example.com"
ops: []
""",
    )
    name, cfg = we.select_employer_config("https://other.example.com/")
    assert name == "_default.yml"
    assert cfg["ops"][0]["summary"] == "default"


def test_select_employer_config_uses_embedded_when_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(we, "_EMPLOYER_DIR", tmp_path / "does-not-exist")
    name, cfg = we.select_employer_config("https://anything.example.com/")
    assert name == "<embedded-fallback>"
    assert cfg["ops"], "embedded fallback should ship a non-empty op list"


def test_select_employer_config_url_contains_accepts_list(employer_dir: Path) -> None:
    _write_yaml(
        employer_dir / "acme.yml",
        """
detect:
  url_contains:
    - "primary-host.example.com"
    - "alt.example.com"
ops: []
""",
    )
    name, _ = we.select_employer_config("https://alt.example.com/job/1")
    assert name == "acme.yml"


def test_choices_for_op_reads_plain_choices() -> None:
    op = {"kind": "dropdown", "choices": ["Yes", "No"]}
    assert we.choices_for_op(op, {}) == ["Yes", "No"]


def test_choices_for_op_branches_on_values_key() -> None:
    op = {
        "kind": "dropdown",
        "choices_by": {
            "key": "category",
            "values": {
                "A": ["Category A applies to me", "Category A"],
                "B": ["Category B"],
            },
        },
    }
    assert we.choices_for_op(op, {"category": "A"}) == [
        "Category A applies to me",
        "Category A",
    ]
    assert we.choices_for_op(op, {"category": "B"}) == ["Category B"]
    assert we.choices_for_op(op, {}) == []
    assert we.choices_for_op(op, {"category": "Z"}) == []


def test_resolve_value_prefers_literal_over_value_from() -> None:
    op = {"value": "literal", "value_from": "ignored_key"}
    assert we.resolve_value(op, {"ignored_key": "would-have-won"}) == "literal"


def test_resolve_value_falls_back_to_values_dict() -> None:
    op = {"value_from": "graduation_date"}
    assert we.resolve_value(op, {"graduation_date": "07/31/2026"}) == "07/31/2026"
    assert we.resolve_value(op, {}) == ""


def test_load_employer_configs_skips_invalid_yaml(employer_dir: Path) -> None:
    _write_yaml(employer_dir / "good.yml", "ops: []\ndetect: {url_contains: x.com}\n")
    # Mapping with a duplicate-tagged scalar that pyyaml refuses to parse
    _write_yaml(employer_dir / "broken.yml", "ops: !!str [\n")
    _write_yaml(employer_dir / "scalar.yml", "just-a-bare-string\n")
    configs = we.load_employer_configs()
    names = [name for name, _ in configs]
    assert "good.yml" in names
    # broken.yml raises yaml.YAMLError → caught by the loader
    assert "broken.yml" not in names
    # scalar.yml parses to a plain string, not a dict → also dropped
    assert "scalar.yml" not in names


def test_embedded_fallback_strips_by_index_strategies():
    """Embedded fallback must not blind-fill by dropdown position (P0 fix)."""
    from job_hunt.services.workday.employer_config import (
        _FALLBACK_CONFIG,
        _sanitize_embedded_fallback,
    )

    cfg = _sanitize_embedded_fallback(_FALLBACK_CONFIG)
    for op in cfg["ops"]:
        for strategy in op.get("strategies", []):
            assert strategy.get("type") != "by_index", (
                "embedded fallback must not carry positional strategies — "
                f"op {op.get('summary')!r} still has by_index"
            )
    # Strategy-based ops that survived must still carry a label/text strategy;
    # ops whose only strategy was positional are dropped entirely. Text ops
    # (label+value, no strategies key) are untouched and safe.
    for op in cfg["ops"]:
        if "strategies" in op:
            assert op["strategies"], f"op {op.get('summary')!r} left with no strategy"
    # by_label survivors are preserved (not everything is dropped).
    assert cfg["ops"], "sanitized fallback should retain label-matched ops"
