#!/usr/bin/env python3
"""Walk-forward sweep of one weight, against the shipped value.

The optimizer's own search minimises MAE over the whole season and then
reports that same number, which is how it talks itself into noise. This
scores a candidate the way the repo actually adopts one: out-of-sample,
fold by fold, and it must win a MAJORITY of folds rather than a total.

A total can be carried by one fold. Six folds each saying the same thing
cannot.

    python scripts/dev/sweep_weight.py --game valorant --stat deaths --param shrink
    python scripts/dev/sweep_weight.py --game valorant --param opponent --values 0,0.2,0.4
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow

SOURCES = {"lol": "data.json", "valorant": "valorant_data.json", "cs2": "cs2_data.json"}

DEFAULT_VALUES = {
    "shrink": [0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0],
    "opponent": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.4, 2.0],
    "share": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    "history": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
    "patchDiscount": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],
    "recencyHalfLife": [2, 3, 4, 5, 6, 8, 10, 14, 20],
    "kp": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],
    "career": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
}


def rows_dated(data, stat, weights):
    cfg = ow.STAT_TYPES[stat]
    out = []
    for rd in data.values():
        teams, scored = rd.get("teams", {}), rd.get("past_matches", [])
        if not teams or not scored:
            continue
        # Predict against everything on record, score only what this
        # event has played -- the same split _collect uses, and the same
        # one the app makes. Reading past_matches for both would sweep
        # against a shallower history than the model actually has.
        past = ow.history_pool(rd)
        for m in scored:
            for side in ("teamA", "teamB"):
                team = m[side]
                opp = m["teamB"] if side == "teamA" else m["teamA"]
                if team not in teams:
                    continue
                for p in teams[team]["players"]:
                    actual = ow.get_actual_stat(m, team, p["name"], cfg["key"])
                    if actual is None or actual == "unavailable":
                        continue
                    pred, prior = ow.project_point_in_time(
                        past, teams, p, team, opp, m.get("maps_counted", 2),
                        weights, m["date"], stat, m.get("patch"))
                    if pred is None:
                        continue
                    if prior == 0 and not p.get("hist"):
                        continue
                    out.append((m["date"], pred, actual))
    return out


def fold_mae(rows, bounds):
    """Per-fold MAE, so a candidate can be judged fold by fold."""
    out = []
    for lo, hi in bounds:
        sel = [r for r in rows if r[0] >= lo and (hi is None or r[0] < hi)]
        if len(sel) < 30:
            continue
        out.append(statistics.mean(abs(p - a) for _, p, a in sel))
    return out


def sweep(game, stat, param, values, folds=6):
    data = ow.load_region_data(SOURCES[game])
    base = dict(ow.SHIPPED_WEIGHTS[game][stat])
    base.setdefault("careerRamp", 0)
    shipped_value = base.get(param, 0.0)

    ref_rows = rows_dated(data, stat, base)
    if len(ref_rows) < 200:
        print(f"  {game}/{stat}: only {len(ref_rows)} rows, not enough to judge")
        return
    dates = sorted({d for d, _, _ in ref_rows})
    bounds = ow.fold_boundaries(dates, folds)
    ref = fold_mae(ref_rows, bounds)
    ref_total = statistics.mean(ref)

    print(f"\n{game}/{stat}  {param}  (shipped {shipped_value}, "
          f"{len(ref_rows)} rows, {len(ref)} folds, MAE {ref_total:.4f})")
    print(f"  {'value':>7}  {'folds won':>9}  {'MAE':>8}  {'change':>8}   verdict")
    for v in values:
        cand = dict(base)
        cand[param] = v
        got = fold_mae(rows_dated(data, stat, cand), bounds)
        if len(got) != len(ref):
            continue
        wins = sum(1 for a, b in zip(ref, got) if b < a)
        total = statistics.mean(got)
        same = abs(v - shipped_value) < 1e-9
        verdict = "= shipped" if same else ("ADOPT" if wins * 2 > len(ref) else "")
        print(f"  {v:>7}  {wins:>4}/{len(ref):<4}  {total:8.4f}  "
              f"{100 * (total / ref_total - 1):+7.2f}%   {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True, choices=list(SOURCES))
    ap.add_argument("--stat", default="all")
    ap.add_argument("--param", required=True)
    ap.add_argument("--values", default=None,
                    help="comma separated; defaults to a sensible grid per param")
    ap.add_argument("--folds", type=int, default=6)
    args = ap.parse_args()

    values = ([float(x) if "." in x or args.param != "recencyHalfLife" else int(x)
               for x in args.values.split(",")]
              if args.values else DEFAULT_VALUES.get(args.param))
    if not values:
        print(f"! no default grid for {args.param}; pass --values", file=sys.stderr)
        return 1

    stats = ([args.stat] if args.stat != "all"
             else [s for s in ow.STAT_TYPES if ow.stat_applies_to(s, args.game)])
    for stat in stats:
        if not ow.SHIPPED_WEIGHTS.get(args.game, {}).get(stat):
            continue
        sweep(args.game, stat, args.param, values, args.folds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
