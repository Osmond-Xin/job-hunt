"""Tests for the apply-do dispatcher and the apply-status text renderer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from job_hunt.services.web import apply_ops
from job_hunt.services.web.page_summary import render_status_lines


# --- parse_op_args -----------------------------------------------------------

def test_parse_click() -> None:
    assert apply_ops.parse_op_args(
        click="Save and Continue", fill=None, select=None, check=None
    ) == (apply_ops.OP_CLICK, "Save and Continue", "")


def test_parse_fill_splits_on_first_equals() -> None:
    op, label, value = apply_ops.parse_op_args(
        click=None, fill="Salary expectation=80000=CAD", select=None, check=None
    )
    assert (op, label, value) == (apply_ops.OP_FILL, "Salary expectation", "80000=CAD")


def test_parse_select() -> None:
    assert apply_ops.parse_op_args(
        click=None, fill=None, select="Country=Canada", check=None
    ) == (apply_ops.OP_SELECT, "Country", "Canada")


def test_parse_check_strips_whitespace() -> None:
    assert apply_ops.parse_op_args(
        click=None, fill=None, select=None, check="  I agree "
    ) == (apply_ops.OP_CHECK, "I agree", "")


def test_parse_rejects_zero_or_multiple_flags() -> None:
    with pytest.raises(ValueError):
        apply_ops.parse_op_args(click=None, fill=None, select=None, check=None)
    with pytest.raises(ValueError):
        apply_ops.parse_op_args(click="A", fill="B=c", select=None, check=None)


def test_parse_rejects_fill_without_value() -> None:
    with pytest.raises(ValueError):
        apply_ops.parse_op_args(click=None, fill="just a label", select=None, check=None)
    with pytest.raises(ValueError):
        apply_ops.parse_op_args(click=None, fill="label=", select=None, check=None)
    with pytest.raises(ValueError):
        apply_ops.parse_op_args(click=None, fill="=value", select=None, check=None)


def test_parse_rejects_empty_label() -> None:
    with pytest.raises(ValueError):
        apply_ops.parse_op_args(click="   ", fill=None, select=None, check=None)


# --- execute_op --------------------------------------------------------------

def _helpers(**overrides):
    helpers = {
        "click": AsyncMock(return_value=True),
        "fill": AsyncMock(return_value=True),
        "select": AsyncMock(return_value=True),
        "check": AsyncMock(return_value=True),
    }
    helpers.update(overrides)
    return helpers


def test_execute_routes_each_op_to_its_helper() -> None:
    page = object()
    helpers = _helpers()
    for op, label, value, target in (
        (apply_ops.OP_CLICK, "Next", "", "click"),
        (apply_ops.OP_FILL, "Email", "a@b.c", "fill"),
        (apply_ops.OP_SELECT, "Country", "Canada", "select"),
        (apply_ops.OP_CHECK, "I agree", "", "check"),
    ):
        ok = asyncio.run(apply_ops.execute_op(page, op, label, value, **helpers))
        assert ok is True
        assert helpers[target].await_count == 1


def test_execute_unknown_op_returns_false() -> None:
    helpers = _helpers()
    ok = asyncio.run(apply_ops.execute_op(object(), "submit", "x", "", **helpers))
    assert ok is False
    for mock in helpers.values():
        assert mock.await_count == 0


def test_execute_propagates_helper_failure() -> None:
    helpers = _helpers(fill=AsyncMock(return_value=False))
    ok = asyncio.run(
        apply_ops.execute_op(object(), apply_ops.OP_FILL, "Email", "x", **helpers)
    )
    assert ok is False


# --- render_status_lines -----------------------------------------------------

def test_render_status_lines_compact_payload() -> None:
    lines = render_status_lines(
        {
            "url": "https://co.wd1.myworkdayjobs.com/x",
            "title": "Apply — Acme",
            "workday_step": "My Experience",
            "errors": ["Please fix the errors below"],
            "required_empty": ["Phone Number*"],
            "actions": ["Back", "Save and Continue"],
        }
    )
    text = "\n".join(lines)
    assert "URL: https://co.wd1.myworkdayjobs.com/x" in text
    assert "Workday step: My Experience" in text
    assert "! Please fix the errors below" in text
    assert "- Phone Number*" in text
    assert "Back | Save and Continue" in text


def test_render_status_lines_clean_page_says_none() -> None:
    lines = render_status_lines({"url": "https://x", "required_empty": []})
    assert "Required still empty: none" in lines


def test_redact_control_masks_sensitive_values() -> None:
    from job_hunt.services.web.page_summary import redact_control

    masked = redact_control(
        {"label": "Password", "type": "password", "value": "hunter2", "filled": True}
    )
    assert masked["value"] == "<filled>"
    masked = redact_control(
        {"label": "Salary expectation*", "type": "text", "value": "80000", "filled": True}
    )
    assert masked["value"] == "<filled>"
    empty = redact_control(
        {"label": "Social Insurance Number", "type": "text", "value": "", "filled": False}
    )
    assert empty["value"] == ""
    normal = redact_control(
        {"label": "Email", "type": "email", "value": "a@b.c", "filled": True}
    )
    assert normal["value"] == "a@b.c"


def test_submit_label_guard_covers_common_final_buttons() -> None:
    from job_hunt.cli import _looks_like_submit_label

    for label in (
        "Submit", "Submit Application", "Apply", "Apply Now", "Easy Apply",
        "Finish", "Complete application", "Done", "Confirm", "Finalize", "Send",
    ):
        assert _looks_like_submit_label(label), label
    for label in ("Save and Continue", "Next", "Back", "Upload file", "Resend code"):
        assert not _looks_like_submit_label(label), label


def test_render_status_lines_with_controls() -> None:
    lines = render_status_lines(
        {
            "url": "https://x",
            "form_controls": [
                {"label": "Email*", "type": "text", "value": "a@b.c", "required": True, "filled": True},
                {"label": "Phone", "type": "text", "value": "", "required": False, "filled": False},
            ],
        }
    )
    text = "\n".join(lines)
    assert "[*] Email* (text) = a@b.c" in text
    assert "[ ] Phone (text) = <empty>" in text
