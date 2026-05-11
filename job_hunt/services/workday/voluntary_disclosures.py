"""Workday Voluntary Disclosures step (step 4 of 5) — terms-and-conditions consent only.

The Voluntary Disclosures step typically asks demographic questions
(gender, ethnicity, disability, veteran status). Per the operator safety
rules in ``docs/agent-apply.md``, the auto-filler does NOT answer
demographic questions. The only field we touch is the "terms and
conditions" consent checkbox, and only when consent has been recorded
out-of-band via either:

- ``profile/profile.yml::workday.consent_terms_and_conditions: true``
  (passed through ``values["workday_consent_terms_and_conditions"]``),
  OR
- ``storage/private/workday-consent-terms`` file present.

This split keeps the consent record auditable (a file on disk that the
operator created consciously) and disjoint from the demographic data the
agent must not touch.
"""

from __future__ import annotations

import re
from pathlib import Path


_CONSENT_SENTINEL = Path("storage/private/workday-consent-terms")
_CONSENT_LABEL_RE = re.compile(
    r"read and consent to the terms and conditions", re.IGNORECASE
)

_JS_FIND_AND_CHECK = """() => {
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const norm = text => (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const setChecked = input => {
        if (!input || input.checked) return !!input;
        input.scrollIntoView({block: 'center'});
        input.click();
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    };
    const inputs = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(visible);
    for (const input of inputs) {
        let scope = input.parentElement;
        for (let depth = 0; scope && depth < 8; depth++, scope = scope.parentElement) {
            const text = norm(scope.innerText);
            if (text.includes('read and consent to the terms and conditions')) {
                return setChecked(input);
            }
        }
    }
    const nodes = Array.from(document.querySelectorAll('label, div, span, p'))
        .filter(visible)
        .filter(el => norm(el.innerText).includes('read and consent to the terms and conditions'));
    for (const node of nodes) {
        let scope = node.parentElement;
        for (let depth = 0; scope && depth < 8; depth++, scope = scope.parentElement) {
            const input = scope.querySelector('input[type="checkbox"]');
            if (input) return setChecked(input);
        }
    }
    return false;
}"""


def consent_enabled(values: dict) -> bool:
    """True when terms consent has been recorded — either in profile or on disk."""
    return bool(values.get("workday_consent_terms_and_conditions")) or _CONSENT_SENTINEL.exists()


async def fill_voluntary_disclosures(page, values: dict) -> tuple[list[str], list[str]]:
    """Click the terms-and-conditions consent checkbox when (and only when)
    consent has been recorded by the operator.

    Returns ``(filled, skipped)`` matching the convention used by every other
    Workday step filler. Demographic fields on this step are intentionally
    left blank for the operator to handle manually.
    """
    filled: list[str] = []
    skipped: list[str] = []

    if not consent_enabled(values):
        skipped.append(
            "Workday terms consent is required but not configured. "
            "Review the legal text and, if you agree, create "
            f"{_CONSENT_SENTINEL} or set "
            "workday.consent_terms_and_conditions: true in profile/profile.yml."
        )
        return filled, skipped

    clicked = await _try_label_check(page) or await _try_js_check(page)

    if clicked:
        filled.append("Workday terms and conditions consent checkbox")
    else:
        skipped.append(
            "Workday terms consent configured, but checkbox was not found — needs manual review."
        )
    return filled, skipped


async def _try_label_check(page) -> bool:
    """Playwright accessibility-based path: ``get_by_label`` + check()."""
    try:
        checkbox = page.get_by_label(_CONSENT_LABEL_RE)
        if await checkbox.count():
            await checkbox.first.check(timeout=5000, force=True)
            return True
    except Exception:
        return False
    return False


async def _try_js_check(page) -> bool:
    """DOM-traversal fallback for sites where the label/checkbox aren't paired
    via the accessibility tree. Walks up from each visible checkbox or matching
    text node and clicks the first associated input. Returns whether anything
    was actually toggled."""
    try:
        return bool(await page.evaluate(_JS_FIND_AND_CHECK))
    except Exception:
        return False
