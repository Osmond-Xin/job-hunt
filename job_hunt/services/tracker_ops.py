"""Tracker batch operations: TSV staging merge, dedup, normalize, verify.

``data/tracker-additions/*.tsv`` files can be merged into
``data/applications.md`` deterministically, and the same checks run via
``job-hunt tracker``.

TSV row layout:

    {num}\\t{date}\\t{company}\\t{role}\\t{status}\\t{score}\\t{pdf}\\t{report}\\t{notes}

The applications.md row layout already used by the project differs in column
order — score before status — so both layouts are translated explicitly when
parsing/serializing.
"""

from __future__ import annotations

import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from typing import Iterator

import yaml
from filelock import FileLock, Timeout

from job_hunt.models.tracker import normalize as normalize_id
from job_hunt.repositories.tracker_repo import (
    TrackerEntry,
    format_tracker_entry,
    parse_tracker_line,
)


_LOCK_TIMEOUT = 30


@contextmanager
def _apps_lock(apps_path: Path) -> Iterator[None]:
    """Hold the same filelock TrackerRepository uses, so concurrent writes from
    email reconcile / write_tracker_addition / tracker_ops can't interleave.
    """
    apps_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(apps_path) + ".lock", timeout=_LOCK_TIMEOUT)
    try:
        with lock:
            yield
    except Timeout:
        raise RuntimeError(
            f"Could not acquire applications.md lock within {_LOCK_TIMEOUT}s"
        ) from None


_DEFAULT_STATES_YML = Path("templates/states.yml")
_DEFAULT_ADDITIONS_DIR = Path("data/tracker-additions")
_DEFAULT_APPS_MD = Path("data/applications.md")

_FALLBACK_CANONICAL = [
    "Evaluated",
    "Applied",
    "Responded",
    "Interview",
    "Offer",
    "Rejected",
    "Discarded",
    "SKIP",
]
_FALLBACK_ALIASES: dict[str, str] = {
    "evaluada": "Evaluated",
    "evaluar": "Evaluated",
    "verificar": "Evaluated",
    "condicional": "Evaluated",
    "hold": "Evaluated",
    "monitor": "Evaluated",
    "aplicado": "Applied",
    "aplicada": "Applied",
    "enviada": "Applied",
    "sent": "Applied",
    "respondido": "Responded",
    "entrevista": "Interview",
    "oferta": "Offer",
    "rechazado": "Rejected",
    "rechazada": "Rejected",
    "descartado": "Discarded",
    "descartada": "Discarded",
    "cerrada": "Discarded",
    "cancelada": "Discarded",
    "no_aplicar": "SKIP",
    "no aplicar": "SKIP",
    "skip": "SKIP",
}

# Pipeline rank for status promotion during dedup. Active applications outrank
# terminal states so a duplicate that has progressed to "Interview" wins over a
# stale "Rejected" sibling.
_STATUS_RANK = {
    "SKIP": 0,
    "Discarded": 0,
    "Rejected": 1,
    "Evaluated": 2,
    "Applied": 3,
    "Responded": 4,
    "Interview": 5,
    "Offer": 6,
}


@dataclass
class StatesYaml:
    canonical: list[str]
    aliases: dict[str, str]

    def normalize(self, raw: str) -> str | None:
        cleaned = raw.replace("**", "").strip()
        cleaned = re.sub(r"\s+\d{4}-\d{2}-\d{2}.*$", "", cleaned).strip()
        if not cleaned or cleaned in {"—", "-"}:
            return None
        lower = cleaned.lower()
        for label in self.canonical:
            if label.lower() == lower:
                return label
        if lower in self.aliases:
            return self.aliases[lower]
        if re.match(r"^(duplicado|dup|repost)\b", lower):
            return "Discarded"
        if re.search(r"geo.?blocker", lower):
            return "SKIP"
        return None


