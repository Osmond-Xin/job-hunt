"""Where `evaluate-batch --max-cost` gets its numbers.

Three subtleties, each measured against a real failure:

- The ledger path comes from settings, not a hardcoded `data/`. Hardcoding it
  made the cap a silent no-op for anyone whose `paths.data_dir` is not the
  default.
- `premium_records > priced_records` means the cap is *unenforceable* — a
  different fact from "$0 spent". Both look like $0.00 to a simple sum, and
  treating the second one as the first silently disables the cap.
- `bool` is an `int` subclass, and JSON round-trips NaN/Infinity — either of
  those would count as "priced" while corrupting the running total.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from job_hunt.config.models import load_settings


def _ledger_path() -> Path:
    """Ledger location as observability.write_usage_ledger computes it.

    Hardcoding `data/` here made --max-cost a silent no-op for anyone whose
    paths.data_dir is not the default.
    """
    return Path(load_settings().paths.data_dir) / "usage-ledger.jsonl"


def _ledger_line_count() -> int:
    path = _ledger_path()
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _ledger_spend_since(start_line: int) -> tuple[float, int, int]:
    """Return ``(total_usd, premium_records, records_with_a_cost)``.

    The counts exist so a budget cap can tell "nothing was spent" from "spend
    is not being recorded". Both look like $0.00 to a simple sum, and the
    second one silently disables the cap.
    """
    path = _ledger_path()
    if not path.exists():
        return 0.0, 0, 0
    total = 0.0
    premium_records = 0
    priced_records = 0
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < start_line or not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("model_tier") != "premium":
                continue
            premium_records += 1
            cost = record.get("cost_usd")
            # bool is an int subclass, and JSON round-trips NaN/Infinity — any
            # of those would count as "priced" while corrupting the total.
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost):
                priced_records += 1
                total += float(cost)
    return total, premium_records, priced_records


def _ledger_cost_since(start_line: int) -> float:
    """Sum reported USD cost of ledger records written after ``start_line``."""
    return _ledger_spend_since(start_line)[0]
