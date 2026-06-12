"""extract_jd, verify_active, mark_unavailable nodes."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from langchain_core.runnables import RunnableConfig

from job_hunt.models.job import JobMeta
from job_hunt.models.state import JobHuntState
from job_hunt.services.web_extract import extract_url_text

_JDS_DIR = Path("jds")

# Minimum characters for a JD to be considered substantive.
_MIN_JD_CHARS = 200

# Signals that a page is still active (checked case-insensitively).
_ACTIVE_SIGNALS = [
    "apply now", "apply for this job", "apply for this position",
    "submit application", "apply online", "apply here",
    "job description", "responsibilities", "requirements", "qualifications",
    "what you'll do", "about the role", "about this role",
]

# Signals that a posting has closed.
_CLOSED_SIGNALS = [
    "this job is no longer available",
    "this position has been filled",
    "this posting has expired",
    "no longer accepting applications",
    "position has been closed",
]


async def extract_jd(state: JobHuntState, config: RunnableConfig) -> dict:
    source_type = state.get("source_type", "jd_text")
    errors: list[str] = []

    if source_type == "jd_text":
        raw = state.get("input", "")
    elif source_type == "local_file":
        input_path = Path(state.get("input", ""))
        path = input_path if input_path.exists() else _JDS_DIR / state.get("input", "")
        if not path.exists():
            return {"errors": [f"Local JD file not found: {path}"], "jd_text": ""}
        raw = path.read_text(encoding="utf-8")
    elif source_type == "url":
        url = state.get("url") or state.get("input", "")
        if not _looks_like_url(url):
            return {"errors": [f"Invalid URL input: {url}"], "jd_text": ""}
        try:
            extracted = await extract_url_text(url, min_chars=_MIN_JD_CHARS)
            raw = extracted.text
        except Exception as exc:
            return {"errors": [f"URL extraction failed for {url}: {exc}"], "jd_text": ""}
    else:
        return {"errors": [f"Unknown source_type: {source_type}"], "jd_text": ""}

    jd_text = _clean_text(raw)
    jd_meta = _extract_meta(jd_text, state)
    if source_type == "url" and "extracted" in locals():
        jd_meta = jd_meta.model_copy(
            update={
                "title": jd_meta.title or extracted.title,
                "company": jd_meta.company or extracted.company,
                "location": jd_meta.location or extracted.location,
                "url": extracted.url,
                "ats": extracted.ats,
            }
        )

    return {
        "jd_text": jd_text,
        "jd_meta": jd_meta,
        "errors": errors,
    }


async def verify_active(state: JobHuntState, config: RunnableConfig) -> dict:
    jd_text = state.get("jd_text", "").lower()

    if len(jd_text) < _MIN_JD_CHARS:
        return {"errors": ["JD text too short to verify; treating as inactive."],
                "jd_active": False}

    for signal in _CLOSED_SIGNALS:
        if signal in jd_text:
            return {"jd_active": False, "errors": []}

    for signal in _ACTIVE_SIGNALS:
        if signal in jd_text:
            return {"jd_active": True, "errors": []}

    # Heuristic: if substantial text present, assume active.
    return {"jd_active": len(jd_text) >= _MIN_JD_CHARS, "errors": []}


async def mark_unavailable(state: JobHuntState, config: RunnableConfig) -> dict:
    return {
        "recommendation": "skip",
        "errors": [f"Job posting appears inactive: {state.get('jd_meta', JobMeta()).title}"],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_meta(jd_text: str, state: JobHuntState) -> JobMeta:
    existing = state.get("jd_meta")
    if existing:
        return existing

    jd_hash = hashlib.sha256(jd_text.encode()).hexdigest()[:12]
    url = state.get("url") or ""
    title, company, location = _guess_title_company_location(jd_text)

    return JobMeta(
        title=title,
        company=company,
        location=location,
        raw_text=jd_text,
        jd_hash=jd_hash,
        url=url,
    )


def _guess_title_company_location(jd_text: str) -> tuple[str, str, str]:
    lines = [line.strip() for line in jd_text.splitlines() if line.strip()]
    first_line = lines[0] if lines else ""
    # Labeled fields ("**Company:** Acme") usually sit in a metadata block a few
    # lines below the heading in markdown JD files — scan the top block, with
    # markdown emphasis stripped so labels and values match cleanly.
    head_block = re.sub(r"[*_`]+", "", "\n".join(lines[:15]) or jd_text[:600])

    def _field(label: str) -> str:
        match = re.search(rf"\b{label}:\s*([^.\n]+)", head_block, flags=re.IGNORECASE)
        return match.group(1).strip() if match else ""

    location = _field("Location")
    title = _field("Title") or _field("Role")
    company = _field("Company")

    if not (title and company):
        at_match = re.search(
            r"(?P<title>[A-Z][A-Za-z0-9&/,+# .-]{2,80})\s+at\s+(?P<company>[A-Z][A-Za-z0-9&/,+# .-]{2,80})(?:[.\n]|$)",
            first_line or jd_text[:240],
        )
        if at_match:
            title = title or at_match.group("title").strip(" .")
            company = company or at_match.group("company").strip(" .")

    # Markdown H1 of the form "# Title — Company" (em dash, en dash, or hyphen).
    if not (title and company):
        h1_match = re.match(r"#\s*(.+)$", first_line)
        if h1_match:
            parts = re.split(r"\s+[—–-]\s+", h1_match.group(1).strip(), maxsplit=1)
            title = title or parts[0].strip()
            if len(parts) > 1:
                company = company or parts[1].strip()

    return title, company, location


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
