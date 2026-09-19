#!/usr/bin/env python3
"""
Sanity-checks a generated data file before the workflow commits it.

Most scrape steps in .github/workflows/scrape.yml are continue-on-error, so
a scraper that dies — or worse, one that "succeeds" but parses nothing after
a site changes its markup — used to leave the run green with stale or gutted
data. This script turns both of those into a red run.

Three things are checked:

  1. Freshness   — generated_at must be recent. If a continue-on-error scrape
                   step failed, the file on disk is still the one checked out
                   from git, so its timestamp gives the failure away.
  2. Structure   — the regions/teams/players shape the frontend expects, and
                   non-trivial content in it.
  3. Regression  — player and match counts compared against the previously
                   committed copy of the same file. A parser that silently
                   stops matching drops these to near zero while still
                   exiting 0, which is the failure mode freshness alone will
                   not catch.

Usage:
    python scripts/check_data.py lol|valorant|cs2 [options]

Exits non-zero (failing the job) on any hard failure. Softer anomalies are
printed as warnings and do not fail the run.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

# The primary output of each scrape job. These are the files the frontend
# actually fetches, so they are the ones worth failing a run over. The
# auxiliary files (career_data.json, champion_stats.json, cs2_career_data.json)
# are deliberately allowed to go stale — the model falls back cleanly when
# they are missing or old — so they are not checked here.
TARGETS = {
    "lol": "data.json",
    "valorant": "valorant_data.json",
    "cs2": "cs2_data.json",
}

# Scheduled runs are 12h apart and job timeouts cap a single run at well
# under an hour, so anything older than this was produced by an earlier run
# — i.e. this run's scrape did not actually write the file.
DEFAULT_MAX_AGE_HOURS = 6.0

# Rosters and match windows churn between runs, so some movement is normal.
# This threshold is meant to catch a parser returning near-nothing, not to
# police ordinary week-to-week variation.
DEFAULT_MAX_DROP_PCT = 50.0
WARN_DROP_PCT = 20.0

# Auxiliary files are allowed to be STALE — the model falls back cleanly when
# they are — but they are not allowed to be EMPTIED. Those are different
# failures and only the first one is handled by design.
#
# On run 116 gol.gg was unreachable, scrape_career.py resolved 0 of 320
# players, wrote {} over a 359KB cache and exited 0. Nothing caught it: the
# step counted as a success, and this script only looked at the game's
# primary file. The scraper itself now refuses to make that write, and this
# is the second line of defence, so no future path can commit a gutted
# auxiliary file either.
AUX_FILES = {
    "lol": ["career_data.json", "champion_stats.json"],
    "cs2": ["cs2_career_data.json"],
    "valorant": [],
}


def aux_entry_count(name, data):
    """A meaningful 'how much is in here' number for an auxiliary file.

    Most are a flat map of player -> record, where the key count is the
    answer. champion_stats.json instead has two fixed top-level keys, so
    counting those would always return 2 no matter how empty it was.
    """
    if not isinstance(data, dict):
        return 0
    if name == "champion_stats.json":
        return len(data.get("champions") or {}) + len(data.get("player_champions") or {})
    return len(data)


def check_aux_files(game, max_drop_pct, baseline_ref, errors):
    for name in AUX_FILES.get(game, []):
        try:
            with open(name) as f:
                current = json.load(f)
        except FileNotFoundError:
            # A continue-on-error step may legitimately not have produced it.
            print(f"  {name}: not present — skipped")
            continue
        except json.JSONDecodeError as exc:
            errors.append(f"{name} is not valid JSON: {exc}")
            continue

        now = aux_entry_count(name, current)
        baseline = load_baseline(name, baseline_ref)
        if baseline is None:
            print(f"  {name}: {now} entries (no baseline to compare)")
            continue
        before = aux_entry_count(name, baseline)
        print(f"  {name}: {now} entries (was {before})")
        if before == 0:
            continue
        drop_pct = 100.0 * (before - now) / before
        if drop_pct > max_drop_pct:
            errors.append(
                f"{name} fell {drop_pct:.0f}% ({before} -> {now} entries), over the "
                f"{max_drop_pct:.0f}% limit — the source was probably unreachable; "
                f"committing this would replace good data with nothing"
            )


def counts(data):
    """Total teams, players and past matches across every region."""
    teams = players = past = upcoming = 0
    regions = data.get("regions") or {}
    for region in regions.values():
        region_teams = region.get("teams") or {}
        teams += len(region_teams)
        for team in region_teams.values():
            players += len(team.get("players") or [])
        past += len(region.get("past_matches") or [])
        upcoming += len(region.get("upcoming_matches") or [])
    return {
        "regions": len(regions),
        "teams": teams,
        "players": players,
        "past_matches": past,
        "upcoming_matches": upcoming,
    }


def load_baseline(path, ref):
    """The previously committed copy of this file, or None if unavailable.

    Missing baselines are not an error: the file may be new, or this may be
    a shallow checkout. The regression check is simply skipped.
    """
    try:
        blob = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True, check=True,
        ).stdout
        return json.loads(blob)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def check_freshness(data, max_age_hours, errors):
    raw = data.get("generated_at")
    if not raw:
        errors.append("no generated_at field — cannot tell when this was produced")
        return
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        errors.append(f"generated_at is not an ISO timestamp: {raw!r}")
        return
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    age_hours = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
    print(f"  generated_at : {raw}  ({age_hours:.1f}h old)")
    if age_hours > max_age_hours:
        errors.append(
            f"data is {age_hours:.1f}h old (limit {max_age_hours}h) — this run's "
            f"scrape step almost certainly failed and left the committed copy in place"
        )
    elif age_hours < -1:
        # Clock skew between the runner and whatever produced the file.
        print(f"  WARNING: generated_at is {-age_hours:.1f}h in the future")


def check_structure(current, errors):
    if not isinstance(current.get("regions"), dict) or not current["regions"]:
        errors.append("'regions' is missing or empty — the frontend has nothing to render")
        return
    populated = [
        name for name, region in current["regions"].items()
        if (region.get("teams") or {})
    ]
    if not populated:
        errors.append(
            f"no region has any teams (regions present: {list(current['regions'])}) — "
            f"the scrape parsed nothing"
        )
        return
    empty = sorted(set(current["regions"]) - set(populated))
    if empty:
        # Legitimate off-season, so a warning rather than a failure.
        print(f"  WARNING: regions with no teams: {', '.join(empty)}")


def check_regression(now, before, max_drop_pct, errors):
    for field in ("players", "past_matches"):
        old, new = before[field], now[field]
        if old == 0:
            continue
        drop_pct = 100.0 * (old - new) / old
        if drop_pct > max_drop_pct:
            errors.append(
                f"{field} fell {drop_pct:.0f}% ({old} -> {new}), over the "
                f"{max_drop_pct:.0f}% limit — likely a parser that stopped matching"
            )
        elif drop_pct > WARN_DROP_PCT:
            print(f"  WARNING: {field} fell {drop_pct:.0f}% ({old} -> {new})")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", choices=sorted(TARGETS))
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    parser.add_argument("--max-drop-pct", type=float, default=DEFAULT_MAX_DROP_PCT)
    parser.add_argument("--baseline-ref", default="HEAD",
                        help="git ref holding the previous copy (default: HEAD)")
    parser.add_argument("--skip-freshness", action="store_true",
                        help="for local runs against data you did not just scrape")
    args = parser.parse_args()

    path = TARGETS[args.game]
    print(f"Checking {path} ({args.game})")

    try:
        with open(path) as f:
            current = json.load(f)
    except FileNotFoundError:
        print(f"FAIL: {path} does not exist — nothing was produced", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"FAIL: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    errors = []
    if not args.skip_freshness:
        check_freshness(current, args.max_age_hours, errors)
    check_structure(current, errors)

    now = counts(current)
    print("  contents     : " + ", ".join(f"{v} {k}" for k, v in now.items()))

    check_aux_files(args.game, args.max_drop_pct, args.baseline_ref, errors)

    baseline = load_baseline(path, args.baseline_ref)
    if baseline is None:
        print("  baseline     : unavailable — skipping regression check")
    else:
        before = counts(baseline)
        print("  baseline     : " + ", ".join(f"{v} {k}" for k, v in before.items()))
        check_regression(now, before, args.max_drop_pct, errors)

    if errors:
        print(f"\nFAIL: {path} did not pass:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"OK: {path} looks healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
