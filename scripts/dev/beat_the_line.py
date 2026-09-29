#!/usr/bin/env python3
"""Can the model predict the outcome better than the posted line does?

THE PRECONDITION FOR EVERYTHING ELSE, and the number this project should be
judged by. Not "does the model make money", not "do its confident picks land" --
those are downstream and both can look fine for the wrong reason. Just: given a
prop, whose number lands closer to what the player actually did, ours or the
book's?

WHY THIS IS THE RIGHT QUESTION. A model that cannot beat the line as a PREDICTOR
has no information the market lacks. Every "edge" it reports is then one of two
things: noise, or the market's own systematic bias reflected back at us. The
second is real money and it is also not a projection of anything -- it is the
observation that this book posts esports lines high, which needs no model and
tells you nothing about the player. Betting it is a legitimate strategy and a
different product.

So this is the gate. Until the model beats the line, an edge it reports is not
evidence about the player, and the honest thing for the app to show is the
projection, the disagreement, and this benchmark saying how much to trust it.

WHERE IT STOOD when this was written, over the whole graded record:

    cs2 kills       836 props   model 5.34   line 5.15   +0.19 behind
    cs2 headshots   727 props   model 3.75   line 3.63   +0.12 behind
    valorant kills  136 props   model 6.08   line 5.67   +0.41 behind

Close, and behind in every cell. Closing 4% on CS2 kills is a realistic target;
it is not a dead end. What it is NOT is a reason to ship an edge.

    python scripts/dev/beat_the_line.py
    python scripts/dev/beat_the_line.py --weights weights.json --json out.json
"""
import argparse
import collections
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import optimize_weights as ow                              # noqa: E402
import search_weights_by_outcome as sw                     # noqa: E402
from propedge import analytics                             # noqa: E402


def compare(game, stat, graded, data, index, weights):
    """(model MAE, line MAE, n, clustered interval on the gap)."""
    rows = sw.cell_rows(graded, game, stat)
    projected = sw.project_all(rows, data, index, game, stat, weights)
    pairs = [(mu, row["line"], row["actual"], row) for row, mu, _ in projected
             if row.get("actual") is not None]
    if len(pairs) < 30:
        return None
    model = statistics.mean(abs(mu - a) for mu, _, a, _ in pairs)
    line = statistics.mean(abs(ln - a) for _, ln, a, _ in pairs)
    # Clustered on the match, because ten props off one map share its pace and
    # counting them as ten independent comparisons overstates the evidence.
    by_match = collections.defaultdict(list)
    for mu, ln, a, row in pairs:
        key = (row.get("match_date"),
               tuple(sorted((str(row.get("team")), str(row.get("opponent") or "?")))))
        by_match[key].append(abs(mu - a) - abs(ln - a))
    per_match = [statistics.mean(v) for v in by_match.values()]
    gap = statistics.mean(per_match)
    spread = (statistics.stdev(per_match) / math.sqrt(len(per_match))
              if len(per_match) > 1 else None)
    return {"game": game, "stat": stat, "n": len(pairs), "matches": len(per_match),
            "model_mae": model, "line_mae": line, "gap": gap,
            "gap_low": gap - 1.96 * spread if spread else None,
            "gap_high": gap + 1.96 * spread if spread else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--weights", help="JSON from search_weights_by_outcome, to compare")
    ap.add_argument("--json", help="write the numbers here")
    args = ap.parse_args(argv)

    graded = json.load(open(args.results))["graded"]
    override = {}
    if args.weights:
        for row in json.load(open(args.weights)):
            override[(row["game"], row["stat"])] = row["found"]

    out = []
    print(f"  {'cell':<22} {'n':>5} {'matches':>8} {'model':>7} {'line':>7} "
          f"{'gap':>8} {'95% on the gap':>18}")
    for game in ("cs2", "valorant", "lol"):
        data, index = sw.load(game)
        if not data:
            continue
        for stat in ow.STAT_TYPES:
            if not ow.stat_applies_to(stat, game):
                continue
            weights = override.get((game, stat)) or ow.SHIPPED_WEIGHTS[game][stat]
            got = compare(game, stat, graded, data, index, weights)
            if not got:
                continue
            out.append(got)
            interval = (f"{got['gap_low']:+.2f} to {got['gap_high']:+.2f}"
                        if got["gap_low"] is not None else "too few matches")
            print(f"  {game + ' ' + stat:<22} {got['n']:>5} {got['matches']:>8} "
                  f"{got['model_mae']:>7.2f} {got['line_mae']:>7.2f} "
                  f"{got['gap']:>+8.2f} {interval:>18}")
    if not out:
        print("  nothing with enough graded props to compare")
        return 1

    ahead = [row for row in out if row["gap"] < 0]
    beaten = [row for row in out if row["gap_high"] is not None and row["gap_high"] < 0]
    print()
    if beaten:
        print(f"  BEATS THE LINE, convincingly, in {len(beaten)} cell(s): "
              + ", ".join(f"{r['game']} {r['stat']}" for r in beaten))
    elif ahead:
        print(f"  ahead in {len(ahead)} cell(s) but none of them convincingly — "
              "every interval still crosses zero")
    else:
        print("  BEHIND THE LINE IN EVERY CELL. The model has no information the "
              "market lacks,")
        print("  so any edge it reports is noise or the market's own bias. Show the "
              "projection,")
        print("  show the disagreement, and do not call it an edge.")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
            f.write("\n")
        print(f"\n  wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
