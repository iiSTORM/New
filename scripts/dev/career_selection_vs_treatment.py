#!/usr/bin/env python3
"""Is a gap between two groups of ROWS an effect, or is it the groups?

Written after getting this wrong. The CS2 career scrape had been dead
for weeks, and the cost was reported as "rows without career data are
projected 12.7% worse on kills, 18.1% on deaths, 11.4% on assists". That
compared players who HAD career records against players who did not,
which is mostly a statement about which players bo3.gg had resolved:
the established ones, who are more predictable whatever tiers you give
them.

The test is to split the same rows the same way and then score BOTH
groups with the tier switched off. Whatever gap survives was never
about the tier.

RESULT, once coverage reached 98% (n=3,128 point-in-time rows):

                    selection   treatment
      kills           +6.9%       -1.2%
      deaths         +10.2%       -2.3%
      assists         +7.3%       -1.7%

So roughly three quarters of the reported gap was the groups, not the
data. The career tier is still worth having -- switching it off across
ALL rows costs +0.5% / +2.4% / +2.1%, a fold majority on each -- but it
is worth about a quarter of what the first number claimed.

The general lesson is cheap to state and was expensive to learn: a
comparison between rows that HAVE a thing and rows that LACK it is not
a measurement of the thing. Toggle it on the same rows.

Usage:
    python scripts/dev/career_selection_vs_treatment.py \\
        --game cs2 --had <a json file keyed by the player names that had it>
"""
import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow

SOURCES = {"lol": "data.json", "valorant": "valorant_data.json", "cs2": "cs2_data.json"}


def mae(values):
    return statistics.mean(values) if values else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="cs2", choices=list(SOURCES))
    ap.add_argument("--had", required=True,
                    help="JSON object keyed by the player names that had the tier")
    ap.add_argument("--param", default="career", help="the weight to toggle")
    args = ap.parse_args()

    with open(args.had) as f:
        had_it = set(json.load(f))
    data = ow.load_region_data(SOURCES[args.game])

    for stat in ("kills", "deaths", "assists"):
        base = dict(ow.SHIPPED_WEIGHTS[args.game][stat])
        base.setdefault("careerRamp", 0)
        off = dict(base)
        off[args.param] = 0.0
        key = ow.STAT_TYPES[stat]["key"]
        groups = {True: {"on": [], "off": []}, False: {"on": [], "off": []}}
        for rd in data.values():
            teams, scored = rd.get("teams", {}), rd.get("past_matches", [])
            if not teams or not scored:
                continue
            past = ow.history_pool(rd)
            for match in scored:
                for side in ("teamA", "teamB"):
                    team = match[side]
                    opp = match["teamB"] if side == "teamA" else match["teamA"]
                    if team not in teams:
                        continue
                    for player in teams[team]["players"]:
                        actual = ow.get_actual_stat(match, team, player["name"], key)
                        if actual is None or actual == "unavailable":
                            continue
                        bucket = groups[player["name"] in had_it]
                        for label, weights in (("on", base), ("off", off)):
                            predicted, prior = ow.project_point_in_time(
                                past, teams, player, team, opp,
                                ow.maps_counted_for(match), weights,
                                match["date"], stat, match.get("patch"))
                            if predicted is None:
                                continue
                            if prior == 0 and not player.get("hist"):
                                continue
                            bucket[label].append(abs(actual - predicted))

        have, lack = groups[True], groups[False]
        print(f"\n{args.game}/{stat}")
        print(f"  had it     n={len(have['off']):5d}  off {mae(have['off']):.4f}  "
              f"on {mae(have['on']):.4f}")
        print(f"  did not    n={len(lack['off']):5d}  off {mae(lack['off']):.4f}  "
              f"on {mae(lack['on']):.4f}")
        print(f"  SELECTION (groups differ with it off for both): "
              f"{(mae(lack['off']) / mae(have['off']) - 1) * 100:+.1f}%")
        print(f"  TREATMENT (the tier, on the group that lacked it): "
              f"{(mae(lack['on']) / mae(lack['off']) - 1) * 100:+.1f}%")


if __name__ == "__main__":
    main()
