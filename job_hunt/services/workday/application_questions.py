"""Workday Application Questions yaml-driven dispatcher (Phase 2.1 extraction).

The actual Playwright interactions (`_select_workday_dropdown_*`,
`_fill_workday_input_in_question`, `_fill_workday_date_input`) still live in
``cli.py``; this module accepts them as injected callables so its logic is fully
unit-testable without a browser. ``cli.py`` wraps the dispatcher and supplies
the live Playwright helpers.

Public API:

- :func:`render_filled_message` — produce the ``filled[]`` summary line for a
  successfully executed op (used directly by tests and by the dispatcher).
- :func:`run_question_ops` — async dispatcher that drives a list of ops and
  returns ``(filled_messages, skipped_messages)``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from job_hunt.services.workday.employer_config import choices_for_op, resolve_value


# Injected helper signatures
DropdownByLabelFn = Callable[..., Awaitable[bool]]      # (page, label, choices, *, force=False)
DropdownInQuestionFn = Callable[..., Awaitable[bool]]   # (page, label, choices)
DropdownContainingLabelFn = Callable[..., Awaitable[bool]]  # (page, label, choices)
DropdownByIndexFn = Callable[..., Awaitable[bool]]      # (page, index, choices)
TextFn = Callable[..., Awaitable[bool]]                 # (page, label, value, *, force=False)
DateFn = Callable[..., Awaitable[bool]]                 # (page, value)
ShortFn = Callable[[str, int], str]


def render_filled_message(op: dict[str, Any], kind: str, *, short: ShortFn) -> str:
    """Format the ``filled`` summary line for an op of the given kind.

    Honours ``op["filled_message"]`` when present; otherwise falls back to a
    kind-specific default that matches the historical inline messages verbatim
    (so existing tests / log greps don't break).
    """
    explicit = op.get("filled_message")
    if explicit:
        return str(explicit)
    summary = op.get("summary") or ""
    if kind == "text":
        label = op.get("label") or summary
        return f"Workday question field: {label}"
    if kind == "date":
        return f"Workday question field: {summary}"
    return f"Workday question: {short(summary, 70)}"


async def _run_dropdown_op(
    page,
    op: dict[str, Any],
    values: dict[str, str],
    *,
    by_label: DropdownByLabelFn,
    in_question: DropdownInQuestionFn,
    containing_label: DropdownContainingLabelFn,
    by_index: DropdownByIndexFn,
) -> bool:
    choices = choices_for_op(op, values)
    if not choices:
        return False
    for strat in op.get("strategies") or []:
        kind = strat.get("type")
        label = strat.get("label", "")
        if kind == "by_label":
            ok = await by_label(page, label, choices)
        elif kind == "in_question":
            ok = await in_question(page, label, choices)
        elif kind == "containing_label":
            ok = await containing_label(page, label, choices)
        elif kind == "by_index":
            idx = int(strat.get("index", 0))
            ok = await by_index(page, idx, choices)
        else:
            continue
        if ok:
            return True
    return False


async def _run_text_op(
    page, op: dict[str, Any], values: dict[str, str], *, fill_text: TextFn
) -> bool:
    label = op.get("label", "")
    value = resolve_value(op, values)
    if not label or not value:
        return False
    return await fill_text(page, label, value, force=bool(op.get("force")))


async def _run_date_op(
    page, op: dict[str, Any], values: dict[str, str], *, fill_date: DateFn
) -> bool:
    value = resolve_value(op, values)
    if not value:
        return False
    return await fill_date(page, value)


async def run_question_ops(
    page,
    values: dict[str, str],
    ops: list[dict[str, Any]],
    *,
    by_label: DropdownByLabelFn,
    in_question: DropdownInQuestionFn,
    containing_label: DropdownContainingLabelFn,
    by_index: DropdownByIndexFn,
    fill_text: TextFn,
    fill_date: DateFn,
    short: ShortFn,
) -> tuple[list[str], list[str]]:
    """Execute each op in order; return ``(filled, skipped)`` summary lists.

    All Playwright interactions are routed through the injected callables so
    this function can be unit-tested with simple ``AsyncMock`` doubles.
    """
    filled: list[str] = []
    skipped: list[str] = []
    for op in ops or []:
        kind = op.get("kind")
        if kind == "dropdown":
            ok = await _run_dropdown_op(
                page,
                op,
                values,
                by_label=by_label,
                in_question=in_question,
                containing_label=containing_label,
                by_index=by_index,
            )
        elif kind == "text":
            ok = await _run_text_op(page, op, values, fill_text=fill_text)
        elif kind == "date":
            ok = await _run_date_op(page, op, values, fill_date=fill_date)
        else:
            continue
        if ok:
            filled.append(render_filled_message(op, kind, short=short))
        elif op.get("on_skip"):
            skipped.append(str(op["on_skip"]))
    return filled, skipped
