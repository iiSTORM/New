#!/usr/bin/env python3
"""Of the edge the board shows against a LINE, how much actually shows up?

edge_realization.py answers a neighbouring question: of the distance the
model puts between a player and a TYPICAL player, how much materialises.
That is what EDGE_REALIZATION in the app discounts by, and it is close to
1 for well-evidenced players, because the model is good at telling a star
from a role player. The line is good at that too. What a bet needs is the
part of the model's opinion the line does not already have, and nothing
measured that.

For every graded prop, point in time:

    claim    = the board's adjusted projection - the line
    realised = what the player did             - the line
    slope    = sum(claim * realised) / sum(claim^2)

The slope is the least-squares weight on the model when blending it with
the line, and the fraction of a shown edge that is real. 1.0 means the
board's edges are worth their face value; 0 means the line already knew
everything the model did and every edge is noise.

The interval is clustered on the match (ten props off one map share its
pace). The walk-forward check fits the slope on earlier matches and scores
later ones, which is the test of whether applying it would have helped.

    python scripts/dev/line_realization.py
    python scripts/dev/line_realization.py --json scripts/dev/line_realization.json
"""
import argparse
import collections
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import optimize_weights as ow                      # noqa: E402
import search_weights_by_outcome as sw             # noqa: E402

CELLS = [("cs2", "kills"), ("cs2", "headshots"), ("valorant", "kills"),
         ("lol", "kills"), ("lol", "assists"), ("lol", "deaths"),
         ("dota2", "kills"), ("dota2", "assists"), ("dota2", "deaths")]
#: The app's EDGE_REALIZATION, (evidence below, factor). Pinned against
#: src/app.jsx by tests/test_line_realization.py.
EDGE_REALIZATION = [(4, 0.52), (8, 0.72), (12, 0.93), (float("inf"), 0.98)]
FOLDS = 4
MIN_PROPS = 100
BOOTSTRAP = 2000


def edge_multiplier(games):
    if not isinstance(games, (int, float)):
        return EDGE_REALIZATION[0][1]
    for up_to, factor in EDGE_REALIZATION:
        if games < up_to:
            return factor
    return EDGE_REALIZATION[-1][1]


def adjusted(mu, evidence, mean):
    """The app's adjustEdge, as a projection rather than an edge."""
    if mean is None:
        return mu
    return mean + edge_multiplier(evidence) * (mu - mean)


def match_key(row):
    return (str(row.get("match_date")),
            tuple(sorted((str(row.get("team")), str(row.get("opponent") or "?")))))


def board_claim(mu, evidence, mean, line):
    """The Edges board's shown edge, before any LINE_REALIZATION."""
    return adjusted(mu, evidence, mean) - line


def slate_claim(mu, evidence, mean, line):
    """The slate tool's disagreement: propedge.esports.shrink_toward_line."""
    from propedge import esports
    return esports.shrink_toward_line(mu, line, evidence) - line


def cell_points(game, stat, graded, weights=None, claim=board_claim):
    """[(match key, claim, realised)] for one cell, oldest first."""
    data, index = sw.load(game)
    if not data:
        return []
    rows = sw.cell_rows(graded, game, stat)
    weights = weights or ow.SHIPPED_WEIGHTS[game].get(stat)
    if not rows or weights is None:
        return []
    key = ow.STAT_TYPES[stat]["key"]
    rates = {}
    out = []
    for row, mu, evidence in sw.project_all(rows, data, index, game, stat, weights):
        if row.get("actual") is None:
            continue
        found = index.get(str(row.get("player")).lower()) or [(None,)]
        region = found[0][0]
        cache = (region, str(row.get("match_date")))
        if cache not in rates:
            blob = data.get(region) or {}
            rates[cache] = ow.league_player_rate(blob.get("past_matches") or [],
                                                 blob.get("teams") or {}, key,
                                                 row.get("match_date"))
        rate = rates[cache]
        mean = rate * row["maps"] if rate is not None else None
        out.append((match_key(row), claim(mu, evidence, mean, row["line"]),
                    row["actual"] - row["line"]))
    out.sort(key=lambda p: p[0][0])
    return out