def load_states(path: Path | None = None) -> StatesYaml:
    """Load canonical states + aliases from ``templates/states.yml``."""
    states_path = path or _DEFAULT_STATES_YML
    if not states_path.exists():
        return StatesYaml(canonical=list(_FALLBACK_CANONICAL), aliases=dict(_FALLBACK_ALIASES))
    raw = yaml.safe_load(states_path.read_text(encoding="utf-8")) or {}
    canonical: list[str] = []
    aliases: dict[str, str] = {}
    for entry in raw.get("states", []):
        label = entry.get("label")
        if not label:
            continue
        canonical.append(label)
        for alias in entry.get("aliases", []) or []:
            aliases[str(alias).lower()] = label
    if not canonical:
        return StatesYaml(canonical=list(_FALLBACK_CANONICAL), aliases=dict(_FALLBACK_ALIASES))
    return StatesYaml(canonical=canonical, aliases=aliases)


@dataclass
class StagedAddition:
    number: int
    date: str
    company: str
    role: str
    status: str
    score: str
    pdf: str
    report: str
    notes: str
    source_file: Path

    def to_tracker_entry(self, *, number: int | None = None) -> TrackerEntry:
        return TrackerEntry(
            number=number if number is not None else self.number,
            date=self.date,
            company=self.company,
            role=self.role,
            score=self.score,
            status=self.status,
            pdf=self.pdf,
            report=self.report,
            notes=self.notes,
        )


@dataclass
class MergeAction:
    kind: str  # "added" | "updated" | "skipped"
    number: int
    company: str
    role: str
    detail: str = ""


@dataclass
class MergeResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    actions: list[MergeAction] = field(default_factory=list)
    moved: list[Path] = field(default_factory=list)


@dataclass
class DedupResult:
    removed: int = 0
    promoted: list[tuple[int, str, str]] = field(default_factory=list)  # (kept_num, old_status, new_status)
    removed_entries: list[TrackerEntry] = field(default_factory=list)


@dataclass
class NormalizeResult:
    changes: int = 0
    unknowns: list[tuple[int, str]] = field(default_factory=list)
    changed_entries: list[tuple[int, str, str]] = field(default_factory=list)


@dataclass
class VerifyResult:
    entries: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    by_status: dict[str, int] = field(default_factory=dict)


# ----- TSV staging helpers -----


def stage_addition(
    entry: TrackerEntry,
    additions_dir: Path | None = None,
) -> Path:
    """Write a 9-col TSV file representing a pending tracker addition."""
    target_dir = additions_dir or _DEFAULT_ADDITIONS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", entry.company.lower()).strip("-") or "entry"
    role_slug = re.sub(r"[^a-z0-9]+", "-", entry.role.lower()).strip("-") or "role"
    filename = f"{entry.number:05d}-{slug}-{role_slug}.tsv"
    path = target_dir / filename
    row = "\t".join(
        [
            str(entry.number),
            entry.date,
            entry.company,
            entry.role,
            entry.status,
            entry.score,
            entry.pdf,
            entry.report,
            entry.notes,
        ]
    )
    path.write_text(row + "\n", encoding="utf-8")
    return path


def parse_tsv_addition(path: Path, *, states: StatesYaml | None = None) -> StagedAddition | None:
    """Parse a TSV file. Tolerates 8 or 9 columns and detects swapped status/score."""
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    line = raw.splitlines()[0]
    cols = line.split("\t")
    if len(cols) < 8:
        return None
    states = states or load_states()
    try:
        number = int(cols[0].strip())
    except ValueError:
        return None
    if number == 0:
        return None
    date = cols[1].strip()
    company = cols[2].strip()
    role = cols[3].strip()

    col4 = cols[4].strip()
    col5 = cols[5].strip()
    col4_score = bool(re.match(r"^\d+(\.\d+)?/5$", col4)) or col4 in {"N/A", "DUP"}
    col5_score = bool(re.match(r"^\d+(\.\d+)?/5$", col5)) or col5 in {"N/A", "DUP"}
    col4_status = states.normalize(col4) is not None
    col5_status = states.normalize(col5) is not None

    if col4_status and not col4_score:
        status_col, score_col = col4, col5
    elif col4_score and col5_status:
        status_col, score_col = col5, col4
    elif col5_score and not col4_score:
        status_col, score_col = col4, col5
    else:
        status_col, score_col = col4, col5

    status_norm = states.normalize(status_col) or "Evaluated"
    pdf = cols[6].strip()
    report = cols[7].strip()
    notes = cols[8].strip() if len(cols) >= 9 else ""

    return StagedAddition(
        number=number,
        date=date,
        company=company,
        role=role,
        status=status_norm,
        score=score_col,
        pdf=pdf,
        report=report,
        notes=notes,
        source_file=path,
    )


