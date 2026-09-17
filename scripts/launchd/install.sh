#!/usr/bin/env bash
# Install (or reinstall) the daily scan as a per-user launchd agent, 07:30 local.
set -euo pipefail

repo="$(cd "$(dirname "$0")/../.." && pwd)"
label="com.jobhunt.daily-scan"
plist="$HOME/Library/LaunchAgents/$label.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$repo/logs"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$repo/scripts/daily_scan.sh</string></array>
  <key>WorkingDirectory</key><string>$repo</string>
  <!-- A Mac asleep at 07:30 runs the job when it wakes. -->
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>$repo/logs/daily-scan.launchd.log</string>
  <key>StandardErrorPath</key><string>$repo/logs/daily-scan.launchd.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "installed $label → $plist (daily 07:30). Run now: launchctl kickstart gui/$(id -u)/$label"
