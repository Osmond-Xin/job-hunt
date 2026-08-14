"""Red-team a hand-written artifact before it is sent.

The pipeline runs this automatically (the `redteam_review` graph node); this CLI is
for artifacts written by hand, which never pass through the graph. Both share
`job_hunt/services/redteam.py`, so the rubric cannot drift between them.

    uv run python scripts/redteam.py \
        --artifact output/<run>/Yi_Xin_Resume_Foo.pdf \
        --jd jds/foo.md --company "Foo" --role "Forward Deployed Engineer"

Exit code: 0 SEND/REVISE, 1 BLOCK, 2 the reviewer could not be reached.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hunt.services.redteam import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    run_review,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifact", action="append", required=True, help="Path to a .md or .pdf artifact (repeatable)")
    ap.add_argument("--jd", help="Path to the archived job description")
    ap.add_argument("--company", default="")
    ap.add_argument("--role", default="")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--out", type=Path, help="Also write the review to this file")
    args = ap.parse_args()

    jd_text = Path(args.jd).read_text(encoding="utf-8") if args.jd else ""
    result = run_review(
        artifacts=[Path(p) for p in args.artifact],
        jd_text=jd_text,
        company=args.company,
        role=args.role,
        model=args.model,
        max_tokens=args.max_tokens,
    )

    if result.review:
        print(result.review)
    for err in result.errors:
        print(f"[{err}]", file=sys.stderr)
    if args.out and result.review:
        args.out.write_text(result.review, encoding="utf-8")
        print(f"\n[review written to {args.out}]", file=sys.stderr)

    if result.verdict == "UNREVIEWED":
        return 2
    return 1 if result.blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
