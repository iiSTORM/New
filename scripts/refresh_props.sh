#!/usr/bin/env bash
#
# Keeps props.json fresh, from a saved board or from a provider that
# permits fetching.
#
# Two routes in, tried in this order:
#
#   1. A board saved by the bookmarklet in tools/. You click it in your own
#      logged-in browser and prizepicks-payload.json lands in Downloads;
#      this picks it up, refuses it if it has gone stale, and runs the
#      matcher over it. That is the working route today.
#
#   2. A live fetch, for a provider with a real server-side API. This
#      cannot work with the PrizePicks adapter: it refuses this script
#      from every network, home connections included -- measured on a
#      Windows machine whose own browser fetched the payload fine minutes
#      later. Point PROVIDERS at a fetchable source and this route starts
#      working unchanged.
#
# Override the pickup with PROPS_PAYLOAD_DIR / PROPS_PAYLOAD_NAME, or skip
# it entirely with PROPS_PAYLOAD_DIR=none.
#
# See "Automatic refresh" in the README for the launchd and cron entries,
# and refresh_props.ps1 for the Windows equivalent.
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
# Where to look for a board saved by the bookmarklet, in order. Colon
# separated, so a codespace or a remote VS Code window can be handled
# without configuring anything: the browser saves to the machine YOUR
# browser runs on, which is not the machine this script runs on, so the
# repo root is searched too and dragging the file into the file explorer
# is enough.
PAYLOAD_DIR="${PROPS_PAYLOAD_DIR:-$HOME/Downloads:$REPO}"
PAYLOAD_NAME="${PROPS_PAYLOAD_NAME:-prizepicks-payload.json}"
# Mirrors PROPS_MAX_AGE_MINUTES in src/app.jsx, which is where staleness is
# actually enforced. Checked here too so a forgotten download is reported
# as one, instead of being committed and then greyed out in the app with
# no explanation of why.
PAYLOAD_MAX_AGE_MIN="${PROPS_PAYLOAD_MAX_AGE_MIN:-90}"
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

# Pick up a board saved by the bookmarklet, if there is a fresh one.
#
# -mmin is the freshness test rather than a timestamp read out of the
# file: scrape_props.py dates a saved payload by its mtime for exactly
# this reason, so the two agree by construction. A download older than the
# window is not passed on -- stale lines that still look actionable are
# the one failure this whole design is arranged to prevent.
FIXTURE=""
STALE=""
if [ "$PAYLOAD_DIR" != "none" ]; then
  # IFS split on ':' rather than an array, because this has to keep
  # working under the plain sh some cron and launchd setups still use.
  OLD_IFS="$IFS"; IFS=":"
  for dir in $PAYLOAD_DIR; do
    IFS="$OLD_IFS"
    [ -n "$dir" ] && [ -d "$dir" ] || { IFS=":"; continue; }
    found="$(find "$dir" -maxdepth 1 -name "$PAYLOAD_NAME" \
                  -mmin "-$PAYLOAD_MAX_AGE_MIN" 2>/dev/null | head -n 1)"
    if [ -n "$found" ]; then FIXTURE="$found"; break; fi
    # Remember a stale one so it can be reported by name. A download that
    # is simply too old and one that was never made need different advice.
    [ -z "$STALE" ] && [ -e "$dir/$PAYLOAD_NAME" ] && STALE="$dir/$PAYLOAD_NAME"
    IFS=":"
  done
  IFS="$OLD_IFS"
fi

if [ -z "$FIXTURE" ] && [ -n "$STALE" ]; then
  log "$STALE is older than ${PAYLOAD_MAX_AGE_MIN} minutes —"
  log "  click the bookmarklet again to save a current board, then re-run"
  exit 1
fi

# scrape_props.py already refuses to replace good lines with an empty file
# and exits non-zero when it does, so a bad read stops here on its own.
if [ -n "$FIXTURE" ]; then
  log "reading the board saved at $FIXTURE"
  if ! "$PYTHON" scripts/scrape_props.py --fixture "$FIXTURE" --out props.json ${DRY_RUN}; then
    log "could not read that payload — props.json left as it was"
    exit 1
  fi
elif ! "$PYTHON" scripts/scrape_props.py --out props.json ${DRY_RUN}; then
  log "fetch failed — props.json left as it was"
  log "  no board saved in any of: $PAYLOAD_DIR"
  log "  see tools/bookmarklet.html — and note that in a codespace or a remote"
  log "  VS Code window your browser downloads to a different machine, so drop"
  log "  $PAYLOAD_NAME into the repo root here."
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
