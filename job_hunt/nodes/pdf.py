"""tailor_cv, generate_cv_html_pdf, and skip_pdf nodes."""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.nodes._cv_fit import next_trim
from job_hunt.services.prompts import render
from job_hunt.nodes._quality import generate_with_audit
from job_hunt.nodes.artifact_paths import artifact_filename, run_output_dir
from job_hunt.services.pdf import pdf_page_count

_TEMPLATES_DIR = Path("templates")
_FONTS_DIR = (_TEMPLATES_DIR / "fonts").resolve()

# A résumé that runs past two pages reads as unedited, whatever is on page three.
MAX_CV_PAGES = 2
MAX_TRIM_ATTEMPTS = 30

# CLAUDE.md §2: "Cover letter: 1 page. Enforced, not aspirational." Unlike the
# résumé's MAX_CV_PAGES, nothing trims against this — see generate_cover_letter
# in cover_letter.py for why an overflow is reported instead of auto-trimmed.
MAX_COVER_LETTER_PAGES = 1

# Ad copy wears first and second person; a job title never does.
_AD_COPY_RE = re.compile(r"\b(we|our|ours|you|your|yours)\b", re.IGNORECASE)


def banner_role(title: str) -> str:
    """The posting title for the target banner, or "" when it is not a title.

    `redteam-facts.md` rule 6 permits this banner *because* it names the one
    role being applied for. An aggregator that mis-parses a posting breaks that
    premise: on 2026-09-01 an Adzuna row arrived titled "We're looking for a
    highly engaged, self-directed developer who can turn product ideas, PRDs,
    and UX mockups into exceptional working software", and the résumé went out
    with the employer's own advertising sentence under the contact block. The
    red team read it as the candidate not bothering to rephrase the posting.

    Dropping the role leaves the employer name standing alone, which is still a
    true and useful banner. Nothing else downstream is touched — the tracker,
    the report and the run directory keep whatever the extractor produced.
    """
    title = (title or "").strip()
    if len(title) > 120 or _AD_COPY_RE.search(title):
        return ""
    return title


async def tailor_cv(state: JobHuntState, config: RunnableConfig) -> dict:
    """Rewrite the master CV into a JD-tailored resume body (pruned projects,
    no tenure self-labels), quality-audited with regeneration. Falls back to
    the master CV downstream when the LLM is unavailable or returns nothing."""
    cv = state.get("cv", "")
    if not cv:
        return {"errors": []}

    prompt = render(
        "evaluate/tailor_cv.md",
        cv=cv,
        article_digest=state.get("article_digest") or "",
        jd_meta=state.get("jd_meta"),
        jd_text=state.get("jd_text", ""),
        archetype=state.get("archetype"),
        evaluation_blocks=state.get("evaluation_blocks", {}),
        mode=state.get("mode", "full"),
    )
    audited = await generate_with_audit(
        state,
        node_name="tailor_cv",
        prompt=prompt,
        prompt_version="evaluate/tailor_cv.md:v2",
        artifact_type="tailored CV",
        temperature=0.2,
        max_tokens=2800,
        tier="premium",
    )
    if audited.status == "failed":
        # A tailored CV the auditor rejected three times is worse than no
        # tailoring: downstream renders the hand-written master CV instead,
        # which is known-good. The rejected draft is not silently shipped.
        return {
            "errors": audited.errors,
            "artifact_warnings": [f"tailored CV withheld (audit failed): {'; '.join(audited.issues)}"],
        }
    if not audited.content:
        return {"errors": audited.errors}
    warnings = (
        [f"tailored CV is UNVERIFIED (auditor unavailable)"]
        if audited.status == "unavailable"
        else []
    )
    return {
        "cv_tailored": audited.content,
        "errors": audited.errors,
        "artifact_warnings": warnings,
    }


