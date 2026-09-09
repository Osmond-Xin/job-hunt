"""Is this page a Workday application?

Pure URL probes, mirroring what ``services/linkedin/detect.py`` already does for
LinkedIn. Before this, the question was answered by the literal
``"myworkdayjobs.com" in page.url`` written out thirteen separate times across
``cli/apply.py`` -- thirteen places to fix when a tenant turns out to be reached
by some other host, and thirteen chances to fix twelve of them.

Two levels, because a URL is a hint and a page is the truth:

- ``is_workday_url`` answers from a string, before navigation. Cheap, and wrong
  for an employer whose careers page redirects into a Workday tenant.
- ``is_workday_page`` answers from the page after navigation, which is the
  answer that counts. It is the one the step machine should ask.
"""

from __future__ import annotations

from urllib.parse import urlparse


# Workday's multi-tenant career hosts. Tenants appear as
# <tenant>.<datacenter>.myworkdayjobs.com, and a few older ones as
# <tenant>.myworkdayjobs.com, so this is a suffix test rather than a set.
_WORKDAY_HOST_SUFFIX = "myworkdayjobs.com"


def is_workday_url(url: str) -> bool:
    """True when ``url``'s host is a Workday careers host.

    Matches on the parsed hostname, not on the raw string: a URL that merely
    mentions the suffix in a query parameter (a redirect target, an analytics
    tag) is not itself a Workday page, and the substring test this replaces
    could not tell the difference.
    """
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host == _WORKDAY_HOST_SUFFIX or host.endswith("." + _WORKDAY_HOST_SUFFIX)


def is_workday_page(page) -> bool:
    """True when the page currently open is a Workday application.

    Reads ``page.url`` rather than the URL the session was asked to open, so an
    employer vanity domain that redirects into a tenant is recognised.
    """
    return is_workday_url(getattr(page, "url", "") or "")