# ----- merge -----


def _load_applications_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _parse_applications_lines(lines: list[str]) -> list[tuple[int, TrackerEntry, str]]:
    """Return (line_index, entry, raw_line) for every parsed row."""
    out: list[tuple[int, TrackerEntry, str]] = []
    for idx, line in enumerate(lines):
        entry = parse_tracker_line(line)
        if entry is not None:
            out.append((idx, entry, line))
    return out


def _extract_report_num(report: str) -> int | None:
    m = re.search(r"\[(\d+)\]", report)
    return int(m.group(1)) if m else None


def _parse_score(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", text.replace("**", ""))
    return float(m.group(1)) if m else 0.0


def _role_overlap(a: str, b: str, threshold: int = 2) -> bool:
    words_a = [w for w in re.split(r"\s+", a.lower()) if len(w) > 3]
    words_b = [w for w in re.split(r"\s+", b.lower()) if len(w) > 3]
    overlap = sum(1 for w in words_a if any(w in wb or wb in w for wb in words_b))
    return overlap >= threshold


def _find_duplicate(
    addition: StagedAddition,
    existing: list[tuple[int, TrackerEntry, str]],
) -> tuple[int, TrackerEntry, str] | None:
    add_company = normalize_id(addition.company)
    add_report_num = _extract_report_num(addition.report)
    if add_report_num is not None:
        for row in existing:
            if _extract_report_num(row[1].report) == add_report_num:
                return row
    # Entry-number match requires same normalized company, otherwise
    # `num=5 / Acme` would dedup against an unrelated `#5 / Beta` row.
    for row in existing:
        if row[1].number == addition.number and normalize_id(row[1].company) == add_company:
            return row
    for row in existing:
        if normalize_id(row[1].company) != add_company:
            continue
        if _role_overlap(addition.role, row[1].role):
            return row
    return None


def _find_header_insert_index(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.startswith("|") and "---" in line:
            return i + 1
    return len(lines)


def merge(
    additions_dir: Path | None = None,
    applications_md: Path | None = None,
    *,
    dry_run: bool = False,
    states: StatesYaml | None = None,
) -> MergeResult:
    """Merge pending TSVs into applications.md.

    Each TSV is parsed, dedup'd against existing rows by report-number / entry
    number / fuzzy company+role match, then either appended or used to update
    the higher-scoring sibling. Processed TSVs are moved to ``merged/`` unless
    ``dry_run`` is set.
    """
    additions_dir = additions_dir or _DEFAULT_ADDITIONS_DIR
    apps_path = applications_md or _DEFAULT_APPS_MD
    states = states or load_states()
    result = MergeResult()

    if not additions_dir.exists():
        return result
    tsv_files = sorted(
        (p for p in additions_dir.iterdir() if p.is_file() and p.suffix == ".tsv"),
        key=lambda p: (
            int(re.match(r"\d+", p.name).group(0)) if re.match(r"\d+", p.name) else 0,
            p.name,
        ),
    )
    if not tsv_files:
        return result

    # Hold the lock for the whole read-modify-write so concurrent writers
    # (email reconcile / write_tracker_addition) cannot interleave.
    lock_ctx = _noop_ctx() if dry_run else _apps_lock(apps_path)
    with lock_ctx:
        lines = _load_applications_lines(apps_path)
        parsed = _parse_applications_lines(lines)
        max_num = max((row[1].number for row in parsed), default=0)
        new_rows: list[str] = []

        for tsv in tsv_files:
            addition = parse_tsv_addition(tsv, states=states)
            if addition is None:
                result.skipped += 1
                result.actions.append(
                    MergeAction(
                        kind="skipped", number=0, company="", role="",
                        detail=f"unparseable {tsv.name}",
                    )
                )
                continue

            duplicate = _find_duplicate(addition, parsed)
            if duplicate is not None:
                parsed = _apply_duplicate(addition, duplicate, lines, parsed, result)
                continue

            entry_num, max_num = _next_entry_num(addition.number, max_num)
            new_entry = addition.to_tracker_entry(number=entry_num)
            new_rows.append(format_tracker_entry(new_entry))
            parsed.append((-1, new_entry, ""))  # block re-add within this run
            result.added += 1
            result.actions.append(
                MergeAction(
                    kind="added", number=entry_num,
                    company=addition.company, role=addition.role,
                    detail=addition.score,
                )
            )

        if new_rows:
            if not lines or not any(line.startswith("|") for line in lines):
                from job_hunt.repositories.tracker_repo import TRACKER_HEADER

                lines = TRACKER_HEADER.rstrip("\n").splitlines()
            insert_at = _find_header_insert_index(lines)
            lines = lines[:insert_at] + new_rows + lines[insert_at:]

        if dry_run:
            return result

        apps_path.write_text(
            "\n".join(lines) + ("\n" if lines and not lines[-1].endswith("\n") else ""),
            encoding="utf-8",
        )

        merged_dir = additions_dir / "merged" / date_cls.today().isoformat()
        merged_dir.mkdir(parents=True, exist_ok=True)
        for tsv in tsv_files:
            target = merged_dir / tsv.name
            shutil.move(str(tsv), str(target))
            result.moved.append(target)
    return result


@contextmanager
def _noop_ctx() -> Iterator[None]:
    yield


def _next_entry_num(addition_num: int, max_num: int) -> tuple[int, int]:
    if addition_num > max_num:
        return addition_num, addition_num
    return max_num + 1, max_num + 1


def _apply_duplicate(
    addition: StagedAddition,
    duplicate: tuple[int, TrackerEntry, str],
    lines: list[str],
    parsed: list[tuple[int, TrackerEntry, str]],
    result: MergeResult,
) -> list[tuple[int, TrackerEntry, str]]:
    """Mutate `lines` in place when score wins; record action either way.
    Returns the (possibly-updated) parsed snapshot.
    """
    line_idx, dup_entry, _ = duplicate
    new_score = _parse_score(addition.score)
    old_score = _parse_score(dup_entry.score)
    if new_score <= old_score:
        result.skipped += 1
        result.actions.append(
            MergeAction(
                kind="skipped", number=dup_entry.number,
                company=addition.company, role=addition.role,
                detail=f"existing #{dup_entry.number} score {old_score} >= {new_score}",
            )
        )
        return parsed

    merged_notes = (
        f"Re-eval {addition.date} ({old_score}→{new_score})."
        + (f" {addition.notes}" if addition.notes else "")
    ).strip()
    updated = dup_entry.model_copy(update={
        "date": addition.date,
        "company": addition.company,
        "role": addition.role,
        "score": addition.score,
        "report": addition.report or dup_entry.report,
        "notes": merged_notes,
    })
    lines[line_idx] = format_tracker_entry(updated)
    # mutate the parsed snapshot in place — O(n) per update across the run
    for k, (i, _e, _raw) in enumerate(parsed):
        if i == line_idx:
            parsed[k] = (line_idx, updated, lines[line_idx])
            break
    result.updated += 1
    result.actions.append(
        MergeAction(
            kind="updated", number=dup_entry.number,
            company=addition.company, role=addition.role,
            detail=f"score {old_score}->{new_score}",
        )
    )
    return parsed


# ----- dedup -----


def dedup(
    applications_md: Path | None = None,
    *,
    dry_run: bool = False,
) -> DedupResult:
    """Drop duplicate (company, role) entries; keep highest-score row.

    If a duplicate further along the pipeline has a more advanced status
    (e.g. Interview) the kept row is promoted to that status.
    """
    apps_path = applications_md or _DEFAULT_APPS_MD
    result = DedupResult()
    if not apps_path.exists():
        return result

    lock_ctx = _noop_ctx() if dry_run else _apps_lock(apps_path)
    with lock_ctx:
        lines = apps_path.read_text(encoding="utf-8").splitlines()
        parsed = _parse_applications_lines(lines)
        if len(parsed) < 2:
            return result

        groups: dict[str, list[tuple[int, TrackerEntry]]] = {}
        for line_idx, entry, _ in parsed:
            key = normalize_id(entry.company)
            groups.setdefault(key, []).append((line_idx, entry))

        drop_indices: set[int] = set()
        for company_entries in groups.values():
            if len(company_entries) < 2:
                continue
            processed: set[int] = set()
            for i, (idx_i, entry_i) in enumerate(company_entries):
                if i in processed:
                    continue
                cluster = [(idx_i, entry_i)]
                processed.add(i)
                for j in range(i + 1, len(company_entries)):
                    if j in processed:
                        continue
                    idx_j, entry_j = company_entries[j]
                    if _role_overlap(entry_i.role, entry_j.role):
                        cluster.append((idx_j, entry_j))
                        processed.add(j)
                if len(cluster) < 2:
                    continue
                cluster.sort(key=lambda t: _parse_score(t[1].score), reverse=True)
                keep_idx, keep_entry = cluster[0]
                best_status = keep_entry.status
                best_rank = _STATUS_RANK.get(keep_entry.status, 0)
                for idx, entry in cluster[1:]:
                    rank = _STATUS_RANK.get(entry.status, 0)
                    if rank > best_rank:
                        best_rank = rank
                        best_status = entry.status
                if best_status != keep_entry.status:
                    promoted = keep_entry.model_copy(update={"status": best_status})
                    lines[keep_idx] = format_tracker_entry(promoted)
                    result.promoted.append((keep_entry.number, keep_entry.status, best_status))
                for idx, entry in cluster[1:]:
                    drop_indices.add(idx)
                    result.removed_entries.append(entry)
                    result.removed += 1

        if not drop_indices or dry_run:
            return result

        # No .bak: git is the backup. Concurrent safety: holding the lock.
        new_lines = [line for i, line in enumerate(lines) if i not in drop_indices]
        apps_path.write_text("\n".join(new_lines), encoding="utf-8")
    return result


# ----- normalize -----


def normalize_statuses(
    applications_md: Path | None = None,
    *,
    dry_run: bool = False,
    states: StatesYaml | None = None,
) -> NormalizeResult:
    """Rewrite status field of every row to the canonical label per states.yml.

    Strips markdown bold and trailing dates from status/score columns. Unknown
    statuses are left untouched and reported via ``result.unknowns``.
    """
    apps_path = applications_md or _DEFAULT_APPS_MD
    states = states or load_states()
    result = NormalizeResult()
    if not apps_path.exists():
        return result

    lock_ctx = _noop_ctx() if dry_run else _apps_lock(apps_path)
    with lock_ctx:
        lines = apps_path.read_text(encoding="utf-8").splitlines()
        changed_anything = False

        for idx, line in enumerate(lines):
            entry = parse_tracker_line(line)
            if entry is None:
                continue
            canonical_status = states.normalize(entry.status)
            score_clean = entry.score.replace("**", "").strip()
            if canonical_status is None:
                result.unknowns.append((entry.number, entry.status))
                if score_clean != entry.score:
                    lines[idx] = format_tracker_entry(
                        entry.model_copy(update={"score": score_clean})
                    )
                    changed_anything = True
                continue
            if canonical_status == entry.status and score_clean == entry.score:
                continue
            new_entry = entry.model_copy(update={
                "status": canonical_status,
                "score": score_clean,
            })
            lines[idx] = format_tracker_entry(new_entry)
            result.changed_entries.append((entry.number, entry.status, canonical_status))
            result.changes += 1
            changed_anything = True

        if changed_anything and not dry_run:
            # No .bak: git is the backup. Concurrent safety: holding the lock.
            apps_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


# ----- verify -----


_TRACKER_EXPECTED_COLUMNS = 9


def _check_tracker_row_columns(line: str) -> str | None:
    """Return an error string when ``line`` is a tracker data row whose
    pipe-separated cell count is not exactly 9. Otherwise return None.

    ``format_tracker_entry`` always emits 9 data cells (number, date, company,
    role, score, status, pdf, report, notes). A hand edit that drops or
    duplicates a `|` is silent until merge time — this hard check surfaces
    the corruption at `tracker verify`.

    Split on *unescaped* pipes only, matching ``parse_tracker_line``. A scraped
    title like ``Digital Systems Analyst Job Details | WRHA`` is written by
    ``_cell`` as ``\\|`` and is one cell, not two; counting it as two reported
    four healthy rows as corrupt for weeks and taught the operator to ignore
    the error line.

    Non-row lines (header, separator, blank, free-text) return None so prose
    around the table doesn't trigger false positives.
    """
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    if "---" in stripped:
        return None
    inner = stripped.strip("|")
    cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", inner)]
    if cells and cells[0].lower() == "#":
        # Header row.
        return None
    # Skip rows that are obviously not data rows (e.g. all-empty cells from a
    # half-deleted entry stay flagged as count mismatch below).
    actual = len(cells)
    if actual == _TRACKER_EXPECTED_COLUMNS:
        return None
    excerpt = stripped[:80]
    return (
        f"row has {actual} columns, expected {_TRACKER_EXPECTED_COLUMNS}: "
        f"{excerpt}"
    )


def _looks_like_data_row(line: str) -> bool:
    """A table line starting with a row number, i.e. not a header or a rule."""
    return bool(re.match(r"^\|\s*\d+\s*\|", line.strip()))


def verify_pipeline(
    applications_md: Path | None = None,
    additions_dir: Path | None = None,
    reports_dir: Path | None = None,
    *,
    states: StatesYaml | None = None,
) -> VerifyResult:
    """Health-check applications.md + tracker-additions/."""
    apps_path = applications_md or _DEFAULT_APPS_MD
    states = states or load_states()
    result = VerifyResult()
    if not apps_path.exists():
        return result

    lines = apps_path.read_text(encoding="utf-8").splitlines()
    seen_company_role: dict[tuple[str, str], list[int]] = {}
    by_status: dict[str, int] = {}
    seen_numbers: dict[int, int] = {}
    unreadable: list[int] = []

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        column_error = _check_tracker_row_columns(line)
        if column_error:
            result.errors.append(column_error)
            # Still attempt to parse — a count mismatch may still yield a usable
            # row, but the error is recorded so the operator can repair it.

        entry = parse_tracker_line(line)
        if entry is None:
            # A row the parser cannot read is a row that does not exist as far
            # as every other command is concerned; it must not pass silently.
            if _looks_like_data_row(line):
                unreadable.append(line_number)
            continue
        result.entries += 1
        seen_numbers[entry.number] = seen_numbers.get(entry.number, 0) + 1
        canonical = states.normalize(entry.status)
        if canonical is None or canonical != entry.status:
            result.errors.append(f"#{entry.number}: non-canonical status {entry.status!r}")
        if "**" in entry.status:
            result.errors.append(f"#{entry.number}: status contains bold markdown")
        if re.search(r"\d{4}-\d{2}-\d{2}", entry.status):
            result.errors.append(f"#{entry.number}: status contains date — move to date column")
        if not re.match(r"^(\d+(\.\d+)?/5|N/A|DUP)$", entry.score.replace("**", "").strip()):
            result.errors.append(f"#{entry.number}: invalid score {entry.score!r}")
        if "**" in entry.score:
            result.warnings.append(f"#{entry.number}: score has bold markdown")
        key = (normalize_id(entry.company), normalize_id(entry.role))
        seen_company_role.setdefault(key, []).append(entry.number)
        by_status[entry.status] = by_status.get(entry.status, 0) + 1

        if reports_dir is not None:
            link_match = re.search(r"\]\(([^)]+)\)", entry.report)
            if link_match:
                rel = link_match.group(1)
                report_path = reports_dir / rel if not Path(rel).is_absolute() else Path(rel)
                if not report_path.exists():
                    result.errors.append(f"#{entry.number}: report not found: {rel}")

    for number, count in sorted(seen_numbers.items()):
        if count > 1:
            result.errors.append(f"#{number}: row number used {count} times")
    for line_number in unreadable:
        result.errors.append(
            f"line {line_number}: looks like a tracker row but does not parse — "
            "invisible to every command that reads the tracker"
        )

    for nums in seen_company_role.values():
        if len(nums) > 1:
            result.warnings.append(f"possible duplicates: {', '.join(f'#{n}' for n in nums)}")

    pending_dir = additions_dir or _DEFAULT_ADDITIONS_DIR
    if pending_dir.exists():
        pending = [p for p in pending_dir.iterdir() if p.is_file() and p.suffix == ".tsv"]
        if pending:
            result.warnings.append(
                f"{len(pending)} pending TSVs in {pending_dir} (run `tracker merge`)"
            )

    result.by_status = by_status
    return result
