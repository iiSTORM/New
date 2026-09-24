#!/usr/bin/env python3
"""Every estimator the model could use, scored on identical rows.

Built to settle whether CS2 should learn about ROUNDS. The reasoning was
good: CS2 kills are roughly rate-per-round times rounds played, rounds
swing from 16 to 30-odd, and nothing in the pipeline stores them --
bo3.gg has the map scores and the scraper drops them. It looked like the
one lever left with a real mechanism.

It is already in the model. share_tier_rate is the player's SHARE of
their team's output times league pace, which is pace-normalised by
construction and does not need a round count to be it. And measured
against everything else on the same rows, it is the weakest thing
available:

    n=2660 rows where every estimator is computable

    model              corr 0.276   MAE 6.2989
    career x maps      corr 0.254   MAE 6.4741
    share tier         corr 0.208   MAE 6.6830
    plain mean         corr 0.210   MAE 6.9797
    recency-weighted   corr 0.207   MAE 7.0695

The blend beats every component it is built from, on both measures. So
there is no single tier being wasted, and the pace idea is not missing --
it is present, weighted zero for kills, and weighted zero because a
sweep said so.

WHY THE ROW SET IS THE WHOLE METHOD HERE. Measuring these separately
gives wildly different answers: the same "plain mean of prior matches"
scores 0.412 if same-day matches may serve as each other's history
(CS2 dates are day-resolution, so sorting leaks), 0.299 with a strict
cutoff and its own row set, and 0.210 here. Only the last is comparable
to anything, because only here is every estimator being asked about the
same matches. Two of today's wrong answers came from skipping that.

The remaining round-shaped idea is EXPECTED rounds for the fixture --
a mismatch is a short match -- but that is an opponent-strength term,
and CS2's opponent weight is 0.0 by repeated measurement.

Usage:
    python scripts/dev/component_strength.py --game cs2 --stat kills
"""
import argparse
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow
import sweep_weight as sw


def corr(xs, ys):
    if len(xs) < 50:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs) / (sx * sy) if sx and sy else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="cs2", choices=list(sw.SOURCES))
    ap.add_argument("--stat", default="kills", choices=["kills", "deaths", "assists"])
    args = ap.parse_args()

    data = ow.load_region_data(sw.SOURCES[args.game])
    weights = dict(ow.SHIPPED_WEIGHTS[args.game][args.stat])
    weights.setdefault("careerRamp", 0)
    key = ow.STAT_TYPES[args.stat]["key"]

    for region_key, rd in data.items():
        teams, scored = rd.get("teams", {}), rd.get("past_matches", [])
        if not teams or len(scored) < 50:
            continue
        pool = ow.history_pool(rd)

        prior_by_player = defaultdict(list)
        for m in pool:
            for team, players in (m.get("actual") or {}).items():
                if not isinstance(players, dict):
                    continue
                for name, row in players.items():
                    if isinstance(row, dict) and row.get(key) is not None:
                        prior_by_player[(team, name)].append(
                            (m["date"], row[key] / (m.get("games", 2) or 2)))

        cols, actuals = defaultdict(list), []
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
                    cutoff, maps = match["date"], match.get("maps_counted", 2)
                    model, _ = ow.project_point_in_time(
                        pool, teams, player, team, opp, maps, weights,
                        cutoff, args.stat, match.get("patch"))
                    career = (ow.point_in_time_cs2_career_rate(player, key, cutoff)
                              if args.game == "cs2" else
                              ((player.get("career") or {}).get(key)))
                    recency, _ = ow.recency_weighted_rate(
                        pool, team, player["name"], key, cutoff,
                        weights["recencyHalfLife"], None, weights["patchDiscount"])
                    share = ow.share_tier_rate(pool, teams, team, player["name"], key, cutoff)
                    earlier = [v for d, v in prior_by_player[(team, player["name"])] if d < cutoff]
                    plain = statistics.mean(earlier) if len(earlier) >= 2 else None
                    if None in (model, career, recency, share, plain):
                        continue
                    cols["model"].append(model)
                    cols["career x maps"].append(career * maps)
                    cols["recency-weighted"].append(recency * maps)
                    cols["share tier"].append(share * maps)
                    cols["plain mean"].append(plain * maps)
                    actuals.append(actual)

        if len(actuals) < 50:
            continue
        print(f"\n{args.game}/{region_key}/{args.stat}  "
              f"n={len(actuals)} rows where every estimator is computable")
        for name in ("model", "career x maps", "recency-weighted", "share tier", "plain mean"):
            mae = statistics.mean(abs(x - y) for x, y in zip(cols[name], actuals))
            print(f"    {name:18} corr {corr(cols[name], actuals):.3f}   MAE {mae:.4f}")


if __name__ == "__main__":
    main()
