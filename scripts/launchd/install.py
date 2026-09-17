#!/usr/bin/env python3
"""Install (or reinstall) the daily scan as a per-user launchd agent, 07:30 local.

Written with plistlib so a checkout path containing "&" or "<" still produces a
valid plist, and linted before the running agent is unloaded — a bad plist
must not leave the machine with no agent at all (Codex review 2026-09-16).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

LABEL = "com.jobhunt.daily-scan"


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    agents = Path.home() / "Library" / "LaunchAgents"
    target = agents / f"{LABEL}.plist"
    (repo / "logs").mkdir(exist_ok=True)
    agents.mkdir(parents=True, exist_ok=True)
    log = str(repo / "logs" / "daily-scan.launchd.log")
    spec = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(repo / "scripts" / "daily_scan.sh")],
        "WorkingDirectory": str(repo),
        # A Mac asleep at 07:30 runs the job when it wakes.
        "StartCalendarInterval": {"Hour": 7, "Minute": 30},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    with tempfile.NamedTemporaryFile("wb", suffix=".plist", delete=False) as handle:
        plistlib.dump(spec, handle)
        staged = Path(handle.name)
    lint = subprocess.run(["plutil", "-lint", str(staged)], capture_output=True, text=True)
    if lint.returncode != 0:
        print(lint.stdout + lint.stderr, file=sys.stderr)
        staged.unlink()
        return 1
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    staged.replace(target)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    print(f"installed {LABEL} → {target} (daily 07:30). Run now: launchctl kickstart {domain}/{LABEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
