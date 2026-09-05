from __future__ import annotations

from pathlib import Path

import typer


def _resolve_source_type(target: str, source_type: str) -> str:
    if source_type != "auto":
        if source_type not in {"url", "jd_text", "local_file"}:
            raise typer.BadParameter("source_type must be auto, url, jd_text, or local_file")
        return source_type
    if target.startswith(("http://", "https://")):
        return "url"
    # P2-10: `local:jds/foo.md` is treated as a URL — web_extract intercepts the
    # `local:` scheme and reads the file directly.
    if target.startswith("local:"):
        return "url"
    if Path(target).exists() or (Path("jds") / target).exists():
        return "local_file"
    return "jd_text"
