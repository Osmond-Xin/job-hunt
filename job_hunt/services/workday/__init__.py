"""Workday-specific helpers extracted from cli.py.

Submodules:
- ``employer_config``: load and select per-employer yaml configs.
- ``review_gate``: detect Workday Review-page validation issues.
- ``application_questions``: yaml-driven dispatcher for the Application Questions step.

cli.py keeps thin re-export aliases so existing call sites and tests continue to work
during the gradual extraction. New code should import from these submodules directly.
"""