# "## Professional Summary" section up to (not including) the next H2.
_SUMMARY_SECTION_RE = re.compile(
    r"^##\s+Professional Summary\s*\n.*?(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)


def strip_summary_section(cv_md: str) -> str:
    """Remove the master CV's Professional Summary section.

    Used on the fallback (untailored) render path when the template banner
    already carries a JD-specific summary_angle — two stacked summaries read
    as duplicated filler to a recruiter."""
    return _SUMMARY_SECTION_RE.sub("", cv_md)


def cv_markdown_to_html(cv_md: str, *, strip_contact_block: bool = True) -> str:
    """Convert CV markdown into rendered HTML for the resume template.

    With ``strip_contact_block`` (the master cv.md case), drops everything up to the
    first standalone ``---`` rule — the name + contact block — because the template's
    header already renders ``profile.name`` and contacts. Tailored CVs start at the
    summary and must be rendered whole.
    """
    if not cv_md:
        return ""
    from markdown_it import MarkdownIt

    body_md = cv_md
    if strip_contact_block:
        parts = re.split(r"^\s*-{3,}\s*$", cv_md, maxsplit=1, flags=re.MULTILINE)
        if len(parts) > 1:
            body_md = parts[1].lstrip()
    # The commonmark preset ships with the linkify core rule off, so the option
    # alone is a no-op — enable() is what makes bare URLs (project repos, the
    # portfolio site) clickable in the PDF instead of dead text.
    md = MarkdownIt(
        "commonmark", {"html": False, "linkify": True, "typographer": True}
    ).enable("linkify")
    return split_h3_dates(md.render(body_md))


# `### Role — Employer | Jan 2026 – Mar 2026`: the trailing segment is pulled out
# so the template can float it right, keeping employer and period on one line.
_H3_DATE_RE = re.compile(r"<h3>(.*?) \| ([^|<]+)</h3>", re.DOTALL)


def split_h3_dates(html: str) -> str:
    """Move a trailing ``| date`` segment of an ``<h3>`` into its own span."""
    return _H3_DATE_RE.sub(
        lambda m: f'<h3>{m.group(1)}<span class="role-date">{m.group(2)}</span></h3>',
        html,
    )


# Flagship repos a reader should be able to reach from any mention of the name,
# not just from the one URL in the Projects block.
_PROJECT_LINKS = {
    "LearnArken": "https://github.com/Osmond-Xin/LearnArken",
}
_PROJECT_NAME_RE = re.compile(r"\b(" + "|".join(map(re.escape, _PROJECT_LINKS)) + r")\b")
# Existing anchors (so a name inside one is not nested) and any other tag (so
# href/attribute text is never rewritten). Everything between is a text node.
_HTML_SKIP_RE = re.compile(r"<a\b[^>]*>.*?</a>|<[^>]+>", re.DOTALL | re.IGNORECASE)


def link_project_mentions(html: str) -> str:
    """Turn every bare project-name mention in rendered HTML into a repo link.

    Operates on text nodes only: names already inside an ``<a>`` and anything
    within a tag (attribute values, URLs) are left untouched. Matching is
    case-sensitive, so identifiers like ``LEARNARKEN_LOCAL_ONLY`` do not match.
    """
    if not html:
        return html

    def wrap(match: re.Match[str]) -> str:
        name = match.group(1)
        return f'<a href="{_PROJECT_LINKS[name]}">{name}</a>'

    out: list[str] = []
    cursor = 0
    for skip in _HTML_SKIP_RE.finditer(html):
        out.append(_PROJECT_NAME_RE.sub(wrap, html[cursor : skip.start()]))
        out.append(skip.group(0))
        cursor = skip.end()
    out.append(_PROJECT_NAME_RE.sub(wrap, html[cursor:]))
    return "".join(out)


# Country signals that map to North-American letter paper. Everything else → A4.
# Matches whole tokens (case-insensitive) so "United Kingdom" doesn't trigger on "US".
_LETTER_COUNTRY_RE = re.compile(
    r"\b(US|U\.S\.|USA|United States|Canada|CA(?:N)?|United States of America)\b",
    re.IGNORECASE,
)


def detect_paper_size(jd_meta) -> str:
    """letter for US/Canada, A4 elsewhere. jd_meta may be None."""
    if jd_meta is None:
        return "letter"
    location = getattr(jd_meta, "location", "") or ""
    if not isinstance(location, str):
        return "letter"
    return "letter" if _LETTER_COUNTRY_RE.search(location) else "A4"


def artifact_template_env():
    """Jinja environment for the CV and cover-letter templates, with the filters they require.

    autoescape is on so LLM-supplied strings (summary_angle, bullets, keywords,
    letter paragraphs) cannot break the HTML; cv_html is the only trusted-markup
    slot (``| safe``). ``project_links`` escapes first and then injects anchors,
    so it must return Markup to survive autoescaping.
    """
    from jinja2 import Environment, FileSystemLoader
    from markupsafe import Markup, escape

    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
    env.filters["project_links"] = lambda value: Markup(
        link_project_mentions(str(escape(value)))
    )
    return env


async def generate_cv_html_pdf(state: JobHuntState, config: RunnableConfig) -> dict:
    """Render the shared Jinja2 CV template with per-company state content, then PDF via Playwright."""
    try:
        scores = state.get("scores")
        profile = state.get("profile")
        cv = state.get("cv", "")
        jd_meta = state.get("jd_meta")
        pdf_content = scores.pdf_content if scores else None

        out_dir = run_output_dir(state)
        out_dir.mkdir(parents=True, exist_ok=True)

        template_path = _TEMPLATES_DIR / "cv.html.j2"
        if not template_path.exists():
            return {
                "pdf_path": None,
                "errors": ["cv.html.j2 template not found; skipping PDF generation."],
            }

        template = artifact_template_env().get_template("cv.html.j2")

        paper_size = detect_paper_size(jd_meta)
        # A cover letter is opt-in: most employers do not ask for one, so the
        # default artifact is the tailored CV alone. `--cover-letter` produces an
        # independent one-page PDF downstream; the CV never carries an embedded
        # copy, which previously meant turning the standalone letter *off* stapled
        # the letter into the résumé instead of dropping it.
        embedded_cover_letter = ""
        cv_tailored = state.get("cv_tailored", "")
        summary_angle = pdf_content.summary_angle if pdf_content else ""
        # Prefer the JD-tailored rewrite; the master cv.md is the fallback and
        # still needs its name/contact block stripped (the header renders it)
        # and its generic summary dropped when the banner carries a JD-specific
        # angle — otherwise the PDF opens with two stacked summaries.
        cv_for_render = cv_tailored or cv
        if not cv_tailored and summary_angle:
            cv_for_render = strip_summary_section(cv_for_render)

        def render_html(cv_md: str) -> str:
            return template.render(
                profile=profile,
                cv_raw=cv,  # kept for backwards compat; template now prefers cv_html
                cv_html=link_project_mentions(
                    cv_markdown_to_html(cv_md, strip_contact_block=not cv_tailored)
                ),
                company=jd_meta.company if jd_meta else "",
                role=banner_role(jd_meta.title) if jd_meta else "",
                summary_angle=pdf_content.summary_angle if pdf_content else "",
                top_bullets=pdf_content.top_bullets if pdf_content else [],
                keywords=pdf_content.keywords if pdf_content else [],
                cover_letter_body=embedded_cover_letter,
                fonts_dir=str(_FONTS_DIR),
                paper_size=paper_size,
            )

        stem = artifact_filename(state, kind="Resume", suffix="")
        html_path = out_dir / f"{stem}.html"
        pdf_path = out_dir / f"{stem}.pdf"

        # Render, measure, trim, repeat. The tailoring node prunes for relevance and
        # has no notion of page count, so without this every generated CV ran to
        # three or four pages — see docs/design-notes.md.
        pages, dropped = await _render_within_budget(
            render_html, cv_for_render, html_path, pdf_path, paper_size, MAX_CV_PAGES
        )

        warnings: list[str] = []
        if dropped:
            warnings.append(
                f"CV trimmed to fit {MAX_CV_PAGES} pages: " + "; ".join(dropped)
            )
        if pages > MAX_CV_PAGES:
            warnings.append(
                f"CV is {pages} pages after trimming everything droppable — "
                f"budget is {MAX_CV_PAGES}. Shorten profile/cv.md or hand-render."
            )

        return {"pdf_path": str(pdf_path), "errors": [], "artifact_warnings": warnings}

    except Exception as exc:
        # CLAUDE.md §1: `errors` only ever reaches the console (report.py
        # never reads it), so on its own this failure would leave no trace in
        # the document the operator actually reads before sending anything —
        # the exact "looks finished but was not reviewed" silence the rule
        # forbids, just from the other direction (no PDF at all, and nothing
        # saying why). artifact_warnings is what report.py surfaces.
        return {
            "pdf_path": None,
            "errors": [f"PDF generation failed: {exc}"],
            "artifact_warnings": [f"CV PDF was not generated (render failed): {exc}"],
        }


async def skip_pdf(state: JobHuntState, config: RunnableConfig) -> dict:
    return {"pdf_path": None, "errors": []}


async def _html_to_pdf(html_path: str, pdf_path: str, *, paper_size: str = "letter") -> None:
    from playwright.async_api import async_playwright

    # Playwright accepts "Letter" or "A4" (case-insensitive on the format param).
    fmt = "Letter" if paper_size.lower() == "letter" else "A4"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(Path(html_path).resolve().as_uri())
        await page.pdf(path=pdf_path, format=fmt, print_background=True)
        await browser.close()


async def _render_within_budget(
    render_html,
    cv_md: str,
    html_path: Path,
    pdf_path: Path,
    paper_size: str,
    max_pages: int,
) -> tuple[int, list[str]]:
    """Render, and while the PDF exceeds `max_pages`, drop one block and re-render.

    One browser is reused across attempts; a launch per attempt would dominate the
    runtime. Returns the final page count and the list of things that were dropped,
    which the caller surfaces as warnings.
    """
    from playwright.async_api import async_playwright

    fmt = "Letter" if paper_size.lower() == "letter" else "A4"
    dropped: list[str] = []

    def measure() -> int:
        try:
            pages = pdf_page_count(pdf_path.read_bytes())
        except Exception:
            # CLAUDE.md §1: page.pdf() above just wrote a complete, valid-
            # looking PDF to pdf_path. If we can't measure it, the page
            # budget was never confirmed and this render never reaches
            # redteam_review (the caller sets state["pdf_path"] = None on
            # this failure) — left on disk, the file would sit in the run
            # directory looking like a finished, reviewed résumé with
            # nothing pointing at it. Removing it closes that gap; the
            # tailored CV text this rendered from is untouched in state, so
            # a retry re-renders it with no LLM cost.
            pdf_path.unlink(missing_ok=True)
            raise
        if pages is None:
            # Same "we don't know" outcome as the except block above, just
            # signalled by pdf_page_count returning None (no /Count in the
            # bytes) instead of raising. The page budget was never confirmed
            # either way, so this must not be left looking like a finished,
            # reviewed résumé — unlink and raise so it hits the same
            # generate_cv_html_pdf except block that reports and withholds a
            # measurement failure today.
            pdf_path.unlink(missing_ok=True)
            raise ValueError(f"PDF page count could not be read from {pdf_path.name}")
        return pages

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            for _ in range(MAX_TRIM_ATTEMPTS):
                html_path.write_text(render_html(cv_md), encoding="utf-8")
                await page.goto(html_path.resolve().as_uri())
                await page.pdf(path=str(pdf_path), format=fmt, print_background=True)

                pages = measure()
                if pages <= max_pages:
                    return pages, dropped

                step = next_trim(cv_md)
                if step is None:
                    return pages, dropped
                cv_md, what = step
                dropped.append(what)
            return measure(), dropped
        finally:
            await browser.close()
