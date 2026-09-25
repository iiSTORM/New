#!/usr/bin/env python3
"""Of the deviation the model claims, how much actually shows up?

Ranking the board by raw edge size puts the least-evidenced rows at the
top, because a model with little to go on produces wilder numbers, and
wilder numbers are bigger disagreements with the line. That is exactly
backwards for anyone reading the top of the list as the best bets.

The fix has to be measured rather than invented. For every point-in-time
prediction, take how far the model placed the player from a typical
player for that stat (the CLAIM) and how far they actually landed (the
REALISATION), and regress one on the other within each evidence bucket:

    slope = sum(claim * realised) / sum(claim^2)

That slope is the fraction of a claimed deviation that materialises. A
slope of 1.0 means the model's disagreements are worth their face value;
0.6 means six tenths of every claimed edge is real and the rest is the
model talking to itself. It is the standard calibration/shrinkage
coefficient, and it is exactly the multiplier an edge should carry.

Run: python scripts/dev/edge_realization.py
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow
import evidence_vs_accuracy as ev


def rows_for(region_data, stat_type, weights):
    """(evidence, predicted, actual) for every scoreable point-in-time row."""
    cfg = ow.STAT_TYPES[stat_type]
    out = []
    for rd in region_data.values():
        teams = rd.get("teams", {})
        past = rd.get("past_matches", [])
        if not teams or not past:
            continue
        for match in past:
            for side in ("teamA", "teamB"):
                team = match[side]
                opp = match["teamB"] if side == "teamA" else match["teamA"]
                if team not in teams:
                    continue
                for player in teams[team]["players"]:
                    actual = ow.get_actual_stat(match, team, player["name"], cfg["key"])
                    if actual is None or actual == "unavailable":
                        continue
                    predicted, prior = ow.project_point_in_time(
                        past, teams, player, team, opp,
                        ow.maps_counted_for(match), weights,
                        match["date"], stat_type, match.get("patch"))
                    if predicted is None:
                        continue
                    if prior == 0 and not player.get("hist"):
                        continue
                    out.append((ev.evidence_for(player, prior, match["date"], weights),
                                predicted, actual))
    return out


def slope(rows, baseline):
    """How much of a claimed deviation materialises. None if too thin."""
    num = den = 0.0
    for _, pred, act in rows:
        claim = pred - baseline
        num += claim * (act - baseline)
        den += claim * claim
    if den <= 0 or len(rows) < 40:
        return None
    return num / den


BUCKETS = [(0, 4), (4, 8), (8, 12), (12, 20), (20, 10**6)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=["lol", "valorant", "cs2", "all"], default="all")
    args = ap.parse_args()
    sources = {"lol": "data.json", "valorant": "valorant_data.json", "cs2": "cs2_data.json"}
    games = list(sources) if args.game == "all" else [args.game]

    pooled = {}
    for game in games:
        data = ow.load_region_data(sources[game])
        if not data:
            continue
        print(f"\n=== {game} " + "=" * 52)
        for stat in ow.STAT_TYPES:
            if not ow.stat_applies_to(stat, game):
                continue
            weights = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
            if not weights:
                continue
            rows = rows_for(data, stat, weights)
            if not rows:
                continue
            # A typical player for this stat, which is what a deviation is
            # measured from. The model's own league prior would do, but the
            # observed mean needs no weights to compute and cannot drift
            # away from the thing being predicted.
            baseline = statistics.mean(a for _, _, a in rows)
            overall = slope(rows, baseline)
            print(f"\n  {game} {stat}  (n={len(rows)}, baseline {baseline:.2f}, "
                  f"overall slope {overall:.3f})" if overall else
                  f"\n  {game} {stat}  (n={len(rows)}, too thin)")
            print(f"    {'evidence':>10}  {'n':>5}  {'slope':>7}")
            for lo, hi in BUCKETS:
                sel = [r for r in rows if lo <= r[0] < hi]
                sl = slope(sel, baseline)
                if sl is None:
                    continue
                label = f"{lo}-{hi}g" if hi < 10**6 else f"{lo}+g"
                print(f"    {label:>10}  {len(sel):5}  {sl:7.3f}")
                pooled.setdefault((lo, hi), []).append((len(sel), sl))

    print("\n\n=== pooled across every game and stat " + "=" * 20)
    print(f"  {'evidence':>10}  {'rows':>6}  {'slope':>7}   (n-weighted)")
    for (lo, hi), entries in sorted(pooled.items()):
        n = sum(c for c, _ in entries)
        sl = sum(c * s for c, s in entries) / n
        label = f"{lo}-{hi}g" if hi < 10**6 else f"{lo}+g"
        print(f"  {label:>10}  {n:6}  {sl:7.3f}")


if __name__ == "__main__":
    main()
