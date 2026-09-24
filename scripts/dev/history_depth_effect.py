#!/usr/bin/env python3
"""Does more history help? Hold the rows still and find out.

Written because it did not, and because the wrong version of this
measurement was convincing enough to ship on.

THE WRONG VERSION thins past_matches, re-scores, and compares MAE. On
CS2 that gives a clean monotonic curve -- 6.6326 at cap 2 down to 6.4339
at cap 8, -3.0%, still falling at the end. It looks like exactly what
"more history helps" should look like.

It is a row-composition artefact. A row whose player has no prior
history is DROPPED rather than predicted, so thinning the history also
removes rows, and the count moved 1604 -> 2094 underneath the
comparison. What changed was partly which matches were being scored.

THE RIGHT VERSION scores only the rows that are predictable at EVERY
depth, so the population is identical and the only thing varying is how
much history each prediction had. On CS2:

    cs2/kills    n=1335  cap2=6.7220 ... cap8=6.7846   +0.93%
    cs2/deaths   n=1335  cap2=5.0380 ... cap8=5.0686   +0.61%
    cs2/assists  n=1335  cap2=2.9514 ... cap8=2.9742   +0.77%

Deeper history is slightly WORSE, not better. Which is consistent with
the game's other result: pt_games feeds the shrink denominator, so more
history shrinks less, and CS2's MAE prefers heavy shrinkage.

This is the same selection-versus-treatment mistake as reading a gap
between players who HAVE career data and players who lack it as the
value of career data. Both are populations differing, read as an effect.
The fix is the same in both: hold the rows fixed.

Usage:
    python scripts/dev/history_depth_effect.py --game cs2
"""
import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow
import sweep_weight as sw

CAPS = (2, 3, 4, 6, 8)


def capped(ordered, per_team):
    seen, kept = {}, []
    for m in ordered:
        sides = (m.get("teamA"), m.get("teamB"))
        if any(seen.get(t, 0) < per_team for t in sides):
            kept.append(m)
            for t in sides:
                seen[t] = seen.get(t, 0) + 1
    return kept


def errors_at(region, fixed, extra, stat):
    """|error| per row key, with `fixed` scored and `extra` only in the pool."""
    rd = json.loads(json.dumps(region))
    rd["past_matches"] = fixed
    rd["history_matches"] = extra
    weights = dict(ow.SHIPPED_WEIGHTS[errors_at.game][stat])
    weights.setdefault("careerRamp", 0)
    key = ow.STAT_TYPES[stat]["key"]
    pool, teams = ow.history_pool(rd), rd.get("teams", {})
    out = {}
    for match in rd["past_matches"]:
        for side in ("teamA", "teamB"):
            team = match[side]
            opp = match["teamB"] if side == "teamA" else match["teamA"]
            if team not in teams:
                continue
            for player in teams[team]["players"]:
                actual = ow.get_actual_stat(match, team, player["name"], key)
                if actual is None or actual == "unavailable":
                    continue
                predicted, prior = ow.project_point_in_time(
                    pool, teams, player, team, opp, match.get("maps_counted", 2),
                    weights, match["date"], stat, match.get("patch"))
                if predicted is None or (prior == 0 and not player.get("hist")):
                    continue
                out[(match.get("match_id"), team, player["name"])] = abs(predicted - actual)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="cs2", choices=list(sw.SOURCES))
    args = ap.parse_args()
    errors_at.game = args.game

    raw = json.load(open(sw.SOURCES[args.game]))
    for region_key, region in raw.get("regions", {}).items():
        past = region.get("past_matches") or []
        if len(past) < 50:
            continue
        ordered = sorted(past, key=lambda m: m.get("date") or "", reverse=True)
        fixed = capped(ordered, CAPS[0])
        fixed_ids = {m.get("match_id") for m in fixed}

        for stat in ("kills", "deaths", "assists"):
            per_cap, common = {}, None
            for cap in CAPS:
                pool = capped(ordered, cap)
                extra = [m for m in pool if m.get("match_id") not in fixed_ids]
                got = errors_at(region, fixed, extra, stat)
                per_cap[cap] = got
                common = set(got) if common is None else (common & set(got))
            if not common:
                continue
            means = [statistics.mean(per_cap[c][k] for k in common) for c in CAPS]
            print(f"{args.game}/{region_key}/{stat:8} n={len(common):5d}  "
                  + "  ".join(f"cap{c}={v:.4f}" for c, v in zip(CAPS, means))
                  + f"   {CAPS[0]}->{CAPS[-1]} {(means[-1] / means[0] - 1) * 100:+.2f}%")


if __name__ == "__main__":
    main()
