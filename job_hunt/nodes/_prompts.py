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
    # A node that hands us `jd_meta=None` — which is what every node does when
    # extraction failed and no metadata was ever written to the state — used to
    # blow up inside the template on `{{ jd_meta.company }}`, because
    # StrictUndefined turns the attribute access into an UndefinedError. That
    # surfaced as a crashed run several nodes deep, after the money was spent,
    # instead of as the extraction failure it actually was. Templates read
    # metadata as optional, so give them an empty object to read.
    if "jd_meta" in safe and safe["jd_meta"] is None:
        from job_hunt.models.job import JobMeta

        safe["jd_meta"] = JobMeta()
    return _env().get_template(template_path).render(**safe)
