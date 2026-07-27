"""Compact page-state summaries for the fill-only session (apply-status).

Token-efficiency layer: instead of the agent reading a full-page screenshot
(or an accessibility-tree snapshot) to learn "where is the form stuck", the
running loop answers a ``status`` command with a small JSON payload built by
these helpers. The agent reads tens of lines of text instead of an image.

All DOM access is best-effort: every collector returns an empty value on any
Playwright/JS failure so a status request can never crash the fill loop.
"""

from __future__ import annotations

import re

_VALUE_MAX_CHARS = 80
_ERROR_MAX_CHARS = 200
_MAX_ERRORS = 10
_MAX_CONTROLS = 60

# Raw values of these controls never leave the browser summary: the status
# report can be pasted into an agent transcript, so login/OTP/identity/comp
# values are reduced to a filled/empty flag (red-team fix 2026-07-09).
_SENSITIVE_TYPES = {"password"}
_SENSITIVE_LABEL_RE = re.compile(
    r"password|passcode|one[- ]?time|otp|verification code|2fa|"
    r"social security|\bssn\b|social insurance|\bsin\b|passport|"
    r"date of birth|birth ?date|salary|compensation|pay (?:rate|expectation)",
    re.I,
)


def redact_control(item: dict) -> dict:
    """Mask the value of sensitive controls; pure and unit-testable."""
    if (
        item.get("type") in _SENSITIVE_TYPES
        or _SENSITIVE_LABEL_RE.search(str(item.get("label") or ""))
    ):
        return {**item, "value": "<filled>" if item.get("filled") else ""}
    return item


async def collect_form_controls(page) -> list[dict]:
    """Summarise visible form controls: label, type, value, required, filled.

    This is the generalisation of the ``_required_empty_fields`` scraper in
    ``cli.py``: same label-resolution logic, but it reports *all* visible
    controls (with truncated current values) instead of only the empty
    required ones, so the agent can decide what to fix via ``apply-do``.
    """
    try:
        controls = await page.evaluate(
            """() => {
                const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                const normalize = text => (text || '').replace(/\\s+/g, ' ').trim();
                const labelFor = el => {
                    const id = el.getAttribute('id');
                    if (id) {
                        const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                        if (label) return normalize(label.innerText);
                    }
                    const label = el.closest('label');
                    if (label) return normalize(label.innerText);
                    const aria = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name');
                    if (aria) return normalize(aria);
                    const wrap = el.closest('div');
                    if (wrap) {
                        const text = normalize(wrap.innerText).split('\\n')[0];
                        if (text) return text;
                    }
                    return el.tagName.toLowerCase();
                };
                const valueOf = el => {
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    if (type === 'file') return el.files && el.files.length ? el.files[0].name : '';
                    if (type === 'checkbox' || type === 'radio') {
                        const name = el.getAttribute('name');
                        if (!name) return el.checked ? 'checked' : '';
                        const on = document.querySelector(`input[name="${CSS.escape(name)}"]:checked`);
                        return on ? normalize(labelFor(on)) : '';
                    }
                    if (el.tagName === 'SELECT') {
                        const opt = el.selectedOptions && el.selectedOptions[0];
                        return opt ? normalize(opt.innerText) : '';
                    }
                    return String(el.value || el.innerText || el.textContent || '').trim();
                };
                const typeOf = el => {
                    if (el.tagName === 'SELECT') return 'select';
                    if (el.tagName === 'TEXTAREA') return 'textarea';
                    if (el.getAttribute('role') === 'textbox') return 'richtext';
                    if (el.getAttribute('role') === 'combobox') return 'combobox';
                    return (el.getAttribute('type') || 'text').toLowerCase();
                };
                const seenRadioGroups = new Set();
                const out = [];
                const nodes = Array.from(document.querySelectorAll(
                    'input, textarea, select, [role="textbox"][contenteditable], [role="combobox"]'
                ));
                for (const el of nodes) {
                    if (out.length >= 60) break;  // cap inside JS: bound work + transfer size
                    if (!visible(el)) continue;
                    const type = typeOf(el);
                    if (['hidden', 'submit', 'button', 'image'].includes(type)) continue;
                    if (type === 'radio') {
                        const name = el.getAttribute('name');
                        if (name) {
                            if (seenRadioGroups.has(name)) continue;
                            seenRadioGroups.add(name);
                        }
                    }
                    const label = labelFor(el).slice(0, 200);
                    const required = !!(el.required || el.getAttribute('aria-required') === 'true' || label.includes('*'));
                    const value = type === 'password' ? (el.value ? '<filled>' : '')
                        : String(valueOf(el)).slice(0, 100);
                    out.push({label, type, value, required, filled: !!value});
                }
                return out;
            }"""
        )
    except Exception:
        return []
    cleaned: list[dict] = []
    for item in controls or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "")
        if len(value) > _VALUE_MAX_CHARS:
            value = value[: _VALUE_MAX_CHARS - 1] + "…"
        cleaned.append(
            redact_control(
                {
                    "label": str(item.get("label") or "")[:200],
                    "type": str(item.get("type") or ""),
                    "value": value,
                    "required": bool(item.get("required")),
                    "filled": bool(item.get("filled")),
                }
            )
        )
        if len(cleaned) >= _MAX_CONTROLS:
            break
    return cleaned


async def collect_error_banners(page) -> list[str]:
    """Visible error/alert texts: ``role=alert`` plus Workday error containers."""
    selectors = (
        '[role="alert"]',
        '[data-automation-id="errorBanner"]',
        '[data-automation-id="pageLevelError"]',
        '[data-automation-id="errorMessage"]',
        '[data-automation-id="inlineError"]',
    )
    out: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        try:
            texts = await page.locator(selector).all_inner_texts()
        except Exception:
            continue
        for text in texts:
            norm = " ".join(str(text).split())
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(norm[:_ERROR_MAX_CHARS])
            if len(out) >= _MAX_ERRORS:
                return out
    return out


def render_status_lines(payload: dict) -> list[str]:
    """Pure formatter: turn a status response payload into compact text lines."""
    lines: list[str] = []
    lines.append(f"URL: {payload.get('url', '')}")
    title = payload.get("title") or ""
    if title:
        lines.append(f"Title: {title}")
    step = payload.get("workday_step") or ""
    if step:
        lines.append(f"Workday step: {step}")
    errors = payload.get("errors") or []
    if errors:
        lines.append(f"Errors ({len(errors)}):")
        lines.extend(f"  ! {item}" for item in errors)
    required_empty = payload.get("required_empty") or []
    if required_empty:
        lines.append(f"Required still empty ({len(required_empty)}):")
        lines.extend(f"  - {item}" for item in required_empty)
    else:
        lines.append("Required still empty: none")
    actions = payload.get("actions") or []
    if actions:
        lines.append("Visible actions: " + " | ".join(actions))
    controls = payload.get("form_controls")
    if controls:
        lines.append(f"Form controls ({len(controls)}):")
        for item in controls:
            flag = "*" if item.get("required") else " "
            state = item.get("value") or ("<empty>" if not item.get("filled") else "<filled>")
            lines.append(f"  [{flag}] {item.get('label', '')} ({item.get('type', '')}) = {state}")
    return lines
