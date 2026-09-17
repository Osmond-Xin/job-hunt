#!/usr/bin/env bash
# Daily quota-free scan, then the balanced shortlist, written where it can be read.
#
# Why this exists (2026-09-16): nothing ran the scan on its own. The last
# `scan --apply` before that day was 2026-09-05, so the government, northern,
# Job Bank and Chinese-board tiers had not refreshed the inbox in eleven days,
# and the daily list was whatever the Adzuna AI-phrase harvests returned. The
# operator asked for it to run every day without being asked.
#
# --no-websearch keeps it off the Brave quota. Installed as a launchd agent by
# scripts/launchd/install.sh; run it by hand the same way.
set -uo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo" || exit 1
mkdir -p logs data/daily
log="logs/daily-scan.log"
today="$(date +%F)"

{
    echo "=== $(date '+%F %T') daily scan start"
    COLUMNS=200 .venv/bin/job-hunt scan --apply --no-websearch
    echo "--- scan exit $?"
    COLUMNS=200 .venv/bin/job-hunt triage --limit 30 > "data/daily/triage-$today.txt" 2>&1
    echo "--- triage exit $? → data/daily/triage-$today.txt"
    echo "=== $(date '+%F %T') daily scan end"
} >> "$log" 2>&1
