"""Workday 'My Experience' step (step 2 of 5) — diagnostic + verification helpers.

This module hosts the self-contained pieces of the My Experience step:

- ``write_debug_field_dump`` — dumps a field inventory JSON to disk when the
  filler bails out, so a follow-up debug session has structured data
  instead of just a screenshot.
- ``experience_dates_match`` — verifies that a freshly-filled Work
  Experience entry actually committed its From/To dates to the DOM (a
  Workday React state-sync issue that needs an after-the-fact check, not
  a click-and-pray assumption).

The bulky fillers (``_fill_workday_my_experience``,
``_fill_workday_structured_experience`` and friends) still live in cli.py
because they depend on many other generic Workday helpers
(``_select_workday_dropdown_*``, ``_fill_workday_field_containing``, etc.).
Extracting them is gated on those helpers also moving — see
docs/design-notes.md §A.1 for the staging plan.
"""

from __future__ import annotations

import json
from pathlib import Path


_DEBUG_DIR = Path("artifacts/apply")
_DEBUG_FILE = _DEBUG_DIR / "workday-my-experience-fields.json"


_JS_FIELD_INVENTORY = """() => {
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const short = text => (text || '').replace(/\\s+/g, ' ').trim().slice(0, 240);
    const fields = Array.from(document.querySelectorAll('input, textarea, button, [role="combobox"]'))
        .filter(visible)
        .map((el, index) => {
            const rect = el.getBoundingClientRect();
            let scope = el.parentElement;
            let scopeText = '';
            for (let depth = 0; scope && depth < 4; depth++, scope = scope.parentElement) {
                scopeText = short(scope.innerText);
                if (scopeText) break;
            }
            return {
                index,
                tag: el.tagName,
                role: el.getAttribute('role') || '',
                type: el.getAttribute('type') || '',
                aria: el.getAttribute('aria-label') || '',
                automation: el.getAttribute('data-automation-id') || '',
                placeholder: el.getAttribute('placeholder') || '',
                value: el.value || el.innerText || '',
                top: Math.round(rect.top),
                left: Math.round(rect.left),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                scopeText,
            };
        });
    return {url: location.href, title: document.title, fields};
}"""


_JS_DATES_VALUES = """(entry) => {
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const titleInput = Array.from(document.querySelectorAll('input'))
        .filter(visible)
        .find(input => (input.value || '').includes(entry.title));
    let group = null;
    if (titleInput) {
        group = titleInput.parentElement;
        for (let depth = 0; group && depth < 10; depth++, group = group.parentElement) {
            const text = group.innerText || '';
            if (text.includes('From') && text.includes('To') && text.includes('Company')) break;
        }
    }
    if (!group) {
        group = Array.from(document.querySelectorAll('div, fieldset, [role="group"]'))
            .filter(visible)
            .filter(el => {
                const text = el.innerText || '';
                return text.includes('Work Experience 1') && text.includes('From') && text.includes('To') && text.includes('Company');
            })
            .sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (ar.height * ar.width) - (br.height * br.width);
            })[0] || null;
    }
    if (!group) return [];
    return Array.from(group.querySelectorAll('input:not([type="hidden"]):not([type="file"])'))
        .filter(visible)
        .map(input => input.value || '')
        .filter(Boolean);
}"""


async def write_debug_field_dump(page) -> None:
    """Dump a structured inventory of My Experience form fields to
    ``artifacts/apply/workday-my-experience-fields.json``.

    Intended for diagnosing why a fill pass failed — captures aria labels,
    data-automation-ids, surrounding scope text, and absolute positions so a
    follow-up session has structured data, not just a screenshot.
    """
    try:
        data = await page.evaluate(_JS_FIELD_INVENTORY)
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        _DEBUG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        # Diagnostic helper — never fatal. Silently giving up is correct.
        pass


async def experience_dates_match(page, entry: dict[str, str]) -> bool:
    """Verify the From/To date pair on the freshly-filled Work Experience card.

    Workday's React state sometimes accepts a value visually but never commits
    it to the form's own state until the field is explicitly re-touched. This
    after-the-fact check compares the entry's expected dates against the
    actual ``input.value`` strings in the matched experience group.

    Returns ``True`` only when *all four* expected tokens (start month,
    start year, end month, end year) appear in the joined visible values.
    """
    try:
        values = await page.evaluate(_JS_DATES_VALUES, entry)
    except Exception:
        return False
    joined = " ".join(str(value) for value in values)
    expected = [
        str(int(entry["start_month"])),
        entry["start_year"],
        str(int(entry["end_month"])),
        entry["end_year"],
    ]
    return all(value in joined for value in expected)
