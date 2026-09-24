#!/usr/bin/env python3
"""Does ONE weight set across all three stats beat three tuned ones?

LoL says it can. Its three separately-fitted sets were replaced by a
single shared one and it won out of sample -- kills -0.40% (4/6 folds),
deaths -1.00% (5/6), assists -0.64% (5/6) -- which is what per-stat
overfitting looks like from the outside. Three sets is three times the
parameters fitted on the same season.

CS2 and Valorant were never given the same test and still carry three
divergent sets each. This runs it.

The search NEVER SEES the folds it is judged on. That is not ceremony:
a greedy pass over a few hundred candidates against six folds will
manufacture a majority about a third of the time, and this session has
already watched a search claim -2.56% on deaths and deliver +0.67%
WORSE on held-out folds. So the set is fitted on the early folds and
reported on the late ones, per stat.

The objective has to be scale-free, since kills sit near 6.5 MAE and
assists near 2.9: each stat is scored as a RATIO to what the shipped
weights get on the same folds, and the search minimises the mean of
those ratios. A set that helps kills by wrecking assists cannot win.

Usage:
    python scripts/dev/shared_weight_set.py --game cs2
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow
import sweep_weight as sw

STATS = ("kills", "deaths", "assists")
SEARCH_FOLDS, TOTAL_FOLDS = 4, 6
GRID = {
    "history": [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
    "opponent": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    "kp": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
    "recencyHalfLife": [2, 3, 4, 6, 8, 10, 14, 20],
    "patchDiscount": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    "career": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    "share": [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8],
    "shrink": [0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True, choices=list(sw.SOURCES))
    ap.add_argument("--rounds", type=int, default=6)
    args = ap.parse_args()

    data = ow.load_region_data(sw.SOURCES[args.game])
    shipped = {}
    bounds = {}
    base_folds = {}
    for stat in STATS:
        weights = dict(ow.SHIPPED_WEIGHTS[args.game][stat])
        weights.setdefault("careerRamp", 0)
        shipped[stat] = weights
        rows = sw.rows_dated(data, stat, weights)
        dates = sorted({d for d, _, _ in rows})
        bounds[stat] = ow.fold_boundaries(dates, TOTAL_FOLDS)
        base_folds[stat] = sw.fold_mae(rows, bounds[stat])

    def folds_for(stat, weights):
        return sw.fold_mae(sw.rows_dated(data, stat, weights), bounds[stat])

    def score(weights, upto):
        """Mean ratio to shipped over the first `upto` folds."""
        ratios = []
        for stat in STATS:
            got = folds_for(stat, weights)
            if len(got) != len(base_folds[stat]):
                return None, None
            ratios.append(statistics.mean(got[:upto])
                          / statistics.mean(base_folds[stat][:upto]))
        return statistics.mean(ratios), ratios

    # Start from the average of the three shipped sets, rounded onto the
    # grid: a neutral point that favours none of them.
    current = {}
    for param, values in GRID.items():
        mean = statistics.mean(float(shipped[s].get(param, 0) or 0) for s in STATS)
        current[param] = min(values, key=lambda v: abs(float(v) - mean))
    current["careerRamp"] = 0

    best, _ = score(current, SEARCH_FOLDS)
    print(f"{args.game}: starting from the mean of the three shipped sets, "
          f"search-fold ratio {best:.4f}  (1.0 = as good as shipped)")

    for _ in range(args.rounds):
        improved = None
        for param, values in GRID.items():
            for value in values:
                if float(current[param]) == float(value):
                    continue
                candidate = dict(current)
                candidate[param] = value
                got, _ = score(candidate, SEARCH_FOLDS)
                if got is not None and got < best - 1e-6:
                    if improved is None or got < improved[0]:
                        improved = (got, param, value)
        if improved is None:
            break
        best, param, value = improved
        print(f"  {param}: {current[param]} -> {value}   search ratio {best:.4f}")
        current[param] = value

    print(f"\nshared set: { {k: current[k] for k in GRID} }")
    print(f"\n{'stat':9} {'shipped':>9} {'shared':>9} {'change':>9}   held-out folds")
    verdict = []
    for stat in STATS:
        got = folds_for(stat, current)
        hold_base = base_folds[stat][SEARCH_FOLDS:]
        hold_new = got[SEARCH_FOLDS:]
        won = sum(1 for a, b in zip(hold_base, hold_new) if b < a)
        change = statistics.mean(hold_new) / statistics.mean(hold_base) - 1
        verdict.append(won * 2 > len(hold_base))
        print(f"{stat:9} {statistics.mean(hold_base):>9.4f} "
              f"{statistics.mean(hold_new):>9.4f} {change * 100:>8.2f}% "
              f"  {won}/{len(hold_base)}")
    print("\nVERDICT:", "shared set wins the holdout on all three"
          if all(verdict) else
          f"shared set fails the holdout on {sum(1 for v in verdict if not v)} of 3 "
          f"-- three tuned sets stand")


if __name__ == "__main__":
    main()
