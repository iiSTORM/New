#!/usr/bin/env python3
"""Does a thin sample actually predict worse, and from where?

The app is about to tell people how much evidence sits behind a
projection. Before it can, that has to be a real distinction rather than
a decoration: if a projection off 6 games scores the same as one off 40,
labelling it is worse than saying nothing, because it spends the
reader's attention on noise.

So this buckets out-of-sample error by how much evidence each row had at
the time it was predicted, and the thresholds the UI uses come from
where the error actually turns rather than from a round number.

"Evidence" is weighted by which tier the projection leans on. CS2 kills
is career 1.0, so its evidence is the career log, not the match history
-- a CS2 player with 6 matches on file and 46 career games is not a thin
sample for kills, and a measure that called it one would be lying in the
opposite direction.

Run: python scripts/dev/evidence_vs_accuracy.py
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow


def career_games_before(player, cutoff_date):
    """How many career games this player had on the day of the match.

    Mirrors careerGameCount() in src/app.jsx, which the UI reads -- if
    these disagree, the thresholds are measured against a different
    quantity than the one being labelled, which is worse than having no
    thresholds at all.

    CS2 stores a dated per-game log, so this is exact. LoL stores an
    aggregate carrying only a total (career.g, typically in the
    hundreds), which cannot be cut at a date; it is used as-is, and the
    effect is that LoL rows land in the top bucket, which is true of
    them. Reading only the CS2 log left every LoL row bucketed by 40% of
    its match history with a 430-game career ignored, which put them in
    the thin buckets and made LoL look insensitive to evidence.
    """
    games = player.get("career_games")
    if games:
        return sum(1 for g in games if (g.get("date") or "")[:10] < cutoff_date)
    career = player.get("career")
    if isinstance(career, dict) and isinstance(career.get("g"), (int, float)):
        return career["g"]
    return 0


def evidence_for(player, prior_games, cutoff_date, weights):
    """Games of evidence behind one projection, weighted by tier.

    A blend rather than a max: with career at 1.0 the career log is the
    whole story, with career at 0 the match history is, and the shipped
    tables sit at both ends and in between.
    """
    cw = weights.get("career", 0.0) or 0.0
    career = career_games_before(player, cutoff_date) if cw > 0 else 0
    return cw * career + (1 - cw) * prior_games


def rows_with_evidence(region_data, stat_type, weights):
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
                    predicted, prior_games = ow.project_point_in_time(
                        past, teams, player, team, opp,
                        match.get("maps_counted", 2), weights,
                        match["date"], stat_type, match.get("patch"))
                    if predicted is None:
                        continue
                    if prior_games == 0 and not player.get("hist"):
                        continue
                    out.append((evidence_for(player, prior_games, match["date"], weights),
                                abs(predicted - actual), actual))
    return out


BUCKETS = [(0, 4), (4, 8), (8, 12), (12, 20), (20, 35), (35, 10**6)]


def report(game, stat, rows):
    if not rows:
        print(f"  {game} {stat}: no rows")
        return
    overall = statistics.mean(e for _, e, _ in rows)
    print(f"\n  {game} {stat}  (n={len(rows)}, overall MAE {overall:.3f})")
    print(f"    {'evidence':>12}  {'n':>5}  {'MAE':>7}  {'vs overall':>10}  {'mean actual':>11}")
    for lo, hi in BUCKETS:
        sel = [(e, a) for ev, e, a in rows if lo <= ev < hi]
        if not sel:
            continue
        m = statistics.mean(e for e, _ in sel)
        act = statistics.mean(a for _, a in sel)
        label = f"{lo}-{hi}g" if hi < 10**6 else f"{lo}+g"
        print(f"    {label:>12}  {len(sel):5}  {m:7.3f}  {100 * (m / overall - 1):+9.1f}%  {act:11.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=["lol", "valorant", "cs2", "all"], default="all")
    args = ap.parse_args()

    sources = {
        "lol": "data.json",
        "valorant": "valorant_data.json",
        "cs2": "cs2_data.json",
    }
    games = list(sources) if args.game == "all" else [args.game]
    for game in games:
        data = ow.load_region_data(sources[game])
        if not data:
            print(f"{game}: no data at {sources[game]}")
            continue
        print(f"\n=== {game} " + "=" * 50)
        for stat in ow.STAT_TYPES:
            if not ow.stat_applies_to(stat, game):
                continue
            weights = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
            if not weights:
                continue
            report(game, stat, rows_with_evidence(data, stat, weights))


if __name__ == "__main__":
    main()
