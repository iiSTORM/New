#!/usr/bin/env python3
"""Team kills, then the player's share: does that know something the model doesn't?

The model projects a player from their own per-map rate, adjusted for the
opponent. The alternative splits it in two:

    team kills  = the team's recent kills per map x the opponent's recent
                  kills allowed per map / the league's kills per map
    share       = the player's share of their team's kills, recently
    projection  = share x team kills x maps

which should be better when a team's pace changes (a new jungler, a patch)
and the player's own sample has not caught up. Whether it is better is the
question, and the test is the one LINE_REALIZATION uses: per (game, stat),
regress what the model missed, actual - the slate tool's realised mean, on
how far the share projection sits from that mean. A slope above 0 whose
interval excludes 0, and a lower held-out error with it, means it knows
something; otherwise it is the same information said differently.

Everything is point in time: maps played before the prop's date.

    python scripts/dev/team_share_probe.py
"""
import argparse
import bisect
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

WINDOW = 20            # maps of team history
SHARE_WINDOW = 15      # maps of the player's own share
MIN_SHARE_MAPS = 3
FOLDS = 5
BOOTSTRAP = 2000
STAT_KEY = {"kills": "k", "headshots": "hs", "deaths": "d", "assists": "a"}


def norm(name):
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def load_maps(game, root="."):
    """[(date, team, opponent, {player: {stat: n}})] for every map, both sides."""
    path = os.path.join(root, esports.SOURCES[game])
    if not os.path.exists(path):
        return []
    seen, out = set(), []
    for blob in (json.load(open(path)).get("regions") or {}).values():
        for m in (blob.get("past_matches") or []) + (blob.get("history_matches") or []):
            date = str(m.get("date") or "")[:10]
            for i, one in enumerate(m.get("per_game") or []):
                teams = list(one)
                if len(teams) != 2 or not date:
                    continue
                key = (date, tuple(sorted(norm(t) for t in teams)), i)
                if key in seen:
                    continue
                seen.add(key)
                for side, other in ((teams[0], teams[1]), (teams[1], teams[0])):
                    out.append((date, norm(side), norm(other),
                                {norm(p): s for p, s in (one[side] or {}).items()}))
    out.sort(key=lambda r: r[0])
    return out


class History:
    """Point-in-time lookups over one game's maps."""

    def __init__(self, maps):
        self.by_team = collections.defaultdict(list)
        self.dates = [m[0] for m in maps]
        self.maps = maps
        for row in maps:
            self.by_team[row[1]].append(row)

    def before(self, team, date):
        rows = self.by_team.get(team) or []
        i = bisect.bisect_left([r[0] for r in rows], date)
        return rows[:i]

    def league_rate(self, key, date):
        i = bisect.bisect_left(self.dates, date)
        recent = self.maps[max(0, i - 2000):i]
        totals = [sum((s.get(key) or 0) for s in r[3].values()) for r in recent]
        return statistics.mean(totals) if totals else None

    def projection(self, team, opponent, player, key, maps, date):
        own = self.before(team, date)[-WINDOW:]
        opp = self.before(opponent, date)[-WINDOW:]
        if len(own) < 5 or len(opp) < 5:
            return None
        league = self.league_rate(key, date)
        if not league:
            return None
        team_rate = statistics.mean(sum((s.get(key) or 0) for s in r[3].values()) for r in own)
        # the opponent's allowed: what teams facing them scored
        allowed = []
        for r in opp:
            for other in self.by_team.get(r[2]) or []:
                if other[0] == r[0] and other[2] == opponent:
                    allowed.append(sum((s.get(key) or 0) for s in other[3].values()))
                    break
        if len(allowed) < 5:
            return None
        team_kills = team_rate * statistics.mean(allowed) / league
        mine = [r for r in own if player in r[3]][-SHARE_WINDOW:]
        if len(mine) < MIN_SHARE_MAPS:
            return None
        num = sum((r[3][player].get(key) or 0) for r in mine)
        den = sum(sum((s.get(key) or 0) for s in r[3].values()) for r in mine)
        if not den:
            return None
        return num / den * team_kills * maps


