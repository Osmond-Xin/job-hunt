#!/usr/bin/env bash
#
# Mirror this project into iCloud Drive, plus a flat copy of the private
# (gitignored) files that exist nowhere else.
#
# Why this exists: profile/, config/*.yml and storage/private/ are gitignored
# by design — they hold personal data and secrets that must not reach a public
# repo. The side effect is that the most valuable artifacts (the job-search
# strategy, the tuned discovery queries, the premium LLM command) live on one
# disk with no copy anywhere. This closes that gap without putting them in git.
#
# Safe to re-run: rsync updates in place and deletes files in the destination
# that no longer exist in the source.
#
# Usage: scripts/backup-to-icloud.sh [destination-root]

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_ROOT="${1:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/JobHuntBackup}"
PROJECT_DEST="$DEST_ROOT/job-hunt"
PRIVATE_DEST="$DEST_ROOT/private-config"

if [ ! -d "$(dirname "$DEST_ROOT")" ]; then
  echo "iCloud Drive not found at $(dirname "$DEST_ROOT")" >&2
  exit 1
fi

mkdir -p "$PROJECT_DEST" "$PRIVATE_DEST"

# --- 1. Full project mirror -------------------------------------------------
#
# .git IS included: it currently holds commits that exist on no remote.
#
# storage/browser-profile is NOT: it is a live Chromium profile holding session
# cookies for LinkedIn, Workday and ATS portals. Syncing live credentials into
# cloud storage is a worse risk than losing a profile that is rebuilt by
# logging in again. Same reasoning, less severe, for .venv (rebuilt by
# `uv sync`) and the various caches.
echo "==> Mirroring project to $PROJECT_DEST"
rsync -a --delete \
  --exclude '.venv/' \
  --exclude 'storage/browser-profile/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.DS_Store' \
  --exclude 'fsmonitor--daemon.ipc' \
  --exclude '*.sock' \
  "$SRC/" "$PROJECT_DEST/"

# --- 2. Flat copy of the irreplaceable private files ------------------------
#
# Duplicated deliberately: a flat, obvious folder is what you want at 2am on a
# new machine, without having to remember which paths were gitignored.
echo "==> Copying private config to $PRIVATE_DEST"
rsync -a --delete "$SRC/profile/" "$PRIVATE_DEST/profile/"
mkdir -p "$PRIVATE_DEST/config"
for f in "$SRC"/config/*.yml; do
  case "$(basename "$f")" in
    *.example.yml) continue ;;   # examples are in git already
  esac
  cp "$f" "$PRIVATE_DEST/config/"
done
if [ -d "$SRC/storage/private" ]; then
  rsync -a --delete "$SRC/storage/private/" "$PRIVATE_DEST/storage-private/"
fi
[ -f "$SRC/.env" ] && cp "$SRC/.env" "$PRIVATE_DEST/env.backup"

# --- 3. Restore notes -------------------------------------------------------
cat > "$DEST_ROOT/README.md" <<EOF
# Job Hunt backup

Last run: $(date '+%Y-%m-%d %H:%M:%S %Z')
Source:   $SRC

## Layout

- \`job-hunt/\` — full project mirror, including \`.git\`.
- \`private-config/\` — flat copy of the gitignored files that exist in no repo:
  - \`profile/\` — CVs, profile.yml, strategy docs
  - \`config/\` — portals.yml, settings.yml, sites.yml, scheduler.yml
  - \`storage-private/\` — credential files
  - \`env.backup\` — .env

## Restoring on a new machine

\`\`\`bash
git clone <remote> job-hunt && cd job-hunt
uv sync                                   # rebuilds .venv
cp -R <backup>/private-config/profile/ profile/
cp <backup>/private-config/config/*.yml config/
cp <backup>/private-config/env.backup .env
mkdir -p storage/private && cp -R <backup>/private-config/storage-private/ storage/private/
job-hunt config doctor
\`\`\`

## Deliberately NOT backed up

- \`.venv/\` — rebuild with \`uv sync\`.
- \`storage/browser-profile/\` — a live Chromium profile with session cookies
  for LinkedIn / Workday / ATS portals. Keeping live credentials out of cloud
  sync is worth more than the convenience; log in again instead.
EOF

echo "==> Done."
du -sh "$DEST_ROOT" 2>/dev/null || true
