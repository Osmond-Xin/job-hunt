"""Render profile/cv-yc.md to output/cv-yc/. Thin wrapper kept for the existing
entry point; all rendering logic lives in scripts/render_cv.py.

    uv run python scripts/render_cv_yc.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_cv import ROOT, render, validate  # noqa: E402


def main() -> None:
    problems = validate((ROOT / "profile" / "cv-yc.md").read_text(encoding="utf-8"), allow_legacy_layout=True)
    if problems:
        raise SystemExit("REJECTED (nothing rendered):\n  " + "\n  ".join(problems))
    render(
        source=ROOT / "profile" / "cv-yc.md",
        out_dir=ROOT / "output" / "cv-yc",
        title="Yi Xin — Resume (YC startups)",
    )


if __name__ == "__main__":
    main()
