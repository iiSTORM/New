#!/usr/bin/env bash
#
# Keeps props.json fresh from a machine the provider will actually answer.
#
# The provider refuses datacenter IPs, which rules out CI, a Codespace and
# the browser alike (it sends no CORS headers, so the page cannot fetch it
# either -- measured, not assumed). What is left is a machine on an
# ordinary connection, running this on a timer. See "Automatic refresh" in
# the README for the launchd, cron and Task Scheduler entries.
#
# Designed to be safe to run unattended every half hour:
#
#   - it will not commit anything but props.json, so an editor left open in
#     the same clone cannot get swept into a commit;
#   - it exits quietly when the lines have not moved, so the log is only
#     interesting when something is wrong;
#   - a failed fetch leaves the existing props.json alone rather than
#     replacing good lines with nothing;
#   - overlapping runs are impossible, so a slow run cannot race the next.
#
#   ./scripts/refresh_props.sh            # fetch, commit and push if changed
#   ./scripts/refresh_props.sh --dry-run  # fetch and report, change nothing
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

BRANCH="${PROPS_BRANCH:-main}"
PYTHON="${PROPS_PYTHON:-python3}"
LOCK="$REPO/.props-refresh.lock"
DRY_RUN=""
[ "${1:-}" = "--dry-run" ] && DRY_RUN="--dry-run"

log() { printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

# mkdir is atomic on every filesystem worth having, where flock is not on
# macOS. A lock older than an hour is from a run that died rather than one
# still going: the fetch has a 25 second timeout.
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +60 2>/dev/null)" ]; then
    log "clearing a stale lock from a run that did not finish"
    rmdir "$LOCK" 2>/dev/null || true
    mkdir "$LOCK" 2>/dev/null || { log "could not take the lock, giving up"; exit 0; }
  else
    log "another refresh is still running, skipping this tick"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$current_branch" != "$BRANCH" ]; then
  log "on '$current_branch', not '$BRANCH' — refusing to push prop lines from the wrong branch"
  exit 1
fi

# Take the remote's newer commits first. The data scrape pushes here twice
# a day and would otherwise reject this push; --autostash keeps a dirty
# working copy from blocking an unattended run.
if ! git pull --rebase --autostash --quiet origin "$BRANCH"; then
  log "could not rebase onto origin/$BRANCH — leaving the tree alone"
  exit 1
fi

# scrape_props.py already refuses to replace good lines with an empty file
# and exits non-zero when it does, so a bad fetch stops here on its own.
if ! "$PYTHON" scripts/scrape_props.py --out props.json ${DRY_RUN}; then
  log "fetch failed — props.json left as it was"
  exit 1
fi

if [ -n "$DRY_RUN" ]; then
  log "--dry-run: nothing committed"
  exit 0
fi

# Only props.json, by path: whatever else is in the tree is not this job's.
git add props.json
if git diff --quiet --cached -- props.json; then
  log "lines unchanged since the last run"
  exit 0
fi

git -c user.name="${PROPS_GIT_NAME:-props-refresh}" \
    -c user.email="${PROPS_GIT_EMAIL:-actions@users.noreply.github.com}" \
    commit --quiet --only props.json \
    -m "Update prop lines $(date -u +%Y-%m-%dT%H:%M:%SZ)"

for attempt in 1 2 3; do
  if git push --quiet origin "$BRANCH"; then
    log "pushed fresh lines"
    exit 0
  fi
  log "push rejected ($attempt/3) — rebasing onto the newest remote and retrying"
  git pull --rebase --autostash --quiet origin "$BRANCH" || true
  sleep $((attempt * 3))
done

log "could not push after three attempts; the commit is local and the next run will carry it"
exit 1
