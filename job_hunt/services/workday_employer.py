"""Backwards-compatible re-export shim.

Real implementation lives at ``job_hunt.services.workday.employer_config`` after
the Phase 2.1 split. Existing imports (``from job_hunt.services.workday_employer
import ...``) keep working until callers migrate.
"""

from job_hunt.services.workday.employer_config import (  # noqa: F401
    _EMPLOYER_DIR,
    _FALLBACK_CONFIG,
    choices_for_op,
    load_employer_configs,
    resolve_value,
    select_employer_config,
)
