"""Is the model systematically HIGH or LOW? (MAE cannot tell you.)

Every weight in this repo was chosen by minimising MAE. MAE is blind to
sign: a model that is 8% low on every single prediction and a model that
is 8% off in random directions score identically. That matters here for
one specific reason — a projection is only useful next to a posted line,
and a constant offset turns into a constant "under" recommendation on
every player, which looks like a signal and is actually a ruler with the
wrong zero.

This reports the SIGNED error of the same point-in-time backtest the
optimizer uses, so the two numbers can be read together:

  mean error   predicted minus actual, in kills/deaths/assists per
               series. Negative = the model under-predicts.
  ratio        mean(predicted) / mean(actual). 1.00 is calibrated. This
               is the number that matters for lines, because lines scale
               with the stat: being 0.5 kills low on a 30-kill line is
               not the same problem as being 0.5 low on a 6-kill one.
  under%       share of rows predicted below the actual. A calibrated
               model sits near 50%; a model at 90% is not "conservative",
               it is wrong in a fixable direction.

Weights are read out of src/app.jsx rather than duplicated here, so this
always measures what actually ships.

  python scripts/dev/diagnose_calibration.py
  python scripts/dev/diagnose_calibration.py --game cs2 --stat kills
"""
import argparse
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimize_weights as ow  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GAMES = {"lol": "data.json", "valorant": "valorant_data.json", "cs2": "cs2_data.json"}


def shipped_weights():
    """Parse DEFAULT_WEIGHTS_BY_GAME_AND_STAT out of src/app.jsx.

    Read rather than copied: a duplicated table would silently stop
    describing the shipped model the first time one is retuned, and a
    calibration report measuring last month's weights is worse than none.
    """
    src = open(os.path.join(ROOT, "src/app.jsx"), encoding="utf-8").read()
    start = src.index("const DEFAULT_WEIGHTS_BY_GAME_AND_STAT = {")
    brace = src.index("{", start)
    depth, end = 0, brace
    for end in range(brace, len(src)):
        if src[end] == "{":
            depth += 1
        elif src[end] == "}":
            depth -= 1
            if depth == 0:
                break
    body = src[brace:end + 1]
    body = re.sub(r"//[^\n]*", "", body)          # comments
    body = re.sub(r",(\s*[}\]])", r"\1", body)    # trailing commas
    body = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', body)
    table = json.loads(body)

    # optimize_weights.py keeps its own hand-mirrored copy of this table.
    # Two copies of the same numbers is exactly the setup where one gets
    # retuned and the other quietly keeps reporting on a model nobody
    # runs, so say so loudly rather than measuring the stale one.
    for game, stats in getattr(ow, "SHIPPED_WEIGHTS", {}).items():
        for stat, mirrored in stats.items():
            live = table.get(game, {}).get(stat)
            if live is None:
                continue
            drift = {k: (v, live.get(k)) for k, v in mirrored.items() if live.get(k) != v}
            if drift:
                print(f"WARNING  optimize_weights.SHIPPED_WEIGHTS[{game!r}][{stat!r}] "
                      f"disagrees with src/app.jsx: {drift} (app.jsx wins here)",
                      file=sys.stderr)
    return table


def rows_for(game, stat, weights, regions=None):
    path = os.path.join(ROOT, GAMES[game])
    region_data = ow.load_region_data(path, regions)
    ow.clear_point_in_time_caches()
    return ow.collect_predictions(region_data, stat, weights)


def report(label, rows):
    if not rows:
        print(f"  {label:22s}  (no rows)")
        return
    errs = [p - a for _, p, a in rows]
    pred = statistics.fmean(p for _, p, _ in rows)
    act = statistics.fmean(a for _, _, a in rows)
    under = sum(1 for e in errs if e < 0)
    mae = statistics.fmean(abs(e) for e in errs)
    print(f"  {label:22s}  n={len(rows):5d}  mean err {statistics.fmean(errs):+7.3f}"
          f"  median {statistics.median(errs):+7.3f}  ratio {pred / act:5.3f}"
          f"  under {100 * under / len(rows):5.1f}%   MAE {mae:6.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=list(GAMES) + ["all"], default="all")
    ap.add_argument("--stat", choices=list(ow.STAT_TYPES) + ["all"], default="all")
    # Derived, not listed: a stat added to STAT_TYPES is immediately
    # available here rather than failing on an "invalid choice" from a
    # copy of the list that nobody remembered to update.
    ap.add_argument("--by-region", action="store_true",
                    help="break each stat down by region, to separate a "
                         "model-wide offset from one bad region's data")
    args = ap.parse_args()

    table = shipped_weights()
    games = list(GAMES) if args.game == "all" else [args.game]
    stats = list(ow.STAT_TYPES) if args.stat == "all" else [args.stat]

    for game in games:
        print(f"\n=== {game} " + "=" * 56)
        for stat in stats:
            weights = dict(table[game][stat])
            weights.setdefault("careerRamp", 0)
            rows = rows_for(game, stat, weights)
            report(stat, rows)
            if not args.by_region:
                continue
            by = {}
            for region, p, a in rows:
                by.setdefault(region, []).append((region, p, a))
            for region in sorted(by):
                report(f"    {region}", by[region])


if __name__ == "__main__":
    main()
