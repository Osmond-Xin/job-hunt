"""Which driver owns this page.

Kept apart from ``ats_contract`` because a module that both defines the protocol
and imports the drivers implementing it is a cycle. The direction is one way:
contract ← drivers ← registry ← session.

Adding a third ATS is a module and one entry in ``DRIVERS``. Neither
``cli/apply.py`` nor the session's body changes, which is the behavioural claim
docs/apply-seam-plan.md §2 makes and `tests/test_ats_registry.py` checks with a
driver that exists only in the test.
"""

from __future__ import annotations

from job_hunt.services.linkedin.driver import LinkedInDriver
from job_hunt.services.web.ats_contract import AtsDriver
from job_hunt.services.workday.driver import WorkdayDriver


# Order matters only for a page two drivers both claim, which no pair here
# does. LinkedIn is first because its check is the cheaper one.
DRIVERS: list[AtsDriver] = [LinkedInDriver(), WorkdayDriver()]


async def driver_for(page, url: str = "") -> AtsDriver | None:
    """The driver for the page as it now stands, or None.

    The page is asked, not the URL that was requested: a careers domain that
    redirects into an ATS tenant is that ATS, and a URL check made before
    navigation cannot see it. ``url`` is accepted so a caller with no page yet
    can still get a hint, and is ignored once a page exists.
    """
    for driver in DRIVERS:
        if page is not None and await driver.matches_page(page):
            return driver
    if page is None and url:
        for driver in DRIVERS:
            if driver.matches_url(url):
                return driver
    return None
