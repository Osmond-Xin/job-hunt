"""Render a hand-written cover letter markdown through the project's own
templates/cover-letter.html.j2, so it matches the pipeline's house style.

    uv run python scripts/render_letter.py \
        output/2026-08-02-ns-product-director-early-years/cover-letter.md \
        --company "Cyber Security & Digital Solutions, Government of Nova Scotia" \
        --role "Product Director – Early Years Modernization (Job ID 604816617)" \
        --date "August 2, 2026" --pdf-name Yi_Xin_Cover_Letter.pdf

Source format: an optional `# heading` / metadata block, a `---` rule, a
greeting line ("Dear ..."), the body paragraphs separated by blank lines, then
"Sincerely," and the signature. Everything between greeting and closing becomes
one <p> per blank-line-separated block.

The shared template is sized for pipeline-generated letters, whose length
varies; --body-size scales a hand-written letter up to fill a single page
without touching the template. 12.0 fills US Letter at ~98% for a five- or
six-paragraph letter — check the page count after rendering.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hunt.nodes.pdf import _html_to_pdf, artifact_template_env  # noqa: E402

SIZE_OVERRIDE = """
<style>
  body {{ font-size: {body}px; line-height: {lh}; }}
  h1 {{ font-size: {h1}px; }}
  .body p {{ font-size: {body}px; line-height: {lh}; margin: 0 0 {pgap}px; }}
  .meta, .date, .recipient, .salutation, .closing, .signature {{ font-size: {body}px; }}
  .date, .recipient {{ margin-bottom: {gap}px; }}
  .salutation {{ margin-bottom: {pgap}px; }}
  .closing {{ margin-top: {gap}px; }}
</style>
"""

PROFILE = {
    "name": "Yi Xin",
    "email": "jonzy.xin@outlook.com",
    "phone": "249-874-5096",
    "location": "Niagara Falls, ON, Canada",
    "website": "https://www.niagaradataanalyst.com",
    "linkedin": "https://www.linkedin.com/in/osmond-xin-92a736308",
}

_GREETING_RE = re.compile(r"^(Dear\b.*)$", re.MULTILINE)
_CLOSING_RE = re.compile(r"^(Sincerely|Regards|Best regards|Yours sincerely)\b", re.MULTILINE)


def parse_letter(text: str) -> tuple[str, list[str]]:
    """Return (greeting, body paragraphs) from the markdown source."""
    greeting_match = _GREETING_RE.search(text)
    if not greeting_match:
        raise SystemExit("No greeting found — the letter must contain a line starting with 'Dear'.")
    greeting = greeting_match.group(1).strip()

    rest = text[greeting_match.end():]
    closing_match = _CLOSING_RE.search(rest)
    body = rest[: closing_match.start()] if closing_match else rest

    paragraphs = []
    for block in body.strip().split("\n\n"):
        block = re.sub(r"\s+", " ", block).strip()
        if block and block != "---":
            paragraphs.append(block)
    if not paragraphs:
        raise SystemExit("No body paragraphs found between the greeting and the closing.")
    return greeting, paragraphs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="Cover letter markdown file")
    ap.add_argument("--company", required=True, help="Recipient organisation")
    ap.add_argument("--role", default="", help="Re: line — role title and competition/job ID")
    ap.add_argument("--date", required=True, help='Letter date, e.g. "August 2, 2026"')
    ap.add_argument("--out", type=Path, help="Output directory (default: alongside the source)")
    ap.add_argument("--pdf-name", default=None, help="PDF filename (default: <source stem>.pdf)")
    ap.add_argument("--closing", default="Sincerely,")
    ap.add_argument("--body-size", type=float, default=12.0, help="Body font size in px")
    args = ap.parse_args()

    source = args.source if args.source.is_absolute() else ROOT / args.source
    if not source.exists():
        raise SystemExit(f"Source not found: {source}")
    out_dir = args.out or source.parent
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    greeting, paragraphs = parse_letter(source.read_text(encoding="utf-8"))

    html = artifact_template_env().get_template("cover-letter.html.j2").render(
        profile=PROFILE,
        company=args.company,
        role=args.role,
        date=args.date,
        greeting=greeting,
        closing=args.closing,
        paragraphs=paragraphs,
        paper_size="letter",
        fonts_dir=str((ROOT / "templates" / "fonts").resolve()),
    )
    html = html.replace(
        "</head>",
        SIZE_OVERRIDE.format(body=args.body_size, lh=1.6, h1=26, gap=14, pgap=10) + "</head>",
    )

    html_path = out_dir / f"{source.stem}.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = out_dir / (args.pdf_name or f"{source.stem}.pdf")
    asyncio.run(_html_to_pdf(str(html_path), str(pdf_path), paper_size="letter"))
    print(f"PDF: {pdf_path}  ({len(paragraphs)} paragraphs @ {args.body_size}px)")


if __name__ == "__main__":
    main()
