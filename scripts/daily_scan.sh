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
# scripts/launchd/install.py; run it by hand the same way.
set -u

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo" || exit 1
mkdir -p logs data/daily data/locks
log="logs/daily-scan.log"
today="$(date +%F)"

# One run at a time. `scan --apply` snapshots the known URLs and appends to
# data/pipeline.md without a lock of its own, so two overlapping runs of this
# script would both append the same new posting. mkdir is atomic; macOS ships
# no flock. The holder's PID is recorded so a run killed before its EXIT trap
# (SIGKILL, power loss) does not disable every later run (Codex review
# 2026-09-16): a lock whose holder is gone is taken over.
lock="data/locks/daily-scan.lock"
if ! mkdir "$lock" 2>/dev/null; then
    holder="$(cat "$lock/pid" 2>/dev/null || true)"
    # The holder is live only if that PID is still *this script* — a PID the OS
    # recycled to an unrelated process must not block every later run.
    if [ -n "$holder" ] && ps -p "$holder" -o command= 2>/dev/null | grep -q "daily_scan.sh"; then
        echo "=== $(date '+%F %T') daily scan pid $holder still running — skipped" >> "$log"
        exit 75
    fi
    # No PID yet means another run created the lock a moment ago and has not
    # written it; only a PID-less lock older than ten minutes is abandoned.
    if [ -z "$holder" ] && [ -z "$(find "$lock" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
        echo "=== $(date '+%F %T') lock just taken by another run — skipped" >> "$log"
        exit 75
    fi
    echo "=== $(date '+%F %T') stale lock (pid ${holder:-unknown} gone) — taking it over" >> "$log"
    rm -rf "$lock"
    if ! mkdir "$lock" 2>/dev/null; then
        echo "=== $(date '+%F %T') lost the race for $lock — skipped" >> "$log"
        exit 75
    fi
fi
echo $$ > "$lock/pid"
trap 'rm -rf "$lock"' EXIT

scan_status=0
triage_status=0
{
    echo "=== $(date '+%F %T') daily scan start"
    COLUMNS=200 .venv/bin/job-hunt scan --apply --no-websearch
    scan_status=$?
    echo "--- scan exit $scan_status"
    COLUMNS=200 .venv/bin/job-hunt triage --limit 30 > "data/daily/triage-$today.txt" 2>&1
    triage_status=$?
    echo "--- triage exit $triage_status → data/daily/triage-$today.txt"
    echo "=== $(date '+%F %T') daily scan end"
} >> "$log" 2>&1

# The last echo succeeding must not report the run as a success.
if [ "$scan_status" -ne 0 ]; then exit "$scan_status"; fi
exit "$triage_status"
