"""apply-do targeted-op dispatcher.

``job-hunt apply-do`` sends one precise action (click / fill / select / check,
addressed by visible label) into the running fill-only session. This is the
escape hatch that replaces driving the browser through an interactive MCP
snapshot loop when auto-fill missed a single field.

Follows the ``run_question_ops`` pattern: the actual Playwright interactions
are injected as callables so the dispatcher and CLI-argument parsing stay
unit-testable without a browser.
"""

from __future__ import annotations

from typing import Awaitable, Callable

OP_CLICK = "click"
OP_FILL = "fill"
OP_SELECT = "select"
OP_CHECK = "check"

# ops whose payload carries a value alongside the label
_VALUE_OPS = {OP_FILL, OP_SELECT}

ClickFn = Callable[..., Awaitable[bool]]   # (page, label)
FillFn = Callable[..., Awaitable[bool]]    # (page, label, value)
SelectFn = Callable[..., Awaitable[bool]]  # (page, label, value)
CheckFn = Callable[..., Awaitable[bool]]   # (page, label)


def parse_op_args(
    *,
    click: str | None,
    fill: str | None,
    select: str | None,
    check: str | None,
) -> tuple[str, str, str]:
    """Validate CLI flags and return ``(op, label, value)``.

    Exactly one flag must be provided. ``--fill`` / ``--select`` take a
    ``label=value`` argument split on the first ``=`` (labels containing ``=``
    are not supported; values may contain ``=`` freely).

    Raises ``ValueError`` with a user-facing message on invalid input.
    """
    provided = [
        (OP_CLICK, click),
        (OP_FILL, fill),
        (OP_SELECT, select),
        (OP_CHECK, check),
    ]
    chosen = [(op, arg) for op, arg in provided if arg is not None]
    if len(chosen) != 1:
        raise ValueError(
            "Provide exactly one of --click / --fill / --select / --check."
        )
    op, arg = chosen[0]
    arg = arg.strip()
    if op in _VALUE_OPS:
        label, sep, value = arg.partition("=")
        label = label.strip()
        value = value.strip()
        if not sep or not label or not value:
            raise ValueError(f"--{op} expects 'label=value' (got {arg!r}).")
        return op, label, value
    if not arg:
        raise ValueError(f"--{op} expects a non-empty label.")
    return op, arg, ""


async def execute_op(
    page,
    op: str,
    label: str,
    value: str,
    *,
    click: ClickFn,
    fill: FillFn,
    select: SelectFn,
    check: CheckFn,
) -> bool:
    """Run one op through the injected Playwright helpers; ``True`` on success."""
    if op == OP_CLICK:
        return await click(page, label)
    if op == OP_FILL:
        return await fill(page, label, value)
    if op == OP_SELECT:
        return await select(page, label, value)
    if op == OP_CHECK:
        return await check(page, label)
    return False