def collect(results_path, root="."):
    graded = json.load(open(results_path))["graded"]
    model = esports.EsportsModel.from_files(root, graded)
    hist = {g: History(load_maps(g, root)) for g in esports.GAMES}
    rows = []
    for row in graded:
        if (row.get("odds_type") or "standard") != "standard" or row.get("actual") is None:
            continue
        game, stat, maps, line = row.get("game"), row.get("stat"), row.get("maps"), row.get("line")
        if game not in esports.GAMES or stat not in STAT_KEY or not isinstance(maps, int) or line is None:
            continue
        mean = model.project_mean(game, row.get("player"), stat, maps, row.get("team"),
                                  row.get("opponent"), row.get("match_date"))
        if mean is None or mean[1] < esports.MIN_MAPS:
            continue
        date = str(row.get("match_date"))
        share = hist[game].projection(norm(row.get("team")), norm(row.get("opponent")),
                                      norm(row.get("player")), STAT_KEY[stat], maps, date)
        if share is None:
            continue
        mu, evidence = mean
        shrunk = esports.shrink_toward_line(mu, line, evidence)
        realised = line + (shrunk - line) * esports.line_realization(game, stat)
        rows.append({"game": game, "stat": stat, "line": line, "actual": row["actual"],
                     "mean": realised, "raw": mu, "share": share, "when": date,
                     "match": (date, tuple(sorted((norm(row.get("team")),
                                                   norm(row.get("opponent"))))))})
    rows.sort(key=lambda r: r["when"])
    return rows


def fit(rows):
    """Weight on (share - mean) that best explains actual - mean."""
    den = sum((r["share"] - r["mean"]) ** 2 for r in rows)
    return sum((r["share"] - r["mean"]) * (r["actual"] - r["mean"]) for r in rows) / den if den else 0.0


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
    matches = sorted({r["match"] for r in rows})
    size = len(matches) // (folds + 1)
    if size < 5:
        return None
    order = {m: i for i, m in enumerate(matches)}
    base, blend, hit0, hit1 = [], [], [], []
    for i in range(1, folds + 1):
        lo, hi = size * i, (size * (i + 1) if i < folds else len(matches))
        test = [r for r in rows if lo <= order[r["match"]] < hi]
        first = min(r["when"] for r in test)
        w = min(1.0, max(0.0, fit([r for r in rows if r["when"] < first])))
        for r in test:
            adj = r["mean"] + w * (r["share"] - r["mean"])
            base.append(abs(r["actual"] - r["mean"]))
            blend.append(abs(r["actual"] - adj))
            if r["actual"] != r["line"]:
                over = r["actual"] > r["line"]
                hit0.append((r["mean"] > r["line"]) == over)
                hit1.append((adj > r["line"]) == over)
    return (statistics.mean(base), statistics.mean(blend),
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
    print(f"  {'cell':<16} {'n':>5} {'matches':>7} {'weight':>7}  {'95%':<17}"
          f"  held out MAE: model / +share   side hit: model / +share")
    for game, stat in sorted({(r["game"], r["stat"]) for r in rows}):
        cell = [r for r in rows if r["game"] == game and r["stat"] == stat]
        if len(cell) < 100:
            continue
        lo, hi, m = interval(cell)
        wf = walk_forward(cell)
        corr = statistics.correlation([r["raw"] for r in cell], [r["share"] for r in cell])
        got = {"game": game, "stat": stat, "n": len(cell), "matches": m, "weight": fit(cell),
               "low": lo, "high": hi, "corr_with_model": corr}
        if wf:
            got.update(dict(zip(("mae_model", "mae_share", "hit_model", "hit_share"), wf)))
        got["ships"] = bool(wf and lo > 0 and wf[1] < wf[0])
        out.append(got)
        print(f"  {game + ' ' + stat:<16} {len(cell):>5} {m:>7} {got['weight']:>+7.3f}  "
              f"{lo:+.3f} to {hi:+.3f}"
              + (f"   {wf[0]:.3f} / {wf[1]:.3f}          {wf[2]:.1%} / {wf[3]:.1%}" if wf else "")
              + f"   r={corr:.2f}" + ("   SHIPS" if got["ships"] else ""))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"window": WINDOW, "share_window": SHARE_WINDOW, "cells": out}, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
