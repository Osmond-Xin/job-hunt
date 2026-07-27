"""Runtime prompt loader — reads .md files from prompts/ and renders Jinja2 templates."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_ROOT = Path(__file__).parent.parent.parent / "prompts"

# Prompts wrap third-party job text in these markers so the model can tell data
# from instructions. A posting that contains the markers itself could close the
# fence early and have the rest read as prompt, so they are stripped from every
# untrusted value on the way in.
_FENCE_RE = re.compile(r"<<<\s*(?:JD|FORM)_TEXT_(?:BEGIN|END)\s*>>>", re.IGNORECASE)

# Values that originate outside the operator's own files. `evaluation_blocks`
# is included because those blocks are LLM output derived from the job posting:
# a hostile JD can steer an upstream node (cv_match, personalization) into
# emitting fence markers or instructions, which then arrive in the artifact
# prompt as ordinary text. Second-order injection is still injection.
_UNTRUSTED_KEYS = frozenset(
    {"jd_text", "form_text", "article_digest", "evaluation_blocks"}
)


@lru_cache(maxsize=None)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_PROMPTS_ROOT)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def strip_fence_markers(value: object) -> object:
    """Remove fence markers so untrusted text cannot escape its fence.

    Recurses through dicts and lists — `evaluation_blocks` is a dict, and only
    sanitizing top-level strings left every block inside it unfiltered.
    """
    if isinstance(value, str):
        return _FENCE_RE.sub("[removed marker]", value)
    if isinstance(value, dict):
        return {key: strip_fence_markers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_fence_markers(item) for item in value]
    return value


def render(template_path: str, **kwargs: object) -> str:
    """Render a prompt template relative to the prompts/ root."""
    safe = {
        key: strip_fence_markers(value) if key in _UNTRUSTED_KEYS else value
        for key, value in kwargs.items()
    }
    return _env().get_template(template_path).render(**safe)
