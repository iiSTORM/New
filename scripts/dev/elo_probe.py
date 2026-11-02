#!/usr/bin/env python3
"""Does a team's Elo explain what the projection and the line both miss?

The model already adjusts for the opponent, at team level, by how many kills
they give up. What it does not carry is who is likely to WIN, and in LoL and
Dota a winning team's players take more kills and die less. Elo is the cheap
way to have that: one rating per team, updated map by map, point in time.

For every graded standard prop:

    left = actual - the slate tool's realised mean (shrunk toward the line)
    diff = team Elo - opponent Elo, from maps played before the match

and per (game, stat), left / line is regressed on diff / 400, fitted only on
earlier matches and scored on later ones. Ships only if the held-out error
falls and the slope's bootstrap interval, clustered on the match, excludes 0.

    python scripts/dev/elo_probe.py
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

from propedge import esports                       # noqa: E402
import series_score                                # noqa: E402

K = 24
START = 1500.0
FOLDS = 5
BOOTSTRAP = 2000


def norm(name):
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def map_results(blob_matches):
    """[(date, a, b, maps a won, maps b won)], one per series, deduplicated."""
    seen, out = set(), []
    for m in blob_matches:
        a, b = norm(m.get("teamA")), norm(m.get("teamB"))
        date = str(m.get("date") or "")[:10]
        # turned to read teamA-teamB: CS2's stored scores were half backwards
        score = str(series_score.orient(m.get("score"), m.get("teamA"), m.get("teamB"),
                                        m.get("winner")) or "")
        if not a or not b or not date or "-" not in score:
            continue
        try:
            wa, wb = (int(x) for x in score.split("-")[:2])
        except ValueError:
            continue
        if wa + wb == 0:
            continue
        key = (date, tuple(sorted((a, b))))
        if key in seen:
            continue
        seen.add(key)
        out.append((date, a, b, wa, wb))
    out.sort()
    return out


def elo_history(series, k=K):
    """{team: [(date, rating before that date's maps)]} plus final ratings,
    so a lookup on date D sees only maps played before D."""
    rating = collections.defaultdict(lambda: START)
    history = collections.defaultdict(list)
    for date, a, b, wa, wb in series:
        for team in (a, b):
            if not history[team] or history[team][-1][0] != date:
                history[team].append((date, rating[team]))
        for won_a in [1] * wa + [0] * wb:
            exp_a = 1 / (1 + 10 ** ((rating[b] - rating[a]) / 400))
            rating[a] += k * (won_a - exp_a)
            rating[b] -= k * (won_a - exp_a)
    return history, rating


def rating_before(history, rating, team, date):
    rows = history.get(team)
    if not rows:
        return None
    for d, r in rows:                   # first entry on or after date: its "before"
        if d >= date:
            return r
    return rating[team]


def load_series(game, root="."):
    path = os.path.join(root, esports.SOURCES[game])
    if not os.path.exists(path):
        return []
    regions = json.load(open(path)).get("regions") or {}
    matches = []
    for blob in regions.values():
        matches += (blob.get("past_matches") or []) + (blob.get("history_matches") or [])
    return map_results(matches)


def collect(results_path, root="."):
    graded = json.load(open(results_path))["graded"]
    model = esports.EsportsModel.from_files(root, graded)
    elo = {g: elo_history(load_series(g, root)) for g in esports.GAMES}
    rows = []
    for row in graded:
        if (row.get("odds_type") or "standard") != "standard" or row.get("actual") is None:
            continue
        game, stat, maps, line = row.get("game"), row.get("stat"), row.get("maps"), row.get("line")
        if game not in esports.GAMES or not isinstance(maps, int) or line is None:
            continue
        mean = model.project_mean(game, row.get("player"), stat, maps, row.get("team"),
                                  row.get("opponent"), row.get("match_date"))
        if mean is None or mean[1] < esports.MIN_MAPS:
            continue
        history, final = elo[game]
        date = str(row.get("match_date"))
        ra = rating_before(history, final, norm(row.get("team")), date)
        rb = rating_before(history, final, norm(row.get("opponent")), date)
        if ra is None or rb is None:
            continue
        mu, evidence = mean
        shrunk = esports.shrink_toward_line(mu, line, evidence)
        realised = line + (shrunk - line) * esports.line_realization(game, stat)
        rows.append({"game": game, "stat": stat, "line": line, "actual": row["actual"],
                     "mean": realised, "diff": (ra - rb) / 400, "when": date,
                     "match": (date, tuple(sorted((norm(row.get("team")), norm(row.get("opponent"))))))})
    rows.sort(key=lambda r: r["when"])
    return rows


def fit(rows):
    """Least-squares slope of (actual - mean) / line on diff."""
    den = sum(r["diff"] ** 2 for r in rows)
    return sum(r["diff"] * (r["actual"] - r["mean"]) / r["line"] for r in rows) / den if den else 0.0


def interval(rows, seed=1):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["match"]].append(r)
    groups = list(by.values())
    rng = random.Random(seed)
    draws = sorted(fit([r for g in rng.choices(groups, k=len(groups)) for r in g])
                   for _ in range(BOOTSTRAP))
    return draws[int(0.025 * BOOTSTRAP)], draws[int(0.975 * BOOTSTRAP) - 1], len(groups)


def walk_forward(rows, folds=FOLDS):
    """(MAE of the mean, MAE with Elo, side hit-rate of each) on later matches."""
    matches = sorted({r["match"] for r in rows})
    size = len(matches) // (folds + 1)
    if size < 5:
        return None
    order = {m: i for i, m in enumerate(matches)}
    base, elo, hit0, hit1 = [], [], [], []
    for i in range(1, folds + 1):
        lo, hi = size * i, (size * (i + 1) if i < folds else len(matches))
        test = [r for r in rows if lo <= order[r["match"]] < hi]
        first = min(r["when"] for r in test)
        b = fit([r for r in rows if r["when"] < first])
        for r in test:
            adj = r["mean"] + b * r["diff"] * r["line"]
            base.append(abs(r["actual"] - r["mean"]))
            elo.append(abs(r["actual"] - adj))
            if r["actual"] != r["line"]:
                over = r["actual"] > r["line"]
                hit0.append((r["mean"] > r["line"]) == over)
                hit1.append((adj > r["line"]) == over)
    return (statistics.mean(base), statistics.mean(elo),
            sum(hit0) / len(hit0), sum(hit1) / len(hit1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    rows = collect(args.results, args.root)
    out = []
    print(f"  {'cell':<16} {'n':>5} {'matches':>7} {'slope':>7}  {'95%':<17}"
          f"  held out MAE: model / +Elo   side hit: model / +Elo")
    for game, stat in sorted({(r["game"], r["stat"]) for r in rows}):
        cell = [r for r in rows if r["game"] == game and r["stat"] == stat]
        if len(cell) < 100:
            continue
        lo, hi, m = interval(cell)
        wf = walk_forward(cell)
        got = {"game": game, "stat": stat, "n": len(cell), "matches": m,
               "slope": fit(cell), "low": lo, "high": hi}
        if wf:
            got.update(dict(zip(("mae_model", "mae_elo", "hit_model", "hit_elo"), wf)))
        got["ships"] = bool(wf and (lo > 0 or hi < 0) and wf[1] < wf[0])
        out.append(got)
        print(f"  {game + ' ' + stat:<16} {len(cell):>5} {m:>7} {got['slope']:>+7.3f}  "
              f"{lo:+.3f} to {hi:+.3f}"
              + (f"   {wf[0]:.3f} / {wf[1]:.3f}        {wf[2]:.1%} / {wf[3]:.1%}" if wf else "")
              + ("   SHIPS" if got["ships"] else ""))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"k": K, "cells": out}, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