def slope(points):
    den = sum(c * c for _, c, _ in points)
    return sum(c * r for _, c, r in points) / den if den else None


def clustered_interval(points, seed=1):
    by_match = collections.defaultdict(list)
    for p in points:
        by_match[p[0]].append(p)
    groups = list(by_match.values())
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [p for g in rng.choices(groups, k=len(groups)) for p in g]
        s = slope(sample)
        if s is not None:
            draws.append(s)
    draws.sort()
    return draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1], len(groups)


def walk_forward(points, folds=FOLDS):
    """(line MAE, board MAE, blended MAE) on held-out matches, the blend's
    weight fitted only on matches before each cut and clamped to [0, 1]."""
    matches = sorted({p[0] for p in points})
    size = len(matches) // (folds + 1)
    if size < 5:
        return None
    line, board, blend = [], [], []
    for i in range(1, folds + 1):
        train_keys = set(matches[:size * i])
        test_keys = set(matches[size * i:] if i == folds else matches[size * i:size * (i + 1)])
        w = slope([p for p in points if p[0] in train_keys])
        w = min(1.0, max(0.0, w)) if w is not None else 1.0
        for _, c, r in (p for p in points if p[0] in test_keys):
            line.append(abs(r))
            board.append(abs(r - c))
            blend.append(abs(r - w * c))
    return statistics.mean(line), statistics.mean(board), statistics.mean(blend)


def measure(game, stat, graded, claim=board_claim):
    points = cell_points(game, stat, graded, claim=claim)
    if len(points) < MIN_PROPS:
        return {"game": game, "stat": stat, "n": len(points)}
    lo, hi, matches = clustered_interval(points)
    wf = walk_forward(points)
    return {"game": game, "stat": stat, "n": len(points), "matches": matches,
            "slope": slope(points), "low": lo, "high": hi,
            "walk_forward": dict(zip(("line", "board", "blend"), wf)) if wf else None}


def shipped_factors(results):
    """{game: {stat: factor}} as the app should carry them: the slope,
    clamped to [0, 1] and rounded, only where the interval excludes 1 AND
    the scaled edge beat the unscaled one out of sample. The second test
    was added on 2026-10-25, when LoL's slate slope fell to 0.43 (interval
    -0.04 to 0.89) on a bad Worlds week while scaling still lost held out
    (2.967 against 2.949)."""
    out = {}
    for got in results:
        if "slope" not in got or got["high"] >= 1:
            continue
        wf = got.get("walk_forward")
        if wf and wf["blend"] >= wf["board"]:
            continue
        out.setdefault(got["game"], {})[got["stat"]] = round(min(1.0, max(0.0, got["slope"])), 2)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--json")
    ap.add_argument("--slate", action="store_true",
                    help="measure the slate tool's shrunk disagreement instead of the board's")
    args = ap.parse_args(argv)
    claim = slate_claim if args.slate else board_claim
    graded = json.load(open(args.results))["graded"]
    out = []
    print(f"  {'cell':<18} {'n':>5} {'matches':>7} {'slope':>6}  {'95%':<14}"
          f"  walk-forward MAE: line / board / blend")
    for game, stat in CELLS:
        got = measure(game, stat, graded, claim)
        out.append(got)
        if "slope" not in got:
            print(f"  {game + ' ' + stat:<18} {got['n']:>5}  too few graded props")
            continue
        wf = got["walk_forward"]
        print(f"  {game + ' ' + stat:<18} {got['n']:>5} {got['matches']:>7} "
              f"{got['slope']:>6.2f}  {got['low']:+.2f} to {got['high']:+.2f}"
              + (f"   {wf['line']:.3f} / {wf['board']:.3f} / {wf['blend']:.3f}" if wf else ""))
    print(f"\n  ship as LINE_REALIZATION: {json.dumps(shipped_factors(out))}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"cells": out, "shipped": shipped_factors(out)}, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
