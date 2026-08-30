#!/usr/bin/env python3
"""
Diagnostic run BEFORE building anything for team cohesion — checks
whether real lineup variation actually exists in the data at all. A
cohesion effect ("do these 5 players perform better together than
their individual averages predict") is only measurable for a team that
has fielded more than one distinct 5-player lineup with a real sample
of games behind each. If most teams played one stable roster all split
with at most a substitution or two, there's genuinely very little to
work with — better to find that out now than force a feature onto data
that can't support it.

No new scraping needed: each past_match's "actual" field already lists
exactly which players appeared per team in that match, which IS that
match's lineup — this just reads data.json directly.

Usage:
    python scripts/diagnose_lineup_variation.py
"""
import json
import sys
from pathlib import Path

DATA_PATH = "data.json"


def main():
    if not Path(DATA_PATH).exists():
        print(f"! {DATA_PATH} not found — run scrape_lcs.py first.", file=sys.stderr)
        sys.exit(1)

    with open(DATA_PATH) as f:
        data = json.load(f)

    print(f"{'Region':<8} {'Team':<28} {'Lineups':>8} {'Games':>6}  Breakdown")
    print("-" * 100)

    total_teams = 0
    teams_with_variation = 0
    candidate_teams = []  # (region, team, lineup_count, games, breakdown) for teams worth exploring further

    for region_key, region_data in data.get("regions", {}).items():
        teams_seen = {}  # team_name -> {lineup_key: game_count}
        for m in region_data.get("past_matches", []):
            games_in_match = m.get("games", 2)
            for side in ("teamA", "teamB"):
                team_name = m[side]
                actual = (m.get("actual") or {}).get(team_name)
                if not actual:
                    continue
                lineup_key = tuple(sorted(actual.keys()))
                if len(lineup_key) < 4:  # a match with badly incomplete player data isn't a reliable lineup signal
                    continue
                teams_seen.setdefault(team_name, {})
                teams_seen[team_name][lineup_key] = teams_seen[team_name].get(lineup_key, 0) + games_in_match

        for team_name, lineups in teams_seen.items():
            total_teams += 1
            lineup_count = len(lineups)
            total_games = sum(lineups.values())
            breakdown = ", ".join(f"{g}g" for g in sorted(lineups.values(), reverse=True))
            print(f"{region_key:<8} {team_name:<28} {lineup_count:>8} {total_games:>6}  {breakdown}")

            if lineup_count > 1:
                teams_with_variation += 1
                # Worth exploring further only if there's a REAL sample
                # behind more than one lineup, not just one game where a
                # single sub came in — arbitrary but reasonable bar: at
                # least 2 distinct lineups each with 3+ games.
                sizable_lineups = [g for g in lineups.values() if g >= 3]
                if len(sizable_lineups) >= 2:
                    candidate_teams.append((region_key, team_name, lineup_count, total_games, breakdown))

    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {teams_with_variation}/{total_teams} teams fielded more than one lineup at all")
    print(f"{len(candidate_teams)}/{total_teams} teams have a REAL sample (2+ lineups, 3+ games each) worth exploring further")
    if candidate_teams:
        print("\nCandidate teams for a real cohesion analysis:")
        for region, team, count, games, breakdown in candidate_teams:
            print(f"  {region}/{team}: {count} lineups, {games} total games — {breakdown}")
    else:
        print("\nNo teams currently meet that bar. This doesn't mean cohesion effects don't exist —")
        print("it means THIS dataset (one split's worth of games) doesn't have enough roster variation")
        print("to measure them reliably right now. Worth revisiting once more of the split (or a full")
        print("career history equivalent across splits/seasons) is available.")


if __name__ == "__main__":
    main()