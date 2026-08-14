"""Deterministic length trimming so a generated résumé fits its page budget.

The tailoring node prunes for *relevance*; nothing until now pruned for *length*,
which is why every pipeline-generated CV since the first one ran to three or four
pages. Word-level edits almost never drop a rendered line — only removing a whole
bullet or a whole entry does — so this module removes whole blocks, in a fixed
order that sheds the least relevant evidence first, and reports what it dropped.
"""

from __future__ import annotations

import re

_H2 = re.compile(r"^##\s+(.*)$")
_H3 = re.compile(r"^###\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+")

_PROJECT_HEADINGS = ("project",)
_EXPERIENCE_HEADINGS = ("experience", "employment")
_EARLY_HEADINGS = ("early career", "earlier roles", "earlier experience")


class _Block:
    """One `### entry` (or the preamble before the first one) inside an H2 section."""

    def __init__(self, heading: str | None, lines: list[str]):
        self.heading = heading
        self.lines = lines

    def bullet_indices(self) -> list[int]:
        return [i for i, ln in enumerate(self.lines) if _BULLET.match(ln)]


class _Section:
    def __init__(self, heading: str | None, lines: list[str]):
        self.heading = heading
        self.lines = lines

    @property
    def title(self) -> str:
        return (self.heading or "").lower()

    def is_projects(self) -> bool:
        return any(k in self.title for k in _PROJECT_HEADINGS)

    def is_experience(self) -> bool:
        return any(k in self.title for k in _EXPERIENCE_HEADINGS)

    def is_early_career(self) -> bool:
        return any(k in self.title for k in _EARLY_HEADINGS)

    def blocks(self) -> list[_Block]:
        blocks: list[_Block] = []
        current = _Block(None, [])
        for ln in self.lines:
            if _H3.match(ln):
                blocks.append(current)
                current = _Block(ln, [])
            else:
                current.lines.append(ln)
        blocks.append(current)
        return blocks

    def set_blocks(self, blocks: list[_Block]) -> None:
        lines: list[str] = []
        for b in blocks:
            if b.heading is not None:
                lines.append(b.heading)
            lines.extend(b.lines)
        self.lines = lines

    def entries(self) -> list[_Block]:
        """Blocks that are real `### entries`, ignoring any preamble."""
        return [b for b in self.blocks() if b.heading is not None]


def _split_sections(md: str) -> list[_Section]:
    sections: list[_Section] = []
    current = _Section(None, [])
    for ln in md.splitlines():
        if _H2.match(ln):
            sections.append(current)
            current = _Section(ln, [])
        else:
            current.lines.append(ln)
    sections.append(current)
    return sections


def _join(sections: list[_Section]) -> str:
    out: list[str] = []
    for s in sections:
        if s.heading is not None:
            out.append(s.heading)
        out.extend(s.lines)
    return "\n".join(out).rstrip() + "\n"


def _drop_last_entry(section: _Section, keep_at_least: int) -> str | None:
    blocks = section.blocks()
    entries = [b for b in blocks if b.heading is not None]
    if len(entries) <= keep_at_least:
        return None
    victim = entries[-1]
    name = victim.heading.lstrip("# ").strip()
    section.set_blocks([b for b in blocks if b is not victim])
    return name


def _drop_last_bullet(section: _Section, cap: int) -> str | None:
    """Drop the closing bullet of the last entry that still has more than `cap`.

    Scanning from the end means the oldest role, or the least relevant project,
    sheds first; taking that entry's *last* bullet respects the ordering the
    tailoring step already imposed, where the first bullet is the strongest.
    """
    blocks = section.blocks()
    for block in reversed(blocks):
        bullets = block.bullet_indices()
        if len(bullets) > cap:
            idx = bullets[-1]
            text = block.lines[idx].strip().lstrip("-* ")
            del block.lines[idx]
            section.set_blocks(blocks)
            return text[:70]
    return None


def next_trim(md: str) -> tuple[str, str] | None:
    """Return (shorter_markdown, human description), or None when nothing can go.

    The ladder matters more than any single rule. Project bullets in this CV are
    several rendered lines each while experience bullets are one or two, so a
    naive "drop the last bullet anywhere" order sheds a lot of employment
    evidence to reclaim very little space. Caps come down in waves instead: thin
    the fattest sections first, and only start cutting into the bone once every
    section is already lean.
    """
    sections = _split_sections(md)
    projects = [s for s in sections if s.is_projects()]
    experience = [s for s in sections if s.is_experience() and not s.is_early_career()]
    early = [s for s in sections if s.is_early_career()]

    def projects_entries(keep: int) -> tuple[str, str] | None:
        for section in projects:
            name = _drop_last_entry(section, keep_at_least=keep)
            if name:
                return _join(sections), f"dropped project “{name}”"
        return None

    def bullets(kind: list[_Section], cap: int, label: str) -> tuple[str, str] | None:
        for section in kind:
            text = _drop_last_bullet(section, cap)
            if text:
                return _join(sections), f"dropped a {label} bullet: “{text}…”"
        return None

    # 1. Projects beyond the two most relevant carry the least weight per line.
    step = projects_entries(keep=2)
    if step:
        return step

    # 2. Thin the long project write-ups before touching employment at all.
    for cap in (5, 4):
        step = bullets(projects, cap, "project")
        if step:
            return step

    # 3. The Early Career list is one block of names with no detail to lose.
    if early:
        victim = early[0]
        sections = [s for s in sections if s is not victim]
        return _join(sections), "dropped the Early Career block"

    # 4. Alternate between the two, each wave leaner than the last.
    for cap in (3, 2):
        step = bullets(projects, cap, "project")
        if step:
            return step
        step = bullets(experience, cap, "experience")
        if step:
            return step

    # 5. Bone: one bullet per entry, then down to a single project.
    step = bullets(projects, 1, "project")
    if step:
        return step
    step = bullets(experience, 1, "experience")
    if step:
        return step
    return projects_entries(keep=1)


_COUNT_RE = re.compile(rb"/Count\s+(\d+)")


def pdf_page_count(pdf_bytes: bytes) -> int | None:
    """Page count from the page-tree /Count. Counting `/Type /Page` is wrong —
    it also matches the /Pages tree node."""
    matches = _COUNT_RE.findall(pdf_bytes)
    if not matches:
        return None
    return max(int(m) for m in matches)
