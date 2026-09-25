#!/usr/bin/env python3
"""Is the model's zero in the wrong place, and does moving it help?

diagnose_calibration.py says the model runs high on CS2 -- +0.449 kills,
+0.393 deaths, out-of-sample against actuals. A constant offset is the
cheapest correction there is: no new data, no new features, one number
per game/stat. This measures whether it actually buys anything.

TWO TRAPS, BOTH OF WHICH THIS EXISTS TO AVOID.

The first is fitting the offset on the rows it is then scored against.
An offset fitted on a window always improves that window -- that is what
fitting means -- so the number would be guaranteed and meaningless. Here
each fold's offset is computed from rows STRICTLY BEFORE that fold
starts, which is the same walk-forward discipline every weight in this
repo was chosen under, and the earliest fold with no history before it is
skipped rather than given the whole timeline.

The second is correcting by the MEAN. MAE is minimised by the MEDIAN of
the error, not its mean, and on this data they disagree enough to flip
the sign of the fix:

    cs2 kills    mean +0.449   median +0.451     agree
    cs2 deaths   mean +0.393   median -0.195     disagree
    lol kills    mean -0.009   median +0.404     "calibrated" on the mean

Subtracting the mean from CS2 deaths would push its median error from
-0.195 to -0.59 and make MAE worse. So both estimators are reported and
neither is assumed.

A caveat worth stating rather than burying: an offset that minimises MAE
moves the mean error AWAY from zero, and the mean is what matters for a
posted line -- a model centred a third of a kill low recommends "under"
slightly too often on every player. MAE and bet-selection want different
numbers here. This measures MAE, because the MAE gap against the line is
what it was written to close; the mean error each choice leaves behind is
printed alongside so the trade is visible rather than implied.

    python scripts/dev/level_offset.py
    python scripts/dev/level_offset.py --folds 4 6 8 10
    python scripts/dev/level_offset.py --game cs2 --stat kills
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimize_weights as ow
# Reused rather than reimplemented: it parses the weights out of
# src/app.jsx (so this measures what actually ships) and warns when
# optimize_weights' mirrored copy has drifted from it.
from diagnose_calibration import shipped_weights

GAMES = {"lol": "data.json", "valorant": "valorant_data.json", "cs2": "cs2_data.json"}

# A projection below zero is not a projection. The offsets in play are
# small enough that this almost never binds, but "almost never" is not a
# reason to emit a negative kill count.
FLOOR = 0.0


def offsets(train):
    """Both estimators of "how far high is the model", from training rows."""
    errs = [p - a for _, _, p, a in train]
    if not errs:
        return {}
    return {"median": statistics.median(errs), "mean": statistics.fmean(errs)}


def score(rows, off):
    """MAE and mean signed error over rows with `off` subtracted."""
    if not rows:
        return None, None
    adj = [(max(p - off, FLOOR), a) for _, _, p, a in rows]
    mae = sum(abs(p - a) for p, a in adj) / len(adj)
    bias = sum(p - a for p, a in adj) / len(adj)
    return mae, bias


def evaluate(rows, folds):
    """Walk-forward: each fold scored with an offset fitted only on rows
    that predate it. Returns {name: (mean % change, folds won, n, mean
    offset, resulting mean error)}."""
    out = {}
    for name in ("median", "mean"):
        base_maes, adj_maes, used, biases = [], [], [], []
        for lo, hi in folds:
            train = [r for r in rows if r[1] < lo]
            test = ow.window_rows(rows, lo, hi)
            if not train or not test:
                continue  # the first fold has no past to fit on: skipped, not fudged
            off = offsets(train).get(name)
            if off is None:
                continue
            b, _ = score(test, 0.0)
            c, bias = score(test, off)
            base_maes.append(b)
            adj_maes.append(c)
            biases.append(bias)
            used.append(off)
        if not base_maes:
            out[name] = None
            continue
        mb, mc = statistics.fmean(base_maes), statistics.fmean(adj_maes)
        wins = sum(1 for b, c in zip(base_maes, adj_maes) if c < b)
        out[name] = (100.0 * (mc - mb) / mb, wins, len(base_maes),
                     statistics.fmean(used), statistics.fmean(biases))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", action="append", choices=sorted(GAMES))
    ap.add_argument("--stat", action="append")
    ap.add_argument("--folds", type=int, nargs="+", default=[6],
                    help="fold counts to check; a fix that only holds at one "
                         "boundary is a fact about the boundary")
    args = ap.parse_args()

    table = shipped_weights()
    games = args.game or ["cs2", "valorant", "lol"]
    stats = args.stat or ["kills", "deaths", "assists", "headshots"]

    for game in games:
        path = GAMES[game]
        if not os.path.exists(path):
            print(f"=== {game}: {path} missing, skipped\n")
            continue
        region_data = ow.load_region_data(path)
        print(f"=== {game} {'=' * 58}")
        for stat in stats:
            if not ow.stat_applies_to(stat, game):
                continue
            weights = dict(table[game][stat])
            rows = ow.collect_predictions_with_dates(region_data, stat, weights)
            if not rows:
                print(f"  {stat:<10} (no rows)")
                continue
            base_all, bias_all = score(rows, 0.0)
            print(f"  {stat:<10} n={len(rows):5d}  MAE {base_all:.4f}  "
                  f"mean err {bias_all:+.3f}")
            for k in args.folds:
                folds = ow.fold_boundaries([r[1] for r in rows], k)
                res = evaluate(rows, folds)
                for name in ("median", "mean"):
                    r = res.get(name)
                    if r is None:
                        print(f"      {k:2d} folds  {name:<6}  (not scoreable)")
                        continue
                    change, wins, n, off, bias = r
                    verdict = "ADOPT" if wins * 2 > n and change < 0 else ""
                    print(f"      {k:2d} folds  {name:<6}  offset {off:+.3f}  "
                          f"{change:+.2f}%  {wins}/{n} folds  "
                          f"leaves mean err {bias:+.3f}  {verdict}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
