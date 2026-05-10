"""Runtime prompt loader — reads .md files from prompts/ and renders Jinja2 templates."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_ROOT = Path(__file__).parent.parent.parent / "prompts"


@lru_cache(maxsize=None)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_PROMPTS_ROOT)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render(template_path: str, **kwargs: object) -> str:
    """Render a prompt template relative to the prompts/ root."""
    return _env().get_template(template_path).render(**kwargs)
