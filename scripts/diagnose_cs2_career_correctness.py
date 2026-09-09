#!/usr/bin/env python3
"""
Two real checks before trusting CS2's career:1.0/0.95 finding:

1. Direct verification that point_in_time_cs2_career_rate's date
   filtering behaves correctly against REAL merged data, not just the
   synthetic example already tested -- picks a handful of real
   historical matches and prints exactly which career_games get
   included/excluded for a real player, so this can be visually
   confirmed rather than trusted on logic alone.

2. Checks whether career_rate and the model's EXISTING pt_rate signal
   (recency_weighted_rate against past_matches) are highly correlated
   for the same player/date -- if they are, that supports "career_rate
   is just a more complete version of the same underlying signal"
   (career_data.json's direct player-scoped bo3.gg query has better
   coverage than this app's own team-discovery-limited past_matches)
   rather than a hidden leakage mechanism nobody's found yet.

Usage:
    python scripts/diagnose_cs2_career_correctness.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from optimize_weights import (  # noqa: E402
    load_region_data, recency_weighted_rate, point_in_time_cs2_career_rate, STAT_TYPES,
)

CS2_DATA_PATH = "cs2_data.json"


def main():
    region_data = load_region_data(CS2_DATA_PATH)
    cs2 = region_data.get("CS2")
    if not cs2:
        print("! No CS2 region found", file=sys.stderr)
        sys.exit(1)

    teams = cs2["teams"]
    past_matches = cs2["past_matches"]
    cfg = STAT_TYPES["kills"]

    # --- Check 1: manual inspection of real date filtering ---
    print("=== CHECK 1: real date filtering, a few actual matches ===\n")
    samples_shown = 0
    for m in sorted(past_matches, key=lambda x: x.get("date") or "")[10:]:  # skip the very earliest few, want matches with real prior history to filter against
        if samples_shown >= 3:
            break
        for side in ("teamA", "teamB"):
            team = m[side]
            if team not in teams:
                continue
            for player in teams[team]["players"]:
                if not player.get("career_games"):
                    continue
                cutoff = m["date"]
                games = player["career_games"]
                included = [g for g in games if g.get("date") and g["date"] < cutoff]
                excluded = [g for g in games if g.get("date") and g["date"] >= cutoff]
                if not excluded:
                    continue  # want to see a real case where exclusion actually matters
                print(f"Match: {m['teamA']} vs {m['teamB']} on {cutoff}")
                print(f"  Player: {player['name']} ({team})")
                print(f"  Total career_games: {len(games)}")
                print(f"  Included (date < {cutoff}): {len(included)} -- most recent 3: "
                      f"{sorted([g['date'] for g in included], reverse=True)[:3]}")
                print(f"  Excluded (date >= {cutoff}): {len(excluded)} -- earliest 3: "
                      f"{sorted([g['date'] for g in excluded])[:3]}")
                rate = point_in_time_cs2_career_rate(player, cfg["key"], cutoff)
                print(f"  Resulting point-in-time career kill rate: {rate}\n")
                samples_shown += 1
                break
            if samples_shown >= 3:
                break

    # --- Check 2: correlation between career_rate and pt_rate ---
    print("\n=== CHECK 2: does career_rate correlate with the model's EXISTING pt_rate signal? ===\n")
    pairs = []
    for m in past_matches:
        cutoff = m.get("date")
        if not cutoff:
            continue
        for side in ("teamA", "teamB"):
            team = m[side]
            if team not in teams:
                continue
            for player in teams[team]["players"]:
                if not player.get("career_games"):
                    continue
                career_rate = point_in_time_cs2_career_rate(player, cfg["key"], cutoff)
                pt_rate, pt_games = recency_weighted_rate(
                    past_matches, team, player["name"], cfg["key"], cutoff,
                    half_life=6, reference_patch=None, patch_discount=0.4
                )
                if career_rate is not None and pt_rate is not None and pt_games >= 4:
                    pairs.append((career_rate, pt_rate))

    if len(pairs) < 10:
        print(f"Only {len(pairs)} usable pairs found — not enough to say much.")
        return

    n = len(pairs)
    mean_c = sum(c for c, _ in pairs) / n
    mean_p = sum(p for _, p in pairs) / n
    cov = sum((c - mean_c) * (p - mean_p) for c, p in pairs) / n
    std_c = (sum((c - mean_c) ** 2 for c, _ in pairs) / n) ** 0.5
    std_p = (sum((p - mean_p) ** 2 for _, p in pairs) / n) ** 0.5
    correlation = cov / (std_c * std_p) if std_c > 0 and std_p > 0 else None

    print(f"n={n} player-date pairs with both signals available")
    print(f"mean career_rate={mean_c:.2f}, mean pt_rate={mean_p:.2f}")
    print(f"correlation between career_rate and pt_rate: {correlation:.3f}" if correlation is not None else "correlation: undefined (zero variance)")
    if correlation is not None:
        if correlation > 0.6:
            print("\n>>> STRONG correlation — consistent with career_rate being a more complete/cleaner")
            print("    version of the SAME underlying recent-form signal, not a separate leaked signal.")
        elif correlation > 0.3:
            print("\n>>> MODERATE correlation — plausible overlap, but career_rate may be capturing")
            print("    something meaningfully different too. Worth more investigation before fully trusting.")
        else:
            print("\n>>> WEAK correlation — career_rate and pt_rate look like largely DIFFERENT signals.")
            print("    This weakens the 'just a cleaner version of the same thing' explanation and")
            print("    warrants more scrutiny before trusting career:1.0 as a final answer.")


if __name__ == "__main__":
    main()