"""Load per-employer Workday application-questions configs.

Each yaml under ``profile/workday-employers/*.yml`` describes a single Workday host's
application questions:

```yaml
detect:
  url_contains: "example.wd1.myworkdayjobs.com"  # str or list[str]

ops:
  - kind: dropdown
    summary: "current program"
    strategies:
      - {type: by_label, label: "Please select your current program."}
      - {type: by_index, index: 2}
    choices: ["Master of Data Analytics", "Other"]
  - kind: dropdown
    summary: "eligibility category"
    strategies:
      - {type: in_question, label: "eligible for this position"}
    choices_by:
      key: cowork_eligibility_category
      values:
        A: ["Category A applies to me", ...]
        B: ["Category B", ...]
    on_skip: "Workday eligibility A/B: dropdown not found"
  - kind: text
    summary: "GPA"
    label: "cumulative GPA"
    value_from: gpa_4_scale
    force: true
  - kind: date
    summary: "graduation date"
    value_from: graduation_date
```

A file named ``_default.yml`` is treated as the fallback used when no other file's
``url_contains`` matches the current page url. If both ``_default.yml`` and the
directory itself are missing, an embedded generic ``_FALLBACK_CONFIG`` provides
safe Workday defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_EMPLOYER_DIR = Path("profile/workday-employers")


# Embedded fallback used only when no Workday employer yaml is available.
# Keep this generic; employer-specific answers belong in yaml files.
_FALLBACK_CONFIG: dict[str, Any] = {
    "detect": {"url_contains": ["myworkdayjobs.com"]},
    "ops": [
        {
            "kind": "dropdown",
            "summary": "Are you a student enrolled in academic studies at a post-secondary i",
            "strategies": [
                {"type": "by_label", "label": "Are you a student enrolled in academic studies at a post-secondary institution (college or university)?"},
                {"type": "by_index", "index": 0},
            ],
            "choices": ["Yes"],
        },
        {
            "kind": "dropdown",
            "summary": "What post-secondary institution are you currently attending? Please s",
            "strategies": [
                {"type": "by_label", "label": "What post-secondary institution are you currently attending? Please select from the list below. If your institution is not listed, select 'Other' and provide the official name of your school (do not include department or faculty names)."},
                {"type": "by_index", "index": 1},
                {"type": "in_question", "label": "post-secondary institution"},
            ],
            "choices": ["Example University", "Other"],
        },
        {
            "kind": "dropdown",
            "summary": "Please select your current program.",
            "strategies": [
                {"type": "by_label", "label": "Please select your current program."},
                {"type": "by_index", "index": 2},
            ],
            "choices": ["Master of Data Analytics", "Master's Degree", "Masters", "Graduate", "Other"],
        },
        {
            "kind": "dropdown",
            "summary": "Please select your declared major.",
            "strategies": [
                {"type": "by_label", "label": "Please select your declared major."},
                {"type": "by_index", "index": 3},
            ],
            "choices": ["IT/Computer Science", "Data Analytics", "Analytics", "Computer Science", "Other"],
        },
        {
            "kind": "dropdown",
            "summary": "Please select your current year of study.",
            "strategies": [
                {"type": "by_label", "label": "Please select your current year of study."},
                {"type": "by_index", "index": 4},
            ],
            "choices": ["Graduate Year 2", "Graduate Year 1", "2nd Year", "Second Year", "Graduate", "Other"],
        },
        {
            "kind": "dropdown",
            "summary": "Are you currently or have you previously been involved in any clubs o",
            "strategies": [
                {"type": "by_label", "label": "Are you currently or have you previously been involved in any clubs or associations?"},
                {"type": "by_index", "index": 5},
            ],
            "choices": ["No"],
        },
        {
            "kind": "dropdown",
            "summary": "I confirm that I have applied to no more than my top three preferred r",
            "strategies": [
                {"type": "by_label", "label": "I confirm that I have applied to no more than my top three preferred roles. I understand that I may be considered for additional roles based on my skills and experience."},
                {"type": "by_index", "index": 7},
            ],
            "choices": ["Yes"],
        },
        {
            "kind": "dropdown",
            "summary": "Do you currently hold a valid Real Estate License?",
            "strategies": [
                {"type": "by_label", "label": "Do you currently hold a valid Real Estate License?"},
                {"type": "by_index", "index": 9},
            ],
            "choices": ["No"],
        },
        {
            "kind": "dropdown",
            "summary": "Workday eligibility category",
            "filled_message": "Workday eligibility category",
            "strategies": [
                {"type": "in_question", "label": "eligible for this position"},
                {"type": "in_question", "label": "fit into category A"},
                {"type": "in_question", "label": "category A or category B"},
                {"type": "in_question", "label": "Please indicate whether you fit into category"},
            ],
            "choices_by": {
                "key": "cowork_eligibility_category",
                "values": {
                    "A": [
                        "Category A applies to me", "Category A", "(A)", "A - Returning", "A - returning",
                        "returning back to school", "returning to school",
                        "returning to complete", "Be returning back to school",
                    ],
                    "B": [
                        "Category B", "(B)", "B - Mandatory", "mandatory component",
                        "work term as a mandatory",
                    ],
                },
            },
            "on_skip": "Workday eligibility category A/B: dropdown not found — needs manual selection.",
        },
        {
            "kind": "dropdown",
            "summary": "Workday legal work permission → Yes",
            "filled_message": "Workday legal work permission → Yes",
            "strategies": [
                {"type": "in_question", "label": "legally permitted to work"},
                {"type": "containing_label", "label": "legally permitted to work"},
                {"type": "by_label", "label": "Are you legally permitted to work in the country where this job is located?"},
            ],
            "choices": ["Yes"],
            "on_skip": "Workday legal work permission: dropdown not found — needs manual selection.",
        },
        {
            "kind": "text",
            "summary": "official name of your school",
            "label": "official name of your school",
            "value": "Example University",
        },
        {
            "kind": "text",
            "summary": "current program",
            "label": "current program",
            "value": "Master of Data Analytics",
        },
        {
            "kind": "text",
            "summary": "declared major",
            "label": "declared major",
            "value": "Data Analytics",
        },
        {
            "kind": "date",
            "summary": "graduation date (date input)",
            "filled_message": "Workday question field: graduation date (date input)",
            "value_from": "graduation_date",
        },
        {
            "kind": "text",
            "summary": "GPA",
            "filled_message": "Workday question field: GPA",
            "label": "cumulative GPA",
            "value_from": "gpa_4_scale",
            "force": True,
        },
    ],
}


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _matches_url(config: dict[str, Any], url: str) -> bool:
    detect = (config or {}).get("detect") or {}
    for needle in _coerce_str_list(detect.get("url_contains")):
        if needle and needle in url:
            return True
    return False


def load_employer_configs() -> list[tuple[str, dict[str, Any]]]:
    """Return ``[(filename, config)]`` for every parseable yaml under the dir."""
    out: list[tuple[str, dict[str, Any]]] = []
    if not _EMPLOYER_DIR.exists():
        return out
    for path in sorted(_EMPLOYER_DIR.glob("*.yml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if isinstance(data, dict):
            out.append((path.name, data))
    return out


def select_employer_config(url: str) -> tuple[str, dict[str, Any]]:
    """Pick the right employer config for ``url``.

    Priority:
      1. First non-default yaml whose ``detect.url_contains`` substring matches.
      2. ``_default.yml`` (or any filename starting with ``_default``).
      3. Embedded generic ``_FALLBACK_CONFIG``.

    Returns ``(source_name, config)`` so callers can surface which config drove the run.
    """
    configs = load_employer_configs()
    default: tuple[str, dict[str, Any]] | None = None
    for name, config in configs:
        if name.startswith("_default"):
            default = (name, config)
            continue
        if _matches_url(config, url):
            return name, config
    if default is not None:
        return default
    return "<embedded-fallback>", _FALLBACK_CONFIG


def choices_for_op(op: dict[str, Any], values: dict[str, str]) -> list[str]:
    """Resolve an op's ``choices`` list, honouring ``choices_by`` value-driven branches."""
    choices_by = op.get("choices_by")
    if isinstance(choices_by, dict):
        key = choices_by.get("key")
        mapping = choices_by.get("values") or {}
        selector = values.get(key, "") if key else ""
        branch = mapping.get(selector)
        if branch:
            return [str(c) for c in branch if c]
    return [str(c) for c in (op.get("choices") or []) if c]


def resolve_value(op: dict[str, Any], values: dict[str, str]) -> str:
    """Return ``op['value']`` or ``values[op['value_from']]`` (empty string when missing)."""
    if "value" in op and op["value"] is not None:
        return str(op["value"])
    key = op.get("value_from")
    if key:
        return str(values.get(key, ""))
    return ""
