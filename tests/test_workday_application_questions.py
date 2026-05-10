"""Tests for the Workday Application Questions yaml-driven dispatcher.

The dispatcher takes Playwright helpers as injected callables, so the tests use
``AsyncMock`` doubles instead of a real browser. We assert (1) strategy order is
honoured, (2) ``choices_by`` branches on values, (3) ``filled_message`` rendering
matches the historical inline messages verbatim, and (4) ``on_skip`` fires only
on full-strategy failure.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from job_hunt.services.workday.application_questions import (
    render_filled_message,
    run_question_ops,
)


def _short(text: str, limit: int) -> str:
    return text[:limit]


def _stub_helpers(
    *,
    by_label_returns=False,
    in_question_returns=False,
    containing_label_returns=False,
    by_index_returns=False,
    fill_text_returns=False,
    fill_date_returns=False,
):
    return {
        "by_label": AsyncMock(return_value=by_label_returns),
        "in_question": AsyncMock(return_value=in_question_returns),
        "containing_label": AsyncMock(return_value=containing_label_returns),
        "by_index": AsyncMock(return_value=by_index_returns),
        "fill_text": AsyncMock(return_value=fill_text_returns),
        "fill_date": AsyncMock(return_value=fill_date_returns),
    }


def test_render_filled_message_dropdown_uses_summary_truncation() -> None:
    op = {"summary": "x" * 100}
    msg = render_filled_message(op, "dropdown", short=_short)
    assert msg == f"Workday question: {'x' * 70}"


def test_render_filled_message_text_uses_label_with_field_prefix() -> None:
    op = {"summary": "summary used as fallback", "label": "cumulative GPA"}
    assert (
        render_filled_message(op, "text", short=_short)
        == "Workday question field: cumulative GPA"
    )


def test_render_filled_message_text_falls_back_to_summary() -> None:
    op = {"summary": "GPA summary"}
    assert (
        render_filled_message(op, "text", short=_short)
        == "Workday question field: GPA summary"
    )


def test_render_filled_message_date_format() -> None:
    op = {"summary": "graduation date (date input)"}
    assert (
        render_filled_message(op, "date", short=_short)
        == "Workday question field: graduation date (date input)"
    )


def test_render_filled_message_explicit_overrides_default() -> None:
    op = {"summary": "ignored", "filled_message": "Workday legal work permission → Yes"}
    assert (
        render_filled_message(op, "dropdown", short=_short)
        == "Workday legal work permission → Yes"
    )


def test_dispatcher_runs_strategies_in_order_and_stops_on_first_success() -> None:
    helpers = _stub_helpers(by_label_returns=False, by_index_returns=True)
    op = {
        "kind": "dropdown",
        "summary": "Please select your current program.",
        "strategies": [
            {"type": "by_label", "label": "Please select your current program."},
            {"type": "by_index", "index": 2},
        ],
        "choices": ["Master of Data Analytics", "Other"],
    }
    filled, skipped = asyncio.run(run_question_ops(
        page=object(), values={}, ops=[op], short=_short, **helpers
    ))
    helpers["by_label"].assert_awaited_once()
    helpers["by_index"].assert_awaited_once()
    assert filled == ["Workday question: Please select your current program."]
    assert skipped == []


def test_dispatcher_records_on_skip_when_all_strategies_fail() -> None:
    helpers = _stub_helpers()  # all helpers return False
    op = {
        "kind": "dropdown",
        "summary": "eligibility",
        "on_skip": "Workday eligibility A/B: dropdown not found",
        "strategies": [
            {"type": "in_question", "label": "fit into category A"},
            {"type": "in_question", "label": "category A or category B"},
        ],
        "choices": ["Category A applies to me"],
    }
    filled, skipped = asyncio.run(run_question_ops(
        page=object(), values={}, ops=[op], short=_short, **helpers
    ))
    assert filled == []
    assert skipped == ["Workday eligibility A/B: dropdown not found"]
    assert helpers["in_question"].await_count == 2


def test_dispatcher_choices_by_routes_on_values_key() -> None:
    helpers = _stub_helpers(in_question_returns=True)
    op = {
        "kind": "dropdown",
        "summary": "eligibility",
        "filled_message": "Workday eligibility category",
        "strategies": [{"type": "in_question", "label": "eligible"}],
        "choices_by": {
            "key": "cowork_eligibility_category",
            "values": {
                "A": ["Category A applies to me"],
                "B": ["Category B"],
            },
        },
    }
    filled, _ = asyncio.run(run_question_ops(
        page=object(), values={"cowork_eligibility_category": "B"},
        ops=[op], short=_short, **helpers
    ))
    in_q = helpers["in_question"]
    in_q.assert_awaited_once()
    assert in_q.await_args.args[2] == ["Category B"]
    assert filled == ["Workday eligibility category"]


def test_dispatcher_choices_by_skips_op_when_branch_missing() -> None:
    helpers = _stub_helpers(in_question_returns=True)
    op = {
        "kind": "dropdown",
        "summary": "eligibility",
        "strategies": [{"type": "in_question", "label": "eligible"}],
        "choices_by": {"key": "cowork_eligibility_category", "values": {"A": ["x"]}},
    }
    filled, skipped = asyncio.run(run_question_ops(
        page=object(), values={"cowork_eligibility_category": "Z"},
        ops=[op], short=_short, **helpers
    ))
    helpers["in_question"].assert_not_awaited()  # no choices => never invoke a strategy
    assert filled == []
    assert skipped == []


def test_dispatcher_text_op_resolves_value_from() -> None:
    helpers = _stub_helpers(fill_text_returns=True)
    op = {
        "kind": "text",
        "summary": "GPA",
        "label": "cumulative GPA",
        "value_from": "gpa_4_scale",
        "force": True,
        "filled_message": "Workday question field: GPA",
    }
    filled, _ = asyncio.run(run_question_ops(
        page="page-sentinel", values={"gpa_4_scale": "3.84"},
        ops=[op], short=_short, **helpers
    ))
    helpers["fill_text"].assert_awaited_once_with(
        "page-sentinel", "cumulative GPA", "3.84", force=True
    )
    assert filled == ["Workday question field: GPA"]


def test_dispatcher_text_op_skips_when_value_empty() -> None:
    helpers = _stub_helpers()
    op = {"kind": "text", "summary": "GPA", "label": "GPA", "value_from": "missing_key"}
    filled, _ = asyncio.run(run_question_ops(
        page=object(), values={}, ops=[op], short=_short, **helpers
    ))
    helpers["fill_text"].assert_not_awaited()
    assert filled == []


def test_dispatcher_date_op_passes_value_to_helper() -> None:
    helpers = _stub_helpers(fill_date_returns=True)
    op = {"kind": "date", "summary": "graduation date", "value_from": "graduation_date"}
    asyncio.run(run_question_ops(
        page="p", values={"graduation_date": "07/31/2026"},
        ops=[op], short=_short, **helpers
    ))
    helpers["fill_date"].assert_awaited_once_with("p", "07/31/2026")


def test_dispatcher_unknown_kind_is_skipped_silently() -> None:
    helpers = _stub_helpers()
    op = {"kind": "captcha", "summary": "?"}
    filled, skipped = asyncio.run(run_question_ops(
        page=object(), values={}, ops=[op], short=_short, **helpers
    ))
    assert filled == []
    assert skipped == []


def test_dispatcher_quadreal_yaml_round_trips_filled_messages(tmp_path) -> None:
    """End-to-end: load the shipped quadreal.yml and verify each op renders the
    historical inline message verbatim. This locks the YAML against drift."""
    import yaml as _yaml
    quadreal_path = (
        tmp_path.parents[0]  # placeholder; replaced by project path in next line
    )
    # Resolve project quadreal.yml (test runs from project root).
    from pathlib import Path as _Path
    quadreal_path = _Path("profile/workday-employers/quadreal.yml")
    if not quadreal_path.exists():
        return  # skip when user has removed the shipped config (private data)
    config = _yaml.safe_load(quadreal_path.read_text(encoding="utf-8"))
    expected_messages = {
        "Workday question: Are you a student enrolled in academic studies at a post-secondary i",
        "Workday question: What post-secondary institution are you currently attending? Please s",
        "Workday question: Please select your current program.",
        "Workday question: Please select your declared major.",
        "Workday question: Please select your current year of study.",
        "Workday question: Are you currently or have you previously been involved in any clubs o",
        "Workday question: I confirm that I have applied to no more than my top three preferred r",
        "Workday question: Do you currently hold a valid Real Estate License?",
        "Workday eligibility category",
        "Workday legal work permission → Yes",
        "Workday question field: official name of your school",
        "Workday question field: current program",
        "Workday question field: declared major",
        "Workday question field: graduation date (date input)",
        "Workday question field: GPA",
    }
    rendered = {render_filled_message(op, op["kind"], short=_short) for op in config["ops"]}
    missing = expected_messages - rendered
    extra = rendered - expected_messages
    assert not missing, f"yaml lost expected messages: {missing}"
    assert not extra, f"yaml gained unexpected messages: {extra}"
