#!/usr/bin/env python3
"""How much of each stat is predictable AT ALL, and how much we get.

A projection cannot beat the player. If two players with identical true
scoring levels produce 14 kills and 22 kills in a given map, no model
sees the difference coming -- that gap is the game, not a modelling
failure. So the useful question is not "what is our MAE" but "how close
is it to the best MAE anyone could have".

The decomposition is the standard one-way random-effects split. Every
observed outcome is a player's true level plus map-to-map noise:

    Y_ij = mu_i + e_ij        Var(Y) = var_mu + var_e

The best possible predictor is mu_i itself, so the highest correlation
with the outcome anyone can reach is

    CEILING = sd_mu / sd_Y

var_mu is recovered from the between- and within-player mean squares,
which is what stops a player's own sample noise from being counted as
real spread -- taking the variance of player averages directly would
overstate it badly for anyone with three maps on file.

ACHIEVED is the model's own correlation with the outcome on the same
rows, point-in-time. The ratio of the two is the honest score: what
fraction of the available signal the model is actually capturing.

WHAT THIS CEILING IS, AND WHAT IT IS NOT. It is the best a predictor
that knows only WHO THE PLAYER IS could do. A model that also knows the
opponent, the map count and the patch can legitimately beat it, because
some of what this treats as within-player noise is predictable from
context. So a score above 100% is not a bug in the arithmetic, it is the
metric's scope -- and it is itself a finding.

RESULT, on the current data:

  game/stat          rows  ceiling  achieved  of ceiling
  lol/kills          7398    0.608     0.666        110%
  lol/deaths         7398    0.377     0.554        147%
  lol/assists        7398    0.578     0.682        118%
  valorant/kills     2357    0.383     0.290         76%
  valorant/deaths    2357    0.330     0.236         71%
  valorant/assists   2357    0.619     0.538         87%
  cs2/kills          3128    0.449     0.216         48%
  cs2/deaths         3128    0.399     0.194         49%
  cs2/assists        3128    0.414     0.220         53%

LoL is over 100% on all three, and it is the only game carrying an
opponent weight (0.4). That is the cleanest evidence yet that its
opponent term earns its place: it is predicting within-player variation
that a player-constant model cannot reach. CS2 and Valorant both sit at
opponent 0.0 -- measured, repeatedly -- so neither can exceed this, and
neither does.

CS2 at roughly half is the number that matters. It is not failing to
predict context; it is failing to pin down the PLAYER, which is what
thin history looks like. The estimate is stable against how many maps a
player must have to count -- 44% to 50% across thresholds of 2, 3, 5 and
8 -- so it is not an artefact of the sample being lopsided.

Usage:
    python scripts/dev/predictability_ceiling.py
    python scripts/dev/predictability_ceiling.py --game cs2
"""
import argparse
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow
import sweep_weight as sw

STATS = ("kills", "deaths", "assists")
MIN_MAPS_PER_PLAYER = 2   # a player with one map contributes no within-variance
MIN_ROWS = 100


def corr(xs, ys):
    if len(xs) < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs) / (sx * sy)


def ceiling_from(groups):
    """groups: {player: [outcomes]} -> (ceiling, sd_true, sd_outcome, n)."""
    usable = {k: v for k, v in groups.items() if len(v) >= MIN_MAPS_PER_PLAYER}
    if len(usable) < 2:
        return None
    counts = [len(v) for v in usable.values()]
    total = sum(counts)
    k = len(usable)
    grand = sum(sum(v) for v in usable.values()) / total

    ss_between = sum(len(v) * (statistics.mean(v) - grand) ** 2 for v in usable.values())
    ss_within = sum(sum((x - statistics.mean(v)) ** 2 for x in v) for v in usable.values())
    if k < 2 or total - k < 1:
        return None
    ms_between = ss_between / (k - 1)
    ms_within = ss_within / (total - k)

    # n0: the effective group size for unbalanced designs. Using the plain
    # mean here inflates var_mu whenever the sample is lopsided, which it
    # always is -- a handful of players carry forty maps and most carry three.
    n0 = (total - sum(c * c for c in counts) / total) / (k - 1)
    var_mu = max(0.0, (ms_between - ms_within) / n0) if n0 else 0.0
    var_e = ms_within
    sd_y = (var_mu + var_e) ** 0.5
    if not sd_y:
        return None
    return (var_mu ** 0.5) / sd_y, var_mu ** 0.5, sd_y, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="all", choices=["all", *sw.SOURCES])
    args = ap.parse_args()
    games = list(sw.SOURCES) if args.game == "all" else [args.game]

    print(f"{'game/stat':18} {'rows':>6} {'ceiling':>8} {'achieved':>9} "
          f"{'of ceiling':>11}   sd(true)/sd(outcome)")
    for game in games:
        data = ow.load_region_data(sw.SOURCES[game])
        if not data:
            print(f"{game}: no data file")
            continue
        for stat in STATS:
            weights = dict(ow.SHIPPED_WEIGHTS[game][stat])
            weights.setdefault("careerRamp", 0)
            key = ow.STAT_TYPES[stat]["key"]

            groups = defaultdict(list)
            preds, actuals = [], []
            for rd in data.values():
                teams, scored = rd.get("teams", {}), rd.get("past_matches", [])
                if not teams or not scored:
                    continue
                pool = ow.history_pool(rd)
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
                            groups[(team, player["name"])].append(actual)
                            predicted, prior = ow.project_point_in_time(
                                pool, teams, player, team, opp,
                                ow.maps_counted_for(match), weights,
                                match["date"], stat, match.get("patch"))
                            if predicted is None:
                                continue
                            if prior == 0 and not player.get("hist"):
                                continue
                            preds.append(predicted)
                            actuals.append(actual)

            got = ceiling_from(groups)
            achieved = corr(preds, actuals)
            if got is None or achieved is None or len(preds) < MIN_ROWS:
                print(f"{game + '/' + stat:18} {len(preds):>6} {'(thin)':>8}")
                continue
            ceiling, sd_true, sd_y, n = got
            share = achieved / ceiling if ceiling else None
            print(f"{game + '/' + stat:18} {len(preds):>6} {ceiling:>8.3f} "
                  f"{achieved:>9.3f} {share * 100:>10.0f}%   "
                  f"{sd_true:.2f} / {sd_y:.2f}")


if __name__ == "__main__":
    main()
