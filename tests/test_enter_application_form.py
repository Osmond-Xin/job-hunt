"""Tests for `_enter_application_form` — the JD-landing-page → form-tab navigator.

Covers each branch:

1. Already on `/application` / `/apply` URL → return without touching the page.
2. Workday `adventureButton` present → navigate to its href.
3. Ashby JD URL pattern → append `/application` and goto.
4. Generic fallback: click a visible "Apply for this Job" / "Application"
   tab/link/button when the URL pattern doesn't match.

Mocks Playwright's page object surface — there's no Chromium involved.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_hunt.cli import _enter_application_form


def _empty_locator() -> MagicMock:
    """Locator that reports 0 elements via count() and never resolves wait_for."""
    locator = MagicMock()
    locator.count = AsyncMock(return_value=0)
    locator.wait_for = AsyncMock(side_effect=RuntimeError("not found"))
    locator.first = locator  # locator.first is itself in our mock
    locator.click = AsyncMock()
    locator.get_attribute = AsyncMock(return_value=None)
    return locator


def _page(url: str, *, workday_apply_href: str | None = None) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.locator = MagicMock(return_value=_empty_locator())
    page.get_by_role = MagicMock(return_value=_empty_locator())
    if workday_apply_href:
        wd = MagicMock()
        wd.first = wd
        wd.count = AsyncMock(return_value=1)
        wd.wait_for = AsyncMock()
        wd.get_attribute = AsyncMock(return_value=workday_apply_href)
        page.locator = MagicMock(
            side_effect=lambda selector: wd
            if "adventureButton" in selector
            else _empty_locator()
        )
    return page


# ----- branch 1: already on form URL -----


def test_no_op_when_url_already_on_application_path() -> None:
    page = _page("https://jobs.ashbyhq.com/cohere/abc/application")
    asyncio.run(_enter_application_form(page))
    page.goto.assert_not_called()


def test_no_op_when_url_already_on_apply_path() -> None:
    page = _page("https://example.com/jobs/123/apply")
    asyncio.run(_enter_application_form(page))
    page.goto.assert_not_called()


def test_trailing_slash_on_application_path_still_no_op() -> None:
    page = _page("https://jobs.ashbyhq.com/cohere/abc/application/")
    asyncio.run(_enter_application_form(page))
    page.goto.assert_not_called()


# ----- branch 2: Workday adventureButton -----


def test_workday_adventure_button_navigates_to_href() -> None:
    page = _page(
        "https://acme.myworkdayjobs.com/external/job/123",
        workday_apply_href="https://acme.myworkdayjobs.com/external/job/123/apply",
    )
    asyncio.run(_enter_application_form(page))
    page.goto.assert_awaited_once()
    call_args = page.goto.await_args
    assert call_args.args[0].endswith("/apply")


# ----- branch 3: Ashby /application suffix -----


def test_ashby_url_navigates_to_application_subpath() -> None:
    page = _page(
        "https://jobs.ashbyhq.com/cohere/1bc73d85-e6f4-4338-b53a-9ffb609a950d"
    )
    asyncio.run(_enter_application_form(page))
    page.goto.assert_awaited_once()
    target = page.goto.await_args.args[0]
    assert target.endswith("/1bc73d85-e6f4-4338-b53a-9ffb609a950d/application")


def test_ashby_url_strips_query_string_before_appending() -> None:
    """`?utm_source=...` style trackers must not break the suffix join."""
    page = _page("https://jobs.ashbyhq.com/cohere/abc?utm_source=linkedin")
    asyncio.run(_enter_application_form(page))
    target = page.goto.await_args.args[0]
    assert target == "https://jobs.ashbyhq.com/cohere/abc/application"


def test_ashby_url_with_trailing_slash_handled() -> None:
    page = _page("https://jobs.ashbyhq.com/cohere/abc/")
    asyncio.run(_enter_application_form(page))
    target = page.goto.await_args.args[0]
    assert target == "https://jobs.ashbyhq.com/cohere/abc/application"


# ----- branch 4: generic Apply control click -----


def test_generic_apply_button_clicked_when_no_url_pattern_matches() -> None:
    """A non-Ashby, non-Workday JD page with a visible 'Apply for this Job'
    button must get clicked as a fallback."""
    page = MagicMock()
    page.url = "https://example.com/jobs/42"
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.locator = MagicMock(return_value=_empty_locator())

    # First two get_by_role calls (tab, link) return empty; the third (button)
    # returns a hit.
    empty = _empty_locator()
    apply_button = MagicMock()
    apply_button.first = apply_button
    apply_button.count = AsyncMock(return_value=1)
    apply_button.click = AsyncMock()

    role_calls = {"tab": empty, "link": empty, "button": apply_button}
    page.get_by_role = MagicMock(side_effect=lambda role, **kw: role_calls[role])

    asyncio.run(_enter_application_form(page))

    apply_button.click.assert_awaited_once()
    page.goto.assert_not_called()


def test_no_apply_control_visible_returns_without_error() -> None:
    """Page has neither URL pattern nor a recognizable Apply control — we
    should fall through without raising; the downstream fill loop will
    report zero filled fields and the operator can step in."""
    page = _page("https://example.com/jobs/42")  # not ashby, not workday
    asyncio.run(_enter_application_form(page))
    # No exception is the assertion.
