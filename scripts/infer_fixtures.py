#!/usr/bin/env python3
"""Adds the fixtures the posted board implies to a game's data file.

Runs after the scraper (and, for LoL, after merge.py) and before
check_data.py. The rule it applies, and the reason it never pairs two teams
on a shared kickoff, is in board_fixtures.py.

    python scripts/infer_fixtures.py            # every configured game
    python scripts/infer_fixtures.py cs2 lol    # just these
    python scripts/infer_fixtures.py --dry-run  # report, write nothing

Exit status is 0 whenever the run was able to judge the board, including
when it judged that nothing should be added. A board that adds nothing is
the normal state once the schedule provider has caught up, and failing the
workflow for it would train everyone to ignore this step.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_fixtures as bf
from scrape_props import GAMES

PROPS_PATH = "props.json"


def infer_for_game(data, props, game, now):
    """Rewrites data["regions"][*]["upcoming_matches"] in place.

    Returns (reported, counts) where reported is [(region, counts)] for the
    regions worth printing.
    """
    regions = data.get("regions") or {}
    slots_by_region, counts = bf.board_slots(
        props.get("props") or {}, game, regions, now,
        fetched_at=props.get("fetched_at"))
    changed, by_region = bf.augment_regions(regions, slots_by_region)
    counts.update(changed)
    reported = [(region_key, region_counts)
                for region_key, region_counts in sorted(by_region.items())
                if bf.describe(region_counts)]
    return reported, counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    # choices= is deliberately not used: argparse validates nargs="*" against
    # it even when the list is empty, so the default-to-everything case dies
    # with "invalid choice: []".
    parser.add_argument("games", nargs="*", metavar="GAME",
                        help=f"one or more of: {', '.join(sorted(GAMES))} "
                             f"(default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    parser.add_argument("--props", default=PROPS_PATH)
    args = parser.parse_args(argv)
    unknown = [g for g in args.games if g not in GAMES]
    if unknown:
        parser.error(f"unknown game(s) {unknown}; choose from {sorted(GAMES)}")

    try:
        with open(args.props) as f:
            props = json.load(f)
    except FileNotFoundError:
        # Nothing to infer from is not a failure: the board is refreshed on
        # its own schedule, and this step is additive by design.
        print(f"No {args.props} — no board to infer fixtures from")
        return 0

    now = datetime.now(timezone.utc)
    captured = bf.parse_stamp(props.get("fetched_at"))
    age = f"{(now - captured).total_seconds() / 3600:.1f}h old" if captured else "age unknown"
    print(f"Board {args.props} ({age})")

    grand = {"filled": 0, "added": 0, "ambiguous": 0}
    for game in (args.games or sorted(GAMES)):
        path = GAMES[game]["data"]
        try:
            with open(path) as f:
                data = json.load(f)
        except FileNotFoundError:
            print(f"  {game}: no {path} — skipped", file=sys.stderr)
            continue
        reported, total = infer_for_game(data, props, game, now)
        # Per region only where there is more than one: CS2 is a single pool,
        # and "cs2/CS2: 9 added" above "cs2: 9 added" is the same line twice.
        if len(data.get("regions") or {}) > 1:
            for region_key, region_counts in reported:
                print(f"  {game}/{region_key}: {bf.describe(region_counts)}")
        summary = bf.describe(total)
        if summary:
            print(f"  {game}: {summary}")
        for key in grand:
            grand[key] += total.get(key, 0)
        changed = total.get("filled") or total.get("added")
        if changed and not args.dry_run:
            with open(path, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            print(f"  {game}: wrote {path}")
        elif changed:
            print(f"  {game}: would write {path} (dry run)")
        else:
            print(f"  {game}: nothing to add")

    print(f"Inferred from the board: {grand['filled']} undecided side(s) filled, "
          f"{grand['added']} fixture(s) added, "
          f"{grand['ambiguous']} slot(s) too ambiguous to touch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
