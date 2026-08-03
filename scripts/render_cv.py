"""Render any CV markdown variant to a styled HTML + PDF matching the
templates/cv.html.j2 aesthetic (Space Grotesk + DM Sans, gradient rule,
teal/purple accents).

    uv run python scripts/render_cv.py profile/cv-data-analyst.md \
        --out output/cv-data-analyst --title "Yi Xin — Data Analyst Resume"

Hand-rolled markdown converter — no external dependency beyond Playwright
(already required by the project). Supports h1/h2/h3, lists, paragraphs, and
`---` horizontal rules; that is the whole grammar the CV variants use.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = (ROOT / "templates" / "fonts").resolve()


def md_inline(text: str) -> str:
    """Convert inline markdown (bold, italic, code, links) to HTML."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def md_to_html(markdown: str) -> str:
    """Minimal markdown-to-HTML converter scoped to the CV files' structure."""
    lines = markdown.splitlines()
    out: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        stripped = raw.strip()

        if not stripped:
            close_list()
            continue

        if stripped == "---":
            close_list()
            out.append('<hr class="rule">')
            continue

        for level, prefix in ((1, "# "), (2, "## "), (3, "### ")):
            if stripped.startswith(prefix):
                close_list()
                out.append(f"<h{level}>{md_inline(stripped[len(prefix):])}</h{level}>")
                break
        else:
            if stripped.startswith("- "):
                if not in_list:
                    out.append("<ul>")
                    in_list = True
                out.append(f"<li>{md_inline(stripped[2:])}</li>")
            else:
                close_list()
                out.append(f"<p>{md_inline(stripped)}</p>")

    close_list()
    return "\n".join(out)


def build_html(body_html: str, title: str) -> str:
    fonts_uri = f"file://{FONTS_DIR}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  @font-face {{
    font-family: 'Space Grotesk';
    src: url('{fonts_uri}/space-grotesk-latin.woff2') format('woff2');
    font-weight: 300 700;
    font-style: normal;
    font-display: swap;
  }}
  @font-face {{
    font-family: 'Space Grotesk';
    src: url('{fonts_uri}/space-grotesk-latin-ext.woff2') format('woff2');
    font-weight: 300 700;
    font-style: normal;
    font-display: swap;
  }}
  @font-face {{
    font-family: 'DM Sans';
    src: url('{fonts_uri}/dm-sans-latin.woff2') format('woff2');
    font-weight: 100 1000;
    font-style: normal;
    font-display: swap;
  }}
  @font-face {{
    font-family: 'DM Sans';
    src: url('{fonts_uri}/dm-sans-latin-ext.woff2') format('woff2');
    font-weight: 100 1000;
    font-style: normal;
    font-display: swap;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{
    font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 10.5px;
    line-height: 1.5;
    color: #1a1a2e;
    background: #ffffff;
  }}
  @page {{ size: letter; margin: 0.55in; }}

  h1 {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #1a1a2e;
    margin-bottom: 4px;
  }}
  h1 + p {{
    font-size: 10px;
    color: #555;
    margin-bottom: 4px;
  }}
  h2 {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: hsl(187, 74%, 32%);
    border-bottom: 1px solid #e5e5e5;
    padding-bottom: 3px;
    margin-top: 14px;
    margin-bottom: 8px;
  }}
  h3 {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 11px;
    font-weight: 600;
    color: #1a1a2e;
    margin-top: 10px;
    margin-bottom: 2px;
  }}
  hr.rule {{
    height: 2px;
    background: linear-gradient(to right, hsl(187, 74%, 32%), hsl(270, 70%, 45%));
    border: none;
    border-radius: 1px;
    margin: 4px 0 8px;
  }}
  p {{ margin-bottom: 6px; font-size: 10.5px; line-height: 1.55; }}
  ul {{ padding-left: 18px; margin-top: 2px; margin-bottom: 8px; }}
  li {{
    font-size: 10.5px;
    line-height: 1.5;
    color: #333;
    margin-bottom: 3px;
    break-inside: avoid;
  }}
  a {{
    color: hsl(187, 74%, 32%);
    text-decoration: none;
    white-space: nowrap;
  }}
  strong {{ color: #1a1a2e; }}
  em {{ color: #555; font-style: italic; }}
  code {{
    font-family: 'JetBrains Mono', Consolas, Menlo, monospace;
    font-size: 9.5px;
    background: hsl(187, 40%, 95%);
    color: hsl(187, 74%, 28%);
    padding: 1px 4px;
    border-radius: 3px;
  }}
</style>
</head>
<body>
{body_html}
</body>
</html>
"""


async def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(html_path.resolve().as_uri())
        await page.pdf(path=str(pdf_path), format="Letter", print_background=True)
        await browser.close()


def render(source: Path, out_dir: Path, title: str, pdf_name: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    html_doc = build_html(md_to_html(source.read_text(encoding="utf-8")), title)
    html_path = out_dir / f"{source.stem}.html"
    html_path.write_text(html_doc, encoding="utf-8")
    pdf_path = out_dir / (pdf_name or f"{source.stem}.pdf")
    asyncio.run(html_to_pdf(html_path, pdf_path))
    print(f"HTML: {html_path}")
    print(f"PDF:  {pdf_path}")
    return pdf_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="CV markdown file, e.g. profile/cv-data-analyst.md")
    ap.add_argument("--out", type=Path, help="Output directory (default: output/<source stem>)")
    ap.add_argument("--title", default=None, help="<title> for the document / PDF metadata")
    ap.add_argument("--pdf-name", default=None, help="PDF filename (default: <source stem>.pdf)")
    args = ap.parse_args()

    source = args.source if args.source.is_absolute() else ROOT / args.source
    if not source.exists():
        raise SystemExit(f"Source not found: {source}")
    out_dir = args.out or (ROOT / "output" / source.stem)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    render(source, out_dir, args.title or f"Yi Xin — {source.stem}", args.pdf_name)


if __name__ == "__main__":
    main()
