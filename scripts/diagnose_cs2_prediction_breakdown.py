#!/usr/bin/env python3
"""
Breaks a real prediction down into its actual component parts (pt_rate,
career_rate, the blended base, opp_mult, kp_mult, final total) for a
sample of REAL recent matches, compared against the real actual outcome
-- rather than theorizing further about what MIGHT be causing the
under-prediction reported live in the app, this shows exactly which
component is responsible.

Usage:
    python scripts/diagnose_cs2_prediction_breakdown.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from optimize_weights import (  # noqa: E402
    load_region_data, recency_weighted_rate, point_in_time_cs2_career_rate,
    resolve_opponent_multiplier, kp_multiplier, get_actual_stat, STAT_TYPES,
)

CS2_DATA_PATH = "cs2_data.json"

# The real, currently-live CS2 kills weights (kill-projector.jsx's
# DEFAULT_WEIGHTS_BY_GAME_AND_STAT.cs2.kills) -- update here if those
# change, or this diagnostic silently evaluates a stale configuration.
WEIGHTS = {"history": 0.3, "opponent": 0.2, "kp": 0.1, "recencyHalfLife": 2, "patchDiscount": 0.4, "career": 1.0}
SAMPLE_SIZE = 10


def main():
    region_data = load_region_data(CS2_DATA_PATH)
    cs2 = region_data.get("CS2")
    if not cs2:
        print("! No CS2 region found", file=sys.stderr)
        sys.exit(1)

    teams = cs2["teams"]
    past_matches = cs2["past_matches"]
    cfg = STAT_TYPES["kills"]

    # Most recent matches first -- want the SAME kind of matches the
    # screenshot showed (current, live predictions), not old history.
    recent_matches = sorted(past_matches, key=lambda m: m.get("date") or "", reverse=True)

    shown = 0
    for m in recent_matches:
        if shown >= SAMPLE_SIZE:
            break
        for side in ("teamA", "teamB"):
            if shown >= SAMPLE_SIZE:
                break
            team = m[side]
            opp = m["teamB"] if side == "teamA" else m["teamA"]
            if team not in teams:
                continue
            for player in teams[team]["players"]:
                if shown >= SAMPLE_SIZE:
                    break
                actual = get_actual_stat(m, team, player["name"], cfg["key"])
                if actual is None or actual == "unavailable":
                    continue

                cutoff = m["date"]
                pt_rate, pt_games = recency_weighted_rate(
                    past_matches, team, player["name"], cfg["key"], cutoff,
                    WEIGHTS["recencyHalfLife"], None, WEIGHTS["patchDiscount"]
                )
                hist_rate = player["hist"][cfg["key"]] if player.get("hist") else None
                base_before_career = pt_rate if pt_rate is not None else (hist_rate if hist_rate is not None else player["cur"][cfg["key"]])

                career_rate = point_in_time_cs2_career_rate(player, cfg["key"], cutoff) if "career_games" in player else None

                base_after_career = base_before_career
                if career_rate is not None and WEIGHTS["career"] > 0:
                    base_after_career = WEIGHTS["career"] * career_rate + (1 - WEIGHTS["career"]) * base_before_career

                opp_mult = resolve_opponent_multiplier(teams, past_matches, player, opp, WEIGHTS["opponent"], cfg, cutoff)
                kp_mult = kp_multiplier(player, WEIGHTS["history"], WEIGHTS["kp"]) if cfg["useKP"] else 1.0

                per_game = base_after_career * opp_mult * kp_mult
                games_in_match = m.get("games", 2)
                total = per_game * games_in_match

                print(f"--- {player['name']} ({team}) vs {opp} on {cutoff} ---")
                print(f"  pt_rate (recent form, per-map):     {pt_rate}")
                print(f"  hist_rate:                          {hist_rate}  (always None for CS2)")
                print(f"  cur (season flat avg, per-map):     {player['cur'][cfg['key']]}")
                print(f"  base BEFORE career blend:           {base_before_career:.2f}")
                print(f"  career_rate (point-in-time):        {career_rate}")
                print(f"  base AFTER career blend (career={WEIGHTS['career']}):  {base_after_career:.2f}")
                print(f"  opp_mult:                            {opp_mult:.3f}")
                print(f"  kp_mult:                              {kp_mult:.3f}")
                print(f"  per_game (base*opp*kp):              {per_game:.2f}")
                print(f"  games_in_match:                      {games_in_match}")
                print(f"  PREDICTED TOTAL:                     {total:.2f}")
                print(f"  ACTUAL TOTAL:                        {actual}")
                print(f"  DIFF:                                {actual - total:+.2f}\n")
                shown += 1


if __name__ == "__main__":
    main()